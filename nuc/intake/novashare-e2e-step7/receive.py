"""receive.py - Novashare person-to-person E2E, Step 3: receive (inbox, Accept / Decline, pieces) (2026-11-24).

Plan: NOVASHARE-E2E-PLAN.md step 3 ("a Playwright test with two browsers round-trips the file byte for byte").
Design: NOVASHARE-E2E-DESIGN.md (delivery state machine, "pieces only after accept", "deleted when the last delivery
completes or declines"); threats: NOVASHARE-E2E-THREATS.md #8 (truncation / replay). Builds on Step 1 (devices.py)
and Step 2 (relay.py: transfer, wrap, piece; the delivery table is in relay._SCHEMA so one init makes everything).
Narrower than the design doc, like steps 1-2: no signatures, push, sweep or cancel yet (steps 4-6).

WHAT THE SERVER DOES AND DOES NOT DO
  It never decrypts. The receiving browser (receive_js.py) fetches ITS OWN wrap (relay.py envelope), unwraps the
  content key with its private keys, opens the manifest (name, type, caption) locally, then fetches the pieces one
  by one and decrypts them in order straight into a file on disk. The server only moves ciphertext and keeps the
  delivery state. It cannot scan the file for malware (it never has the plaintext); Windows Defender scans the saved
  file on the PC (README.md).

ROUTES (all: devices.guard = signed in, X-Auth-Level full, Sec-Fetch-Site same-origin, X-Novashare: 1; (D) = the
caller's own live device in X-Device-Id; nothing here has a drop-key-only, token or service-peer door)
  GET  /relay/inbox                               the inbox page (strict CSP, no inline script)
  GET  /relay/in/{inbox.js,inbox.css}             its script and style (SRI in the page)
  GET  /relay/sw.js                               the download service worker (scope /relay/; only /relay/dl/*)
  GET  /relay/api/inbox (D)                       deliveries to THIS device: sender, size, time, state (no names)
  POST /relay/api/transfers/{id}/accept (D)       pending -> accepted (again: 200 already)
  POST /relay/api/transfers/{id}/decline (D)      pending|accepted -> declined, this device's wrap deleted
  POST /relay/api/transfers/{id}/complete (D)     accepted -> downloaded ("mark received"), wrap deleted
  GET  /relay/api/transfers/{id}/pieces/{n} (D)   ciphertext piece n, only while THIS device's delivery is accepted
  GET  /relay/api/sent (D)                        the caller's sends with each receiving device's state

STATE MACHINE (delivery; every other edge is 409 wrong_state / not_accepted, a repeat of the same edge is 200)
  pending --accept--> accepted --complete--> downloaded;  pending|accepted --decline--> declined.
  A wrap row exists exactly while its delivery is pending or accepted, so a used or declined wrap is gone for good
  (replay of the envelope after complete = 404). When no live device has a pending or accepted delivery left, the
  transfer turns 'deleted' and its ciphertext file is removed, in the same transaction as the last transition
  (the file is unlinked after COMMIT; a reader that already has it open finishes its read, a new one gets 410).

SIX ANSWERS (backend-design:design, 2026-11-24)
  1 Context: HTTP routes in the intake FastAPI app (one uvicorn process); callers are the inbox page, which retries.
  2 Data: the delivery table (relay._SCHEMA). Invariants above; triggers forbid moving a delivery, going back from
    accepted to pending, or changing a finished one.
  3 Failures: 403 guard codes / not_your_device, 404 not_found (also someone else's transfer: existence is not
    confirmed), 409 wrong_state / not_accepted / not_ready, 410 gone (transfer deleted / file missing), 416
    bad_piece, 503 relay_unavailable.
  4 Authorization: every query carries the device id AND person of the caller; a delivery row is only ever read or
    changed for (transfer, caller's device, caller's person). The sender list is the caller's own sends only.
  5 Idempotence and concurrency: every transition is BEGIN IMMEDIATE + compare-and-set (UPDATE ... WHERE state=?)
    checked by rowcount; a repeat of a finished edge answers 200 {already: true}. Accept twice from two tabs: one
    moves it, the other sees accepted. Pieces are read with pread on a fd opened after the state check.
  6 Observability: one say() line per transition / refusal: transfer id cut to 8, device id to 6. No names.

STEP 4 (2026-11-24, push.py): the inbox page gained the Notifications box (PUSH_TAG / PUSH_SECTION) and the service
worker gained push and notificationclick (receive_js.SW). Routes and rules here are unchanged.

STEP 6 (2026-11-24, sweep.py): the inbox lists a send once it was ever ready (ready_at set), so one cancelled or
abandoned while still uploading never shows up; a cancelled send whose delivery was still open shows "cancelled"
(the sender withdrew it), an expired one "gone". Accept / decline / complete each write one content-free audit line.
Piece reads are counted in relay._ACTIVE (the sweep does not expire a transfer mid-read). OUTCOME: test_sweep.py.

OUTCOME: test_receive.py (server rules, in the patched image) and test_receive_browser.py (two Chrome profiles, two
people: 50 MB and 2 GB round-trip byte for byte, tamper -> refused and partial removed, decline flow).
"""
from __future__ import annotations

