"""sweep.py - Novashare person-to-person E2E, Step 6: expiry, quotas, sender cancel, audit log (2026-11-24).

Plan: NOVASHARE-E2E-PLAN.md step 6 ("the sweep deletes expired relay items; quotas hold under concurrent sends").
Design: NOVASHARE-E2E-DESIGN.md "The sweep". Builds on Steps 1-5 (devices, relay, receive, push, pick; all live).
Closes the gaps Steps 2-3 left: a crash during create could leave an empty folder; unfinished uploads kept holding
their quota; there was no create rate limit (relay.py now has one, counted in the create transaction).

WHAT IT ADDS
  - The sweep, every 10 minutes, in a daemon thread of the intake app (started after the app's lifespan startup):
      1 expire   uploading|ready sends 7 days after create (expires_at), and uploading sends with no piece for
                 6 hours (abandoned) -> 'expired': key wraps and piece rows deleted, ciphertext removed. The quota
                 is released by the state change itself (the sums count uploading|ready only).
      2 finish   a ready send none of whose LIVE receiving devices still waits (all removed) -> 'deleted' (as if
                 the last one declined; receive._finish_if_done, the same code).
      3 lost     an uploading|ready row whose ciphertext file is missing -> 'deleted', logged at ERROR.
      4 purge    finished rows: 'deleted' and 'expired' 24 h after the last change, 'cancelled' 24 h after the cancel
                 and not before the original 7 days (so receivers see "cancelled" for a while). Rows go with their
                 deliveries, wraps and pieces (ON DELETE CASCADE).
      5 orphans  folders under the relay root named like a transfer id: no row and older than 1 h (a crash between
                 the folder and the create commit), or a finished row (a crash between commit and unlink) -> removed.
                 Rows in wrap / piece / delivery without a transfer -> deleted. Anything else in the root
                 is left alone and counted.
      6 house    rotated audit files older than 90 days removed; the run's numbers stored for the Devices page.
  - Sender cancel: POST /relay/api/transfers/{id}/cancel from any live device of the sender. uploading|ready ->
    'cancelled' in one transaction with deleting every key wrap and piece row; the ciphertext is removed right after
    the commit. Receivers' inbox shows "cancelled"; push.py's trigger push_on_cancel (same transaction) queues "A
    Novashare item was withdrawn" for their subscribed devices that still waited, if the send had been ready.
  - Audit log without content: audit.py (event, transfer id cut to 8, sizes, counts, device ids HMAC-hashed,
    timestamps; rotated; mode 600; read back only by Jason at GET /relay/api/audit).
  - Jason's "Relay" line on the Devices page: transfers live, bytes held, last sweep (devices.RELAY_LINE).

ROUTES (devices.guard: signed in, X-Auth-Level full, Sec-Fetch-Site same-origin, X-Novashare: 1)
  POST /relay/api/transfers/{id}/cancel (D)   200 {id, state:'cancelled', waiting}; again: 200 {already: true}
  GET  /relay/api/relay-stats                 portal.ADMIN only (anyone else 404): {live, bytes_held, last_sweep}
  GET  /relay/api/audit?limit=                portal.ADMIN only (anyone else 404): the newest audit lines

SIX ANSWERS (backend-design:design, 2026-11-24)
  1 Context: a background worker (a daemon thread in the intake uvicorn process, like push.py's) plus three HTTP
    routes. Chosen over a cron because: the sweep must see the in-process "piece read/write in flight" state
    (relay._ACTIVE / _BUSY) to not expire a send under a running download; it reuses the app's DB code and log; it
    starts and stops with the app (the lifespan), so a restart restarts it; no new container, cron entry or port.
    A cron would need docker exec into the container and would still not see the in-flight state.
  2 Data: no new transfer columns. relay_meta(k, v): the sweep lease and the last run's numbers. push_out's kind
    CHECK gains 'withdrawn' (push._migrate). Lifecycle: uploading -> ready -> deleted (all saved/declined) |
    cancelled | expired; deleted|expired purged at +24 h, cancelled at max(+24 h, created + 7 days).
    Invariants: a key wrap and the ciphertext exist only while the send is uploading|ready (cancel, expiry, finish
    and lost delete them; the file is removed after the commit, and anything a crash left behind is found by step 5
    the next run); quota sums count uploading|ready only; the sweep only ever removes RELAY/<22-char id>/data and
    then that empty folder, by a path rebuilt from a validated id (relay.data_path's regex + normpath wall), never
    follows a symlink, never removes a tree.
  3 Failures: sweep: the DB busy or locked -> that step stops, logged, next run continues (every step is
    re-derived from the DB and the folder, so nothing is half-done for long); a file that cannot be removed ->
    logged, retried next run; a crash or restart mid-run -> the committed state changes stand, the files are found
    by step 5 next run (tested). Cancel: 404 not_found (also someone else's send), 409 wrong_state (already finished
    or expired), 403 not_your_device, 503 relay_unavailable.
  4 Authorization: cancel = the sender person, from one of their own live devices (person + device predicate in
    the query; someone else's id answers like a missing one). Stats and audit = portal.ADMIN only, 404 for others
    (existence not confirmed). The sweep acts as the system: it takes no input from a request.
  5 Idempotence and concurrency: every transition is BEGIN IMMEDIATE + compare-and-set (UPDATE ... WHERE id=? AND
    state=? AND <condition>) checked by rowcount; file removal is idempotent (missing = done). A lease row in
    relay_meta keeps two sweeps (two threads, or a second process) from running together, but correctness does not
    depend on it: test_sweep.py runs two sweeps at once with the lease bypassed and gets the same end state. Cancel
    twice = 200 already. Cancel vs a piece write: the write's commit sees the state and answers 409, a write that
    opens the removed file answers 410. Cancel vs a download: the next piece read answers 410 gone.
  6 Observability: one say() line per run (counts, ms) and per cancel / lost file (ids cut to 8 / 6); the audit
    log; relay_meta 'last_sweep' for Jason's Devices line and its age ("late" over 30 minutes).

OUTCOME: test_sweep.py (server rules + concurrency + crash/restart, in the patched image) and
test_sweep_browser.py (two people in Chrome: send, cancel, inbox "cancelled", withdrawn push; 10 parallel creates
near the quota through the real server).
"""
from __future__ import annotations

