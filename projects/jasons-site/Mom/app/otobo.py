"""Mirror tickets into OTOBO so you get a real helpdesk UI to manage.
Replaces peppermint.py -- Peppermint is gone from this stack.

WHY THIS IS A MIRROR AND NOT A MIGRATION
-----------------------------------------
Same reason as before: tickets.py's SQLite stays authoritative. The ticket is
opened by Python before the model is asked anything, closed by Python,
documented by Python, emailed by Python. If OTOBO's five containers are ever
down, nothing about how Mom works for you changes -- OTOBO gets a copy,
best-effort, through a retry queue, same shape as the old pepper_sync table
(renamed here to otobo_sync).

HOW AUTH WORKS HERE, DIFFERENT FROM PEPPERMINT
------------------------------------------------
Peppermint had one unauthenticated "public create" route. OTOBO's REST layer
(GenericInterface) has no such thing out of the box -- every operation,
including TicketCreate, needs a real agent session. So unlike peppermint.py,
_create() logs in too, not just set_status(). One session, cached the same
way, used for everything.

The webservice itself ("MamaREST") is not something this module can create --
it has to be imported once via the OTOBO console (see OTOBO.md and the note
in .env), same one-time-manual-step shape as Peppermint's admin password was.

UNTESTED AGAINST A LIVE OTOBO YET
------------------------------------
This was written against OTOBO's shipped example webservice
(development/webservices/GenericTicketConnectorREST.yml) and its documented
route map, not verified against a running instance the way peppermint.py was
live-tested and fixed. Two things most likely to need adjusting after the
first real ticket:
  - CustomerUser: sent as the plain contact email. OTOBO may want an existing
    CustomerUser login rather than an arbitrary address, depending on how the
    CustomerUser backend is configured.
  - Queue: MOM_OTOBO_QUEUE must already exist in OTOBO (created by hand during
    setup) or ticket creation fails.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

import httpx

from . import core

BASE = os.getenv("MOM_OTOBO_URL", "").rstrip("/")  # .../Webservice/MamaREST
LOGIN = os.getenv("MOM_OTOBO_LOGIN", "mama-api")
PASSWORD = os.getenv("MOM_OTOBO_PASSWORD", "")
QUEUE = os.getenv("MOM_OTOBO_QUEUE", "Household")
ENABLED = bool(BASE and PASSWORD)
TIMEOUT = int(os.getenv("MOM_OTOBO_TIMEOUT", "20"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS otobo_sync (
    thread_id   INTEGER PRIMARY KEY,
    remote_id   TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'queued',  -- queued | synced | failed
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT NOT NULL DEFAULT '',
    payload     TEXT NOT NULL DEFAULT '{}',
    updated_at  REAL NOT NULL
);
"""


def init() -> None:
    with core.tx() as c:
        c.executescript(SCHEMA)


def configured() -> dict:
    return {"enabled": ENABLED, "url": BASE or "(not set)"}


def ping() -> tuple[bool, str]:
    """Is OTOBO up at all? Any HTTP response counts -- see peppermint.py's
    old ping() for why this cannot check further than that."""
    if not ENABLED:
        return False, "not configured"
    try:
        httpx.get(BASE, timeout=8)
        return True, "reachable"
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"[:200]


# ============================================================ agent session ===
#
# CACHED, NOT RE-LOGGED-IN EVERY CALL, same reasoning as peppermint.py: a
# session lasts hours, so logging in fresh for every ticket touched would be
# a needless round trip. Cached in memory only.
_session_id = ""
_session_until = 0.0


def _login() -> str:
    global _session_id, _session_until
    if _session_id and time.time() < _session_until:
        return _session_id
    if not PASSWORD:
        raise RuntimeError(
            "MOM_OTOBO_PASSWORD is not set, so nothing can log in to OTOBO.")
    r = httpx.post(f"{BASE}/Session",
                   json={"UserLogin": LOGIN, "Password": PASSWORD},
                   timeout=TIMEOUT)
    r.raise_for_status()
    data = r.json()
    sid = data.get("SessionID")
    if not sid:
        raise RuntimeError(f"OTOBO login returned no SessionID: {data}")
    _session_id = sid
    # Refreshed a few minutes early, not at the exact expiry -- same margin
    # peppermint.py used, kept here for the same reason.
    _session_until = time.time() + 7.5 * 3600
    return _session_id