import errno
import html
import os
import sqlite3
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

import devices
import relay
from devices import Refuse, _answer, _json, guard

router = APIRouter()

LIVE = ("pending", "accepted")
INBOX_MAX = 50


def _say(msg: str) -> None:
    devices._say(msg)


def _plain(size_ct: int, pieces: int) -> int:
    return size_ct - 16 * pieces


def _gone() -> Refuse:
    return Refuse(410, "gone", "This send is no longer on the server.")


def _mine(c: sqlite3.Connection, person: str, did: str, tid: str) -> sqlite3.Row:
    """THIS device's delivery joined with its transfer, or 404 (someone else's id answers like a missing one)."""
    if not relay.ID_RE.match(tid or ""):
        raise Refuse(404, "not_found", "No such transfer.")
    row = c.execute("SELECT d.state AS dstate, t.state AS tstate, t.size_ct, t.pieces FROM delivery d JOIN transfer t"
                    " ON t.id=d.transfer_id WHERE d.transfer_id=? AND d.device_id=? AND d.person=?",
                    (tid, did, person)).fetchone()
    if row is None:
        raise Refuse(404, "not_found", "No such transfer.")
    return row


# ------------------------------------------------------------------ transitions

# action -> (states it may start from, target state)
_EDGES = {"accept": (("pending",), "accepted"), "decline": (("pending", "accepted"), "declined"),
          "complete": (("accepted",), "downloaded")}


def _finish_if_done(c: sqlite3.Connection, tid: str, now: int) -> bool:
    """Inside the caller's transaction: no live device still pending/accepted -> transfer 'deleted', every wrap and
    piece row gone, deliveries of removed devices marked declined. True if the ciphertext file must be removed."""
    left = c.execute("SELECT count(*) FROM delivery d JOIN device v ON v.id=d.device_id WHERE d.transfer_id=?"
                     " AND d.state IN ('pending','accepted') AND v.revoked IS NULL", (tid,)).fetchone()[0]
    if left:
        return False
    c.execute("UPDATE delivery SET state='declined', updated=? WHERE transfer_id=? AND state IN ('pending','accepted')",
              (now, tid))
    c.execute("DELETE FROM wrap WHERE transfer_id=?", (tid,))
    c.execute("DELETE FROM piece WHERE transfer_id=?", (tid,))
    return c.execute("UPDATE transfer SET state='deleted', last_activity=? WHERE id=? AND state='ready'",
                     (now, tid)).rowcount == 1