import contextlib
import html
import json
import os
import sqlite3
import stat
import threading
import time
import uuid

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

import audit
import devices
import push
import receive
import relay
from devices import Refuse, _answer, _json, guard

router = APIRouter()

EVERY_S = int(os.environ.get("INTAKE_RELAY_SWEEP_S", "600"))
FIRST_S = int(os.environ.get("INTAKE_RELAY_SWEEP_FIRST_S", "60"))
ABANDON_S = 6 * 3600
KEEP_S = 24 * 3600
ORPHAN_S = 3600
LEASE_S = 300
LATE_S = 1800
LIVE = ("uploading", "ready")

_META = """
CREATE TABLE IF NOT EXISTS relay_meta(k TEXT PRIMARY KEY, v TEXT NOT NULL) WITHOUT ROWID;
"""
_READY: set[str] = set()
# test hook: called between a state change's COMMIT and its file removal (a crash there must be harmless)
_after_commit = None


def _say(msg: str) -> None:
    devices._say(msg)


def _db() -> sqlite3.Connection:
    """push._db() (devices + relay + push schema and triggers, so a cancel always queues its push) plus relay_meta."""
    c = push._db()
    key = str(devices.db_path())
    try:
        if key not in _READY:
            with devices._INIT_LOCK:
                if key not in _READY:
                    c.executescript(_META)
                    _READY.add(key)
        return c
    except sqlite3.Error as e:
        c.close()
        _say(f"ERROR relay sweep db: {type(e).__name__}")
        raise Refuse(503, "relay_unavailable", "Sending is not available right now. Try again soon.") from None


# ------------------------------------------------------------------ files (literal, validated paths only)

