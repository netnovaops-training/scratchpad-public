"""Push every closed ticket to a listener on your own computer.

WHY THIS EXISTS ALONGSIDE EMAIL
-------------------------------
Email already sends you the write-up. This sends the same thing as JSON, with
the photos attached, to a small server you run on your desktop -- so the record
lands on your own disk in a form you can grep, script against, or feed into
anything else, without going through a mail provider at all.

Mail is for reading. This is for keeping.

SECURITY
--------
The Pi POSTs to your desktop over the LAN. That link is authenticated with an
HMAC signature over the exact request body, using a shared secret:

    X-TechHelp-Signature: sha256=<hex>

The listener recomputes it and refuses anything that does not match. Without
this, any device on your network -- or anything that finds its way onto it --
could post fabricated tickets into your archive, and you would have no way to
tell. The comparison uses compare_digest, so it cannot be attacked by timing.

The secret is NOT a password you type. Generate it once:

    openssl rand -hex 32

and put the same value in the Pi's .env as MOM_WEBHOOK_SECRET and in the
listener's environment.

FAILURE IS NOT AN ERROR
-----------------------
Your desktop hibernates. It will be asleep when most tickets close, and that is
entirely normal, so a failed delivery is queued and retried by the nightly job
rather than raised. A ticket is never held up waiting for this: the SQLite
record on the Pi is the source of truth and always was.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Optional

import httpx

from . import core

URL = os.getenv("MOM_WEBHOOK_URL", "").strip()
SECRET = os.getenv("MOM_WEBHOOK_SECRET", "").strip()
ENABLED = bool(URL)
TIMEOUT = int(os.getenv("MOM_WEBHOOK_TIMEOUT", "30"))

# Photos travel inline as base64. Fine over a LAN; capped so one enormous
# picture cannot turn a webhook into a memory problem on either end.
MAX_PHOTO_MB = int(os.getenv("MOM_WEBHOOK_MAX_PHOTO_MB", "12"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_queue (
    thread_id  INTEGER PRIMARY KEY,
    payload    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'queued',   -- queued | sent | failed
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at REAL NOT NULL
);
"""


def init() -> None:
    with core.tx() as c:
        c.executescript(SCHEMA)


def configured() -> dict:
    return {"enabled": ENABLED, "url": URL or "(not set)",
            "signed": bool(SECRET)}


def sign(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _photos_for(thread_id: int) -> list[dict]:
    """Base64 the pictures she sent, newest first, within the size budget."""
    out, spent = [], 0
    budget = MAX_PHOTO_MB * 1024 * 1024
    rows = core.conn().execute(
        "SELECT audio_path FROM messages WHERE thread_id=? AND audio_path!=''",
        (thread_id,)).fetchall()
    for r in rows:
        p = Path(r["audio_path"])
        if p.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".heic",
                                    ".heif", ".gif", ".bmp"}:
            continue
        try:
            raw = p.read_bytes()
        except Exception:
            continue
        if spent + len(raw) > budget:
            out.append({"name": p.name, "skipped": "size budget reached"})
            continue
        spent += len(raw)
        out.append({"name": p.name, "bytes": len(raw),
                    "b64": base64.b64encode(raw).decode()})
    return out


def build(thread_id: int) -> dict:
    th = core.get_thread(thread_id)
    if not th:
        return {}
    msgs = core.list_messages(thread_id)
    return {
        "version": 1,
        "ticket_no": th["ticket_no"],
        "status": th["status"],
        "category": th["category"],
        "title": th["title"],
        "username": th["username"],
        "summary": th["summary"],
        "root_cause": th["resolution"],
        "steps": core.jload(th["steps"] or "[]", []),
        "opened_at": th["created_at"],
        "closed_at": th["closed_at"],
        "opened_local": core.local_time(th["created_at"], th["username"]),
        "messages": [
            {"role": m["role"], "at": m["created_at"], "text": m["content"],
             "flags": core.jload(m["flags"] or "[]", []), "model": m["model"]}
            for m in msgs if m["role"] in ("user", "assistant")
        ],
        "models_used": [
            dict(r) for r in core.conn().execute(
                "SELECT role, model, ms, ok FROM routing WHERE thread_id=? ORDER BY ts",
                (thread_id,)).fetchall()
        ],
        "photos": _photos_for(thread_id),
        "sent_at": time.time(),
    }