def _authed(method: str, path: str, payload: Optional[dict] = None,
           retried: bool = False) -> tuple[bool, dict]:
    try:
        sid = _login()
    except Exception as e:  # noqa: BLE001
        return False, {"error": f"login failed: {e}"}
    body = dict(payload or {})
    body["SessionID"] = sid
    try:
        r = httpx.request(method, f"{BASE}{path}", json=body, timeout=TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return False, {"error": str(e)}
    if r.status_code == 401 and not retried:
        global _session_until
        _session_until = 0.0
        return _authed(method, path, payload, retried=True)
    if r.status_code >= 400:
        return False, {"error": f"HTTP {r.status_code}", "body": r.text[:300]}
    try:
        data = r.json()
    except Exception:
        return True, {"raw": r.text[:300]}
    # GenericInterface reports its own errors inside a 200 response body
    # rather than an HTTP error code -- an {"Error": {...}} key means the
    # call failed even though the transport succeeded.
    if isinstance(data, dict) and data.get("Error"):
        return False, data["Error"]
    return True, data


def sync_close(thread_id: int, escalated: bool) -> None:
    """Called once, when a ticket finishes -- closed or escalated either way.
    Same shape as peppermint.py's version: update the mirrored ticket rather
    than creating a second one, falling back to create only if this thread
    was never mirrored at all. Never raises."""
    try:
        ok, why = set_status(thread_id, done=not escalated)
        if not ok and "never mirrored" in why:
            queue(thread_id)
            flush(limit=1)
    except Exception as e:  # noqa: BLE001
        core.log("warn", "otobo", f"close sync failed for {thread_id}: {e}")


def set_status(thread_id: int, done: bool) -> tuple[bool, str]:
    """Mark the mirrored ticket closed or reopened in OTOBO."""
    if not ENABLED:
        return True, "not configured, nothing to update"
    row = core.conn().execute(
        "SELECT remote_id FROM otobo_sync WHERE thread_id=? AND remote_id != ''",
        (thread_id,)).fetchone()
    if not row:
        return False, "never mirrored to OTOBO, so nothing to update"
    state = "closed successful" if done else "open"
    # CAUGHT LIVE: the shipped GenericTicketConnectorREST webservice does not
    # route "PATCH /Ticket/:TicketID" at all -- TicketUpdate is "POST
    # /TicketUpdate" with the id IN the body, not the path. Checked directly
    # against the imported .yml's own RouteOperationMapping after a live 500
    # ("Error while determine Operation for request URI").
    ok, data = _authed("POST", "/TicketUpdate",
                       {"TicketID": row["remote_id"], "Ticket": {"State": state}})
    if not ok:
        core.log("warn", "otobo",
                 f"status update failed for thread {thread_id}: {data}")
        return False, str(data.get("Message") or data)[:200]
    return True, "updated"


# ================================================================== mirror ===

# A named function, not inlined, so a test can pin the mapping directly
# rather than needing a real safety-category ticket sitting in the fixture
# data -- the same reason peppermint.py's old _ticket_type() was its own
# function.
def _priority(category: str, status: str) -> str:
    if category == "safety":
        return "5 very high"
    return "4 high" if status == "escalated" else "3 normal"


def _payload(thread_id: int) -> dict:
    th = core.get_thread(thread_id)
    if not th:
        return {}
    steps = core.jload(th["steps"] or "[]", [])
    detail = [f"Opened by the Tech Help assistant for {th['username']}.", ""]
    if th["summary"]:
        detail += ["What happened:", th["summary"], ""]
    if th["resolution"]:
        detail += ["Cause:", th["resolution"], ""]
    if steps:
        detail += ["Steps that worked:"]
        detail += [f"  {i}. {s}" for i, s in enumerate(steps, 1)]
        detail += [""]
    detail += [f"Local record: {th['ticket_no']}  (authoritative copy is on the Pi)"]

    return {
        "Ticket": {
            "Title": f"{th['ticket_no']} — {th['title']}"[:180],
            "Queue": QUEUE,
            "State": "new",
            "Priority": _priority(th["category"], th["status"]),
            "CustomerUser": os.getenv("MOM_OTOBO_CONTACT", "person1@example.com"),
        },
        "Article": {
            "Subject": th["title"][:180] if th["title"] else th["ticket_no"],
            "Body": "\n".join(detail)[:8000],
            "MimeType": "text/plain",
            # CAUGHT LIVE: OTOBO's real REST create route rejects an Article
            # with no Charset -- "TicketCreate: Article->Charset is required!"
            # -- something no schema or stub server would have caught.
            "Charset": "utf-8",
        },
    }


def queue(thread_id: int) -> None:
    """Record the intent to sync. Never blocks, never raises."""
    if not ENABLED:
        return
    try:
        p = _payload(thread_id)
        if not p:
            return
        with core.tx() as c:
            c.execute(
                "INSERT INTO otobo_sync(thread_id, payload, status, updated_at) "
                "VALUES (?,?, 'queued', ?) ON CONFLICT(thread_id) DO UPDATE SET "
                "payload=excluded.payload, status='queued', updated_at=excluded.updated_at",
                (thread_id, json.dumps(p), time.time()))
    except Exception as e:  # noqa: BLE001
        core.log("warn", "otobo", f"queue failed for {thread_id}: {e}")


def flush(limit: int = 25) -> dict:
    """Push queued tickets. Called by the nightly job and from the dashboard.

    Failures increment a counter and stay queued. After 8 attempts a row is
    marked failed so a permanently broken payload cannot spin forever, but the
    row is kept so you can see it on the dashboard rather than losing it.
    """
    out = {"sent": 0, "failed": 0, "skipped": 0}
    if not ENABLED:
        out["skipped"] = 1
        return out
    init()
    rows = core.conn().execute(
        "SELECT * FROM otobo_sync WHERE status='queued' AND attempts < 8 "
        "ORDER BY updated_at LIMIT ?", (limit,)).fetchall()

    for r in rows:
        payload = core.jload(r["payload"], {})
        if not payload:
            out["skipped"] += 1
            continue
        ok, data = _authed("POST", "/Ticket", payload)
        now = time.time()
        if ok:
            rid = str(data.get("TicketID") or "")
            with core.tx() as c:
                c.execute("UPDATE otobo_sync SET status='synced', remote_id=?, "
                          "last_error='', updated_at=? WHERE thread_id=?",
                          (rid, now, r["thread_id"]))
            out["sent"] += 1
        else:
            att = r["attempts"] + 1
            status = "failed" if att >= 8 else "queued"
            with core.tx() as c:
                c.execute("UPDATE otobo_sync SET attempts=?, status=?, "
                          "last_error=?, updated_at=? WHERE thread_id=?",
                          (att, status, str(data)[:400], now, r["thread_id"]))
            out["failed"] += 1

    if out["sent"] or out["failed"]:
        core.log("info", "otobo",
                 f"sync: {out['sent']} sent, {out['failed']} failed")
    return out


def thread_for_remote(remote_id: str) -> Optional[int]:
    """The local thread_id for an OTOBO ticket id, or None.

    The reverse of set_status()'s own lookup -- used by /internal/otobo-status
    to find out which local ticket a status change coming FROM OTOBO belongs
    to. remote_id here is a string because that is what arrives over the
    wire (JSON has no distinct int-vs-string-id concept, and OTOBO's own
    TicketID has shown up as both in different responses in testing).
    """
    remote_id = str(remote_id or "").strip()
    if not remote_id:
        return None
    row = core.conn().execute(
        "SELECT thread_id FROM otobo_sync WHERE remote_id=?",
        (remote_id,)).fetchone()
    return row["thread_id"] if row else None


def status() -> dict:
    """Counts for the dashboard."""
    try:
        init()
        rows = core.conn().execute(
            "SELECT status, COUNT(*) n FROM otobo_sync GROUP BY status").fetchall()
        d = {r["status"]: r["n"] for r in rows}
    except Exception:
        d = {}
    d.update(configured())
    return d


def pending() -> list:
    try:
        return core.conn().execute(
            "SELECT p.*, t.ticket_no, t.title FROM otobo_sync p "
            "LEFT JOIN threads t ON t.id = p.thread_id "
            "WHERE p.status != 'synced' ORDER BY p.updated_at DESC LIMIT 50").fetchall()
    except Exception:
        return []