def remove_files(tid: str) -> bool:
    """Remove RELAY/<tid>/data and then the empty RELAY/<tid> folder. The path is rebuilt from the id by
    relay.data_path (22 base64url characters, normpath wall), so nothing from a request or the folder listing is
    ever used as a path. Never follows a symlink (a link is unlinked itself), never removes a tree: an unexpected
    entry in the folder leaves it in place and is logged. True = nothing of it is left."""
    try:
        p = relay.data_path(tid)
    except Refuse:
        return False
    d = p.parent
    try:
        st = os.lstat(d)
    except FileNotFoundError:
        return True
    if not stat.S_ISDIR(st.st_mode):                 # a link or a file where the folder should be: remove it only
        os.unlink(d)
        return True
    try:
        os.unlink(p)                                 # unlink never follows a link: a symlink named data goes itself
    except FileNotFoundError:
        pass
    try:
        os.rmdir(d)
    except FileNotFoundError:
        pass
    except OSError:
        _say(f"WARNING relay sweep: folder t={tid[:8]} has unexpected entries; left in place")
        return False
    return True


def _drop(tid: str) -> None:
    """After a COMMIT that ended a send: its files go now; a failure is logged and step 5 retries next run."""
    if _after_commit is not None:
        _after_commit(tid)
    try:
        remove_files(tid)
    except OSError as e:
        _say(f"ERROR relay sweep remove t={tid[:8]} errno={e.errno}")


# ------------------------------------------------------------------ the sweep

def _lease(c: sqlite3.Connection, token: str, now: int, force: bool) -> bool:
    c.execute("BEGIN IMMEDIATE")
    r = c.execute("SELECT v FROM relay_meta WHERE k='sweep_lease'").fetchone()
    if r is not None and not force:
        owner, _, until = r["v"].partition("|")
        if owner != token and until.isdigit() and int(until) > now:
            c.execute("ROLLBACK")
            return False
    c.execute("INSERT INTO relay_meta(k,v) VALUES('sweep_lease',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
              (f"{token}|{now + LEASE_S}",))
    c.execute("COMMIT")
    return True


def _end(c: sqlite3.Connection, tid: str, old: str, new: str, cond: str, args: tuple, now: int) -> bool:
    """One compare-and-set: old -> new where `cond` still holds, plus the wraps and pieces deleted. True = moved."""
    c.execute("BEGIN IMMEDIATE")
    n = c.execute(f"UPDATE transfer SET state=?, last_activity=? WHERE id=? AND state=? AND ({cond})",
                  (new, now, tid, old, *args)).rowcount
    if n != 1:                                       # changed since we looked (another sweep, a cancel, a piece)
        c.execute("ROLLBACK")
        return False
    c.execute("DELETE FROM wrap WHERE transfer_id=?", (tid,))
    c.execute("DELETE FROM piece WHERE transfer_id=?", (tid,))
    c.execute("COMMIT")
    return True


def _step(name: str, fn, out: dict) -> None:
    """Run one step; a DB or file error stops that step only (logged), the next run does it again."""
    try:
        fn()
    except (sqlite3.Error, OSError, Refuse) as e:
        out["errors"] += 1
        _say(f"ERROR relay sweep {name}: {type(e).__name__}")