def do_move(person: str, did: str | None, tid: str, action: str) -> dict:
    start, target = _EDGES[action]
    c = relay._db()
    drop = False
    try:
        c.execute("BEGIN IMMEDIATE")      # check and change in one writer: two tabs cannot both move it
        me = relay._my_device(c, person, did)
        row = _mine(c, person, me["id"], tid)
        d, t = row["dstate"], row["tstate"]
        if d == target:
            c.execute("ROLLBACK")
            return {"id": tid, "state": d, "already": True}
        if d not in start:
            c.execute("ROLLBACK")
            code = "not_accepted" if action == "complete" and d == "pending" else "wrong_state"
            _say(f"relay refused {action} t={tid[:8]} d={me['id'][:6]} code={code} from={d}")
            raise Refuse(409, code, {"accept": "This was already saved or declined.",
                                     "decline": "This was already saved.",
                                     "complete": "Accept it first." if d == "pending" else "This was declined."}[action])
        if t == "uploading" and action != "decline":
            c.execute("ROLLBACK")
            raise Refuse(409, "not_ready", "This send has not finished uploading.")
        if t not in ("uploading", "ready"):
            c.execute("ROLLBACK")
            raise _gone()
        now = int(time.time())
        n = c.execute("UPDATE delivery SET state=?, updated=? WHERE transfer_id=? AND device_id=? AND person=? AND state=?",
                      (target, now, tid, me["id"], person, d)).rowcount
        if n != 1:                        # cannot happen under BEGIN IMMEDIATE; never act on a lost race
            c.execute("ROLLBACK")
            raise Refuse(409, "wrong_state", "This changed at the same moment. Reload.")
        if target in ("declined", "downloaded"):
            c.execute("DELETE FROM wrap WHERE transfer_id=? AND device_id=?", (tid, me["id"]))
            if t == "ready":
                drop = _finish_if_done(c, tid, now)
        c.execute("COMMIT")
        _say(f"relay {target} t={tid[:8]} d={me['id'][:6]}" + (" (transfer finished, ciphertext removed)" if drop else ""))
        relay._audit(target, tid=tid, dev=me["id"], now=now)
        if drop:
            relay._audit("finished", tid=tid, size=row["size_ct"], now=now)
        return {"id": tid, "state": target}
    except sqlite3.Error as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        drop = False
        _say(f"ERROR relay {action}: {type(e).__name__}")
        raise Refuse(503, "relay_unavailable", "Receiving is not available right now. Try again soon.") from None
    finally:
        if c.in_transaction:
            c.execute("ROLLBACK")
            drop = False
        c.close()
        if drop:
            relay._drop_files(relay.data_path(tid))


# ------------------------------------------------------------------ reads

@relay._db_errors
def do_inbox(person: str, did: str | None) -> dict:
    c = relay._db()
    try:
        me = relay._my_device(c, person, did)
        rows = c.execute(
            "SELECT t.id, t.sender_person, t.size_ct, t.pieces, t.created, t.expires_at, t.state AS tstate,"
            " d.state AS dstate, d.updated, v.label AS slabel, v.id AS sdev FROM delivery d JOIN transfer t ON t.id=d.transfer_id"
            " LEFT JOIN device v ON v.id=t.sender_device WHERE d.device_id=? AND d.person=? AND t.ready_at IS NOT NULL"
            " ORDER BY t.created DESC, t.id LIMIT ?", (me["id"], person, INBOX_MAX)).fetchall()
        names = devices.portal.NAMES
        items = []
        for r in rows:
            open_ = r["dstate"] in LIVE and r["tstate"] == "ready"
            shown = r["dstate"] if r["dstate"] not in LIVE or open_ else (
                "cancelled" if r["tstate"] == "cancelled" else "gone")
            items.append({"id": r["id"], "sender": {"person": r["sender_person"], "device_id": r["sdev"],
                                                    "name": names.get(r["sender_person"], r["sender_person"].title()),
                                                    "label": r["slabel"] or "a removed device"},
                          "size": _plain(r["size_ct"], r["pieces"]), "size_ct": r["size_ct"], "pieces": r["pieces"],
                          "created": r["created"], "expires_at": r["expires_at"], "updated": r["updated"],
                          "state": shown, "open": open_})
        return {"device_id": me["id"], "items": items}
    finally:
        c.close()