def queue(thread_id: int) -> None:
    if not ENABLED:
        return
    try:
        init()
        payload = build(thread_id)
        if not payload:
            return
        with core.tx() as c:
            c.execute("INSERT INTO webhook_queue(thread_id, payload, status, updated_at) "
                      "VALUES (?,?, 'queued', ?) ON CONFLICT(thread_id) DO UPDATE SET "
                      "payload=excluded.payload, status='queued', updated_at=excluded.updated_at",
                      (thread_id, json.dumps(payload), time.time()))
    except Exception as e:  # noqa: BLE001
        core.log("warn", "webhook", f"queue failed for {thread_id}: {e}")


def _post(payload: str) -> tuple[bool, str]:
    body = payload.encode()
    headers = {"Content-Type": "application/json",
               "User-Agent": "TechHelp/1 (Raspberry Pi)"}
    if SECRET:
        headers["X-TechHelp-Signature"] = sign(body)
    try:
        r = httpx.post(URL, content=body, headers=headers, timeout=TIMEOUT)
        if r.status_code >= 400:
            return False, f"HTTP {r.status_code}: {r.text[:200]}"
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:300]


def flush(limit: int = 20, wake: bool = False) -> dict:
    """Send whatever is waiting. Called on close and again by the nightly job."""
    out = {"sent": 0, "failed": 0, "skipped": 0}
    if not ENABLED:
        out["skipped"] = 1
        return out
    init()
    rows = core.conn().execute(
        "SELECT * FROM webhook_queue WHERE status='queued' AND attempts < 10 "
        "ORDER BY updated_at LIMIT ?", (limit,)).fetchall()
    if not rows:
        return out

    # The desktop is usually asleep. The nightly job has already woken it, so
    # only wake here when explicitly asked -- a ticket closing must never sit
    # waiting four minutes for a machine to boot.
    if wake and not core.backend_status()["ready"] and core.AUTO_WAKE:
        core.ensure_awake()

    for r in rows:
        ok, err = _post(r["payload"])
        now = time.time()
        if ok:
            with core.tx() as c:
                c.execute("UPDATE webhook_queue SET status='sent', last_error='', "
                          "updated_at=? WHERE thread_id=?", (now, r["thread_id"]))
            out["sent"] += 1
        else:
            att = r["attempts"] + 1
            with core.tx() as c:
                c.execute("UPDATE webhook_queue SET attempts=?, status=?, "
                          "last_error=?, updated_at=? WHERE thread_id=?",
                          (att, "failed" if att >= 10 else "queued", err, now,
                           r["thread_id"]))
            out["failed"] += 1
    if out["sent"] or out["failed"]:
        core.log("info", "webhook", f"{out['sent']} sent, {out['failed']} failed")
    return out


def status() -> dict:
    try:
        init()
        rows = core.conn().execute(
            "SELECT status, COUNT(*) n FROM webhook_queue GROUP BY status").fetchall()
        d = {r["status"]: r["n"] for r in rows}
    except Exception:
        d = {}
    d.update(configured())
    return d


def test() -> tuple[bool, str]:
    """Send a harmless ping so you can prove the link works before relying on it."""
    if not ENABLED:
        return False, "MOM_WEBHOOK_URL is not set"
    ok, err = _post(json.dumps({"version": 1, "ticket_no": "TEST",
                                "title": "Connection test", "status": "test",
                                "messages": [], "photos": [],
                                "sent_at": time.time()}))
    return ok, err or "delivered"