def sweep_once(now: int | None = None, force: bool = False) -> dict:
    """One full run. `now` fakes the clock (tests); `force` bypasses the lease (tests: two sweeps at once).
    Returns the numbers; {"skipped": "lease"} if another sweep holds the lease."""
    t_start = time.monotonic()
    now = int(now if now is not None else time.time())
    out = {"ts": now, "expired": 0, "abandoned": 0, "finished": 0, "lost": 0, "purged": 0, "orphan_dirs": 0,
           "orphan_rows": 0, "busy": 0, "unknown": 0, "errors": 0}
    token = uuid.uuid4().hex
    c = _db()
    try:
        if not _lease(c, token, now, force):
            return {"skipped": "lease"}

        def expire():
            rows = c.execute("SELECT id, state, size_ct, expires_at, last_activity FROM transfer"
                             " WHERE state IN ('uploading','ready') AND (expires_at<=? OR"
                             " (state='uploading' AND last_activity<=?))", (now, now - ABANDON_S)).fetchall()
            for r in rows:
                if relay.in_use(r["id"]):
                    out["busy"] += 1                 # a piece read or write runs right now: next run
                    continue
                if _end(c, r["id"], r["state"], "expired", "expires_at<=? OR (state='uploading' AND last_activity<=?)",
                        (now, now - ABANDON_S), now):
                    why = "expired" if r["expires_at"] <= now else "abandoned"
                    out[why] += 1
                    _drop(r["id"])
                    relay._audit(why, tid=r["id"], size=r["size_ct"], now=now)

        def finish():
            rows = c.execute("SELECT id, size_ct FROM transfer t WHERE state='ready' AND NOT EXISTS(SELECT 1 FROM"
                             " delivery d JOIN device v ON v.id=d.device_id WHERE d.transfer_id=t.id"
                             " AND d.state IN ('pending','accepted') AND v.revoked IS NULL)").fetchall()
            for r in rows:
                c.execute("BEGIN IMMEDIATE")
                drop = receive._finish_if_done(c, r["id"], now)
                c.execute("COMMIT")
                if drop:
                    out["finished"] += 1
                    _drop(r["id"])
                    relay._audit("finished", tid=r["id"], size=r["size_ct"], now=now)

        def lost():
            for r in c.execute("SELECT id, state, size_ct FROM transfer WHERE state IN ('uploading','ready')").fetchall():
                if os.path.lexists(relay.data_path(r["id"])) or relay.in_use(r["id"]):
                    continue
                if _end(c, r["id"], r["state"], "deleted", "1", (), now):
                    out["lost"] += 1
                    _say(f"ERROR relay sweep: t={r['id'][:8]} was {r['state']} but its file is missing; marked deleted")
                    relay._audit("lost", tid=r["id"], size=r["size_ct"], now=now)
                    _drop(r["id"])

        def purge():
            cond = ("(state IN ('deleted','expired') AND last_activity<=?) OR"
                    " (state='cancelled' AND last_activity<=? AND expires_at<=?)")
            args = (now - KEEP_S, now - KEEP_S, now)
            c.execute("BEGIN IMMEDIATE")
            ids = [r[0] for r in c.execute(f"SELECT id FROM transfer WHERE {cond}", args)]
            c.execute(f"DELETE FROM transfer WHERE {cond}", args)
            c.execute("COMMIT")
            for tid in ids:
                out["purged"] += 1
                _drop(tid)                           # normally gone already; a leftover goes now
                relay._audit("purged", tid=tid, now=now)

        def orphans():
            root = relay.relay_dir()
            try:
                names = os.listdir(root)
            except FileNotFoundError:
                names = []
            for name in names:
                if not relay.ID_RE.match(name):
                    out["unknown"] += 1              # not ours to judge: left alone
                    continue
                r = c.execute("SELECT state FROM transfer WHERE id=?", (name,)).fetchone()
                if r is not None and r["state"] in LIVE:
                    continue
                if r is None:
                    try:
                        age = now - os.lstat(os.path.join(root, name)).st_mtime
                    except FileNotFoundError:
                        continue
                    if age < ORPHAN_S:               # maybe a create committing right now
                        continue
                # race review (soundcheck race-condition, 2026-11-24): with NO row, the re-check and the removal run
                # while holding the DB write lock. relay.do_create makes its file INSIDE its own BEGIN IMMEDIATE, so a
                # create of this very id (a retried send after a crash reuses its id) either committed before we look
                # again (row found: keep) or waits until the folder is gone (then makes a fresh one). A FINISHED row
                # never goes live again and blocks a create of its id (id_taken), so that folder goes without the lock
                # (audit 2026-11-24: an unlink of a big file must not stall every create and piece commit).
                locked = r is None
                if locked:
                    c.execute("BEGIN IMMEDIATE")
                try:
                    if locked and c.execute("SELECT 1 FROM transfer WHERE id=?", (name,)).fetchone() is not None:
                        continue
                    gone = remove_files(name)
                except OSError as e:                 # one odd entry never stops the rest of the scan
                    out["errors"] += 1
                    _say(f"ERROR relay sweep orphan t={name[:8]} errno={e.errno}")
                    continue
                finally:
                    if locked:
                        c.execute("ROLLBACK")        # nothing was written; this only releases the lock
                if gone:
                    out["orphan_dirs"] += 1
                    relay._audit("orphan_dir", tid=name, now=now)
            c.execute("BEGIN IMMEDIATE")
            n = 0
            for t in ("wrap", "piece", "delivery"):
                n += c.execute(f"DELETE FROM {t} WHERE transfer_id NOT IN (SELECT id FROM transfer)").rowcount
            c.execute("COMMIT")
            out["orphan_rows"] += n

        def house():
            audit.prune(now)

        for name, fn in (("expire", expire), ("finish", finish), ("lost", lost), ("purge", purge),
                         ("orphans", orphans), ("house", house)):
            if c.in_transaction:
                c.execute("ROLLBACK")
            _step(name, fn, out)
        if c.in_transaction:
            c.execute("ROLLBACK")
        out["ms"] = int((time.monotonic() - t_start) * 1000)
        acts = sum(out[k] for k in ("expired", "abandoned", "finished", "lost", "purged", "orphan_dirs", "orphan_rows"))
        try:
            c.execute("INSERT INTO relay_meta(k,v) VALUES('last_sweep',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                      (json.dumps(out, separators=(",", ":")),))
        except sqlite3.Error as e:
            _say(f"ERROR relay sweep meta: {type(e).__name__}")
        relay._audit("sweep", n=acts, now=now)
        _say("relay sweep " + " ".join(f"{k}={v}" for k, v in out.items() if k != "ts"))
        return out
    finally:
        try:
            if c.in_transaction:
                c.execute("ROLLBACK")
            c.execute("DELETE FROM relay_meta WHERE k='sweep_lease' AND v LIKE ?", (token + "|%",))
        except sqlite3.Error:
            pass                                     # the lease ends on its own after LEASE_S
        c.close()


# ------------------------------------------------------------------ sender cancel

def do_cancel(person: str, did: str | None, tid: str) -> dict:
    if not relay.ID_RE.match(tid or ""):
        raise Refuse(404, "not_found", "No such transfer.")
    c = _db()
    moved = False
    try:
        c.execute("BEGIN IMMEDIATE")
        me = relay._my_device(c, person, did)
        t = c.execute("SELECT state, size_ct, ready_at FROM transfer WHERE id=? AND sender_person=?",
                      (tid, person)).fetchone()
        if t is None:                                # someone else's send answers exactly like a missing one
            c.execute("ROLLBACK")
            raise Refuse(404, "not_found", "No such transfer.")
        if t["state"] == "cancelled":
            c.execute("ROLLBACK")
            return {"id": tid, "state": "cancelled", "already": True}
        if t["state"] not in LIVE:
            c.execute("ROLLBACK")
            raise Refuse(409, "wrong_state", "This send is already finished or expired; nothing is left to cancel.")
        now = int(time.time())
        waiting = c.execute("SELECT count(*) FROM delivery WHERE transfer_id=? AND state IN ('pending','accepted')",
                            (tid,)).fetchone()[0]
        n = c.execute("UPDATE transfer SET state='cancelled', last_activity=? WHERE id=? AND sender_person=? AND state=?",
                      (now, tid, person, t["state"])).rowcount   # push_on_cancel queues 'withdrawn' (if it was ready)
        if n != 1:
            c.execute("ROLLBACK")
            raise Refuse(409, "wrong_state", "This changed at the same moment. Reload.")
        c.execute("DELETE FROM wrap WHERE transfer_id=?", (tid,))
        c.execute("DELETE FROM piece WHERE transfer_id=?", (tid,))
        c.execute("COMMIT")
        moved = True
        _say(f"relay cancelled t={tid[:8]} d={me['id'][:6]} from={t['state']} waiting={waiting}")
        relay._audit("cancelled", tid=tid, size=t["size_ct"], n=waiting, dev=me["id"], now=now)
        return {"id": tid, "state": "cancelled", "waiting": waiting}
    except sqlite3.Error as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        _say(f"ERROR relay cancel: {type(e).__name__}")
        raise Refuse(503, "relay_unavailable", "Sending is not available right now. Try again soon.") from None
    finally:
        if c.in_transaction:
            c.execute("ROLLBACK")
        c.close()
        if moved:
            _drop(tid)
            push.WORKER.wake()


# ------------------------------------------------------------------ Jason's numbers

def stats() -> dict:
    c = _db()
    try:
        n, b = c.execute("SELECT count(*), coalesce(sum(size_ct),0) FROM transfer"
                         " WHERE state IN ('uploading','ready')").fetchone()
        r = c.execute("SELECT v FROM relay_meta WHERE k='last_sweep'").fetchone()
    finally:
        c.close()
    last = None
    if r is not None:
        try:
            last = json.loads(r["v"])
        except ValueError:
            last = None
    return {"live": n, "bytes_held": b, "last_sweep": last}


def _size(n: int) -> str:
    return f"{n / 1073741824:.2f} GB" if n >= 1073741824 else f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n} bytes"