@relay._db_errors
def do_sent(person: str, did: str | None) -> dict:
    c = relay._db()
    try:
        relay._my_device(c, person, did)
        ts = c.execute("SELECT id, size_ct, pieces, created, state FROM transfer WHERE sender_person=?"
                       " ORDER BY created DESC, id LIMIT ?", (person, INBOX_MAX)).fetchall()
        names = devices.portal.NAMES
        by = {t["id"]: [] for t in ts}
        if by:                            # one query for every listed send's deliveries (audit: was one per send)
            for d in c.execute("SELECT d.transfer_id, d.device_id, d.person, d.state, d.updated, v.label FROM delivery d"
                               " LEFT JOIN device v ON v.id=d.device_id WHERE d.transfer_id IN (%s)"
                               " ORDER BY d.person, v.label" % ",".join("?" * len(by)), list(by)):
                by[d["transfer_id"]].append({"device_id": d["device_id"], "person": d["person"],
                                             "name": names.get(d["person"], d["person"].title()),
                                             "label": d["label"] or "a removed device", "state": d["state"],
                                             "updated": d["updated"]})
        return {"items": [{"id": t["id"], "size": _plain(t["size_ct"], t["pieces"]), "created": t["created"],
                           "state": t["state"], "deliveries": by[t["id"]]} for t in ts]}
    finally:
        c.close()


@relay._db_errors
def _piece_check(person: str, did: str | None, tid: str, n: int) -> tuple[int, int]:
    """(offset, length) of piece n if THIS device's delivery is accepted and the transfer is ready; else Refuse."""
    c = relay._db()
    try:
        me = relay._my_device(c, person, did)
        row = _mine(c, person, me["id"], tid)
        if row["dstate"] == "pending":
            raise Refuse(409, "not_accepted", "Accept it first.")
        if row["dstate"] != "accepted" or row["tstate"] != "ready":
            _say(f"relay refused piece-read t={tid[:8]} d={me['id'][:6]} code=gone state={row['dstate']}")
            raise _gone()
        if not 0 <= n < row["pieces"]:
            raise Refuse(416, "bad_piece", "No such piece.")
        return n * relay.CT_PIECE, relay.piece_len(row["size_ct"], row["pieces"], n)
    finally:
        c.close()


def _read(tid: str, off: int, ln: int) -> bytes:
    try:
        fd = os.open(relay.data_path(tid), os.O_RDONLY)
    except FileNotFoundError:
        raise _gone() from None
    try:
        out = bytearray()
        while len(out) < ln:
            b = os.pread(fd, min(1048576, ln - len(out)), off + len(out))
            if not b:
                raise OSError(errno.EIO, "short read")
            out += b
        return bytes(out)
    finally:
        os.close(fd)


# ------------------------------------------------------------------ HTTP routes

def _dev(request: Request) -> str | None:
    return request.headers.get("x-device-id")


@router.get("/relay/api/inbox")
async def inbox_api(request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_inbox, person, _dev(request)))
    except Refuse as e:
        return _answer(e)


@router.get("/relay/api/sent")
async def sent_api(request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_sent, person, _dev(request)))
    except Refuse as e:
        return _answer(e)


async def _move(tid: str, request: Request, action: str):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_move, person, _dev(request), tid, action))
    except Refuse as e:
        return _answer(e)


@router.post("/relay/api/transfers/{tid}/accept")
async def accept(tid: str, request: Request):
    return await _move(tid, request, "accept")


@router.post("/relay/api/transfers/{tid}/decline")
async def decline(tid: str, request: Request):
    return await _move(tid, request, "decline")


@router.post("/relay/api/transfers/{tid}/complete")
async def complete(tid: str, request: Request):
    return await _move(tid, request, "complete")


@router.get("/relay/api/transfers/{tid}/pieces/{n}")
async def get_piece(tid: str, n: int, request: Request):
    try:
        person = guard(request)
        try:
            # Step 7 (audit Low 24): the access check runs FIRST. It used to run inside using(), so any member could keep
            # someone else's transfer marked "in use" (and the sweep from expiring it) by asking for its pieces.
            await run_in_threadpool(_piece_check, person, _dev(request), tid, n)
            with relay.using(tid):        # Step 6: the sweep sees this read and does not expire the send under it
                # checked again while held: the sweep may have expired it between the first check and using()
                off, ln = await run_in_threadpool(_piece_check, person, _dev(request), tid, n)
                data = await run_in_threadpool(_read, tid, off, ln)
        except OSError as e:
            _say(f"ERROR relay piece-read t={tid[:8]} errno={e.errno}")
            raise Refuse(503, "relay_unavailable", "The piece could not be read. Try again.") from None
        return Response(data, media_type="application/octet-stream",
                        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
    except Refuse as e:
        return _answer(e)


# ------------------------------------------------------------------ the inbox page, its files, the service worker

import receive_js  # noqa: E402  (page script, style and worker in a .py so SWAP-PATCH.sh installs them)

_JS = receive_js.JS.encode()
_CSS = receive_js.CSS.encode()
_SW = receive_js.SW.encode()

# Step 4 (push.py, 2026-11-24): set by push.py at import (script tag with SRI, the "Notifications" box). Empty = Step 3.
PUSH_TAG = ""
PUSH_SECTION = ""


# The Devices CSP plus frame-src 'self': the download path opens /relay/dl/<token> (answered by the worker, never the
# network) in a hidden same-origin frame. Nothing else changes; frame-ancestors 'none' still forbids framing US.
PAGE_HEADERS = {**devices.PAGE_HEADERS, "Content-Security-Policy": devices.CSP + "; frame-src 'self'"}


def _page(name: str) -> str:
    t = html.escape(devices.themes.current().get("name", "Novashare"))
    v = devices.vendor_noble_pq
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=robots content="noindex,nofollow">
<meta name=referrer content=no-referrer><title>Inbox - {t}</title>
<link rel=stylesheet href="/devices/static/devices.css" integrity="{devices._sri(devices.CSS)}">
<link rel=stylesheet href="/relay/in/inbox.css" integrity="{devices._sri(_CSS)}">
<script type=module src="/devices/static/{v.NAME}" integrity="{v.SRI}"></script>
<script type=module src="/relay/in/inbox.js" integrity="{devices._sri(_JS)}"></script>
{PUSH_TAG}
</head><body><main>
<h1>Inbox</h1>
<p class=dim>{t} for {html.escape(name)}. Names and notes are unlocked on this device only; the server never sees
them. Accept saves the file straight to disk as it is unlocked.</p>
<p id=support class=dim role=status>Checking this device...</p>
<div id=alerts role=alert></div>
<section aria-labelledby=h-save><h2 id=h-save>Save to</h2>
<div id=saveto class="card saveto"><p class=dim>Loading...</p></div></section>
<section aria-labelledby=h-in><h2 id=h-in>Waiting for you</h2>
<p id=count class=dim role=status aria-live=polite></p>
<ul id=inbox class=plain></ul></section>
<section aria-labelledby=h-done><h2 id=h-done>Earlier</h2><ul id=done class=plain></ul></section>
{PUSH_SECTION}
<p><a href="/relay/send">Send something</a></p>
</main></body></html>"""


@router.get("/relay/inbox", response_class=HTMLResponse)
async def inbox_page(request: Request):
    try:
        person = guard(request, api=False)
    except Refuse as e:
        return HTMLResponse(f"<!doctype html><title>Inbox</title><p>{e.words}</p>", status_code=e.status,
                            headers=devices.PAGE_HEADERS)
    return HTMLResponse(_page(devices.portal.NAMES.get(person, person.title())), headers=PAGE_HEADERS)


@router.get("/relay/in/{name}")
async def inbox_static(name: str, request: Request):
    try:
        guard(request, api=False)
    except Refuse as e:
        return Response(e.code, status_code=e.status, media_type="text/plain")
    hdr = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
    if name == "inbox.js":
        return Response(_JS, media_type="text/javascript", headers=hdr)
    if name == "inbox.css":
        return Response(_CSS, media_type="text/css", headers=hdr)
    return Response("not found", status_code=404, media_type="text/plain")


@router.get("/relay/sw.js")
async def service_worker(request: Request):
    """The download worker. Service workers cannot carry SRI, so it is tiny, has no imports and only ever answers
    /relay/dl/<token> from a stream the inbox page handed it (never the network)."""
    try:
        guard(request, api=False)
    except Refuse as e:
        return Response(e.code, status_code=e.status, media_type="text/plain")
    return Response(_SW, media_type="text/javascript",
                    headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": devices.CSP, "Service-Worker-Allowed": "/relay/"})