def relay_line(person: str) -> str:
    """The Devices page line, for the admin only ("" for anyone else). Numbers only, HTML-escaped."""
    if person != devices.portal.ADMIN:
        return ""
    s = stats()
    last = s["last_sweep"]
    if last and isinstance(last.get("ts"), int):
        age = max(0, int(time.time()) - last["ts"])
        ago = f"{age // 60} min ago" if age >= 60 else "just now"
        did = sum(int(last.get(k, 0)) for k in ("expired", "abandoned", "finished", "lost", "purged", "orphan_dirs"))
        sw = f"last sweep {ago} ({did} removed{', LATE' if age > LATE_S else ''})"
    else:
        sw = "last sweep: not yet"
    text = (f"Relay: {s['live']} transfer{'' if s['live'] == 1 else 's'} live, {_size(s['bytes_held'])} held, {sw}.")
    return (f'<section aria-labelledby=h-relay><h2 id=h-relay>Relay</h2>'
            f'<p id=relay-line class=dim>{html.escape(text)}</p></section>\n')


devices.RELAY_LINE = relay_line


# ------------------------------------------------------------------ the worker

class _Worker:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="novashare-sweep", daemon=True)
        self._thread.start()
        _say(f"relay sweep on: every {EVERY_S} s")

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=30)
        self._thread = None

    def _loop(self) -> None:
        wait = FIRST_S
        while not self._stop.wait(wait):
            try:
                sweep_once()
            except Exception as e:  # noqa: BLE001  (the loop must survive anything; logged without detail)
                _say(f"ERROR relay sweep: {type(e).__name__}")
            wait = EVERY_S


WORKER = _Worker()


def wrap_lifespan(prev):
    """app.py's lifespan (with Step 4's push worker) plus the sweep worker."""
    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with prev(app) as state:
            WORKER.start()
            try:
                yield state
            finally:
                WORKER.stop()
    return lifespan


# ------------------------------------------------------------------ HTTP routes

@router.post("/relay/api/transfers/{tid}/cancel")
async def cancel(tid: str, request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_cancel, person, request.headers.get("x-device-id"), tid))
    except Refuse as e:
        return _answer(e)


def _admin(request: Request) -> str:
    person = guard(request)
    if person != devices.portal.ADMIN:
        raise Refuse(404, "not_found", "Not found.")
    return person


@router.get("/relay/api/relay-stats")
async def relay_stats(request: Request):
    try:
        _admin(request)
        return _json(await run_in_threadpool(stats))
    except Refuse as e:
        return _answer(e)


@router.get("/relay/api/audit")
async def audit_tail(request: Request, limit: int = 200):
    try:
        _admin(request)
        return _json({"events": await run_in_threadpool(audit.tail, max(1, min(limit, 1000)))})
    except Refuse as e:
        return _answer(e)
