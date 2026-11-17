"""
Caller memory.

The point of this file: someone who called last week shouldn't have to spell
their name and address again. When the phone rings, Twilio tells us the caller's
number before the assistant says a word — we look them up and hand the assistant
what it already knows about them.

One SQLite file. Keyed on the phone number in E.164.

What we deliberately DON'T store: anything medical. Not a diagnosis, not a
condition, not what the prescription is for. "Pharmacy: CVS on Craig Road" is an
errand. "Takes warfarin for AFib" is a medical record, and this business has no
business holding one.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger("bridge.callers")

SCHEMA = """
CREATE TABLE IF NOT EXISTS callers (
    phone          TEXT PRIMARY KEY,
    name           TEXT,
    pickup_address TEXT,
    notes          TEXT,
    preferences    TEXT,
    call_count     INTEGER NOT NULL DEFAULT 0,
    first_seen     TEXT NOT NULL,
    last_seen      TEXT NOT NULL,
    blocked        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS destinations (
    phone      TEXT NOT NULL,
    place      TEXT NOT NULL,
    times_used INTEGER NOT NULL DEFAULT 1,
    last_used  TEXT NOT NULL,
    PRIMARY KEY (phone, place)
);

CREATE TABLE IF NOT EXISTS call_log (
    call_id    TEXT PRIMARY KEY,
    phone      TEXT,
    started_at TEXT,
    ended_at   TEXT,
    reason     TEXT,
    summary    TEXT,
    transcript TEXT
);
CREATE INDEX IF NOT EXISTS idx_call_log_phone ON call_log(phone);
"""


def normalise(number: str | None) -> str:
    """
    E.164-ish. Twilio hands us '+17025551234'; humans and test tools hand us
    '555-0164'. Both must land on the same row or memory silently
    doesn't work.
    """
    if not number:
        return ""
    digits = re.sub(r"[^\d+]", "", str(number))
    if digits.startswith("+"):
        return digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return digits


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CallerStore:
    def __init__(self, state_dir: str) -> None:
        d = Path(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / "callers.db"
        self._lock = threading.Lock()
        with self._connect() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=3000")
            yield conn
        finally:
            conn.close()

    # -- read ----------------------------------------------------------------

    def get(self, phone: str) -> dict[str, Any] | None:
        p = normalise(phone)
        if not p:
            return None
        with self._connect() as c:
            row = c.execute("SELECT * FROM callers WHERE phone = ?", (p,)).fetchone()
            if not row:
                return None
            dests = c.execute(
                "SELECT place, times_used FROM destinations WHERE phone = ? "
                "ORDER BY times_used DESC, last_used DESC LIMIT 5",
                (p,),
            ).fetchall()
        out = dict(row)
        out["destinations"] = [d["place"] for d in dests]
        try:
            out["preferences"] = json.loads(out.get("preferences") or "{}")
        except json.JSONDecodeError:
            out["preferences"] = {}
        return out

    def is_blocked(self, phone: str) -> bool:
        rec = self.get(phone)
        return bool(rec and rec.get("blocked"))

    # -- write ---------------------------------------------------------------

    def touch(self, phone: str) -> dict[str, Any]:
        """Record that this number called. Creates the row if it's new."""
        p = normalise(phone)
        if not p:
            return {}
        now = _now()
        with self._lock, self._connect() as c:
            c.execute(
                """INSERT INTO callers (phone, call_count, first_seen, last_seen)
                   VALUES (?, 1, ?, ?)
                   ON CONFLICT(phone) DO UPDATE SET
                     call_count = call_count + 1,
                     last_seen  = excluded.last_seen""",
                (p, now, now),
            )
        return self.get(p) or {}

    def update(
        self,
        phone: str,
        *,
        name: str | None = None,
        pickup_address: str | None = None,
        notes: str | None = None,
        preferences: dict[str, Any] | None = None,
    ) -> None:
        """
        Fill in what we learned. Only overwrites fields we actually got --
        a call where the caller didn't mention their address must not wipe
        the address we already had.
        """
        p = normalise(phone)
        if not p:
            return
        sets, vals = [], []
        if name:
            sets.append("name = ?"); vals.append(name.strip())
        if pickup_address:
            sets.append("pickup_address = ?"); vals.append(pickup_address.strip())
        if notes:
            sets.append("notes = ?"); vals.append(notes.strip())
        if preferences is not None:
            sets.append("preferences = ?"); vals.append(json.dumps(preferences))
        if not sets:
            return
        sets.append("last_seen = ?"); vals.append(_now())
        vals.append(p)
        with self._lock, self._connect() as c:
            c.execute(f"UPDATE callers SET {', '.join(sets)} WHERE phone = ?", vals)

    def add_destination(self, phone: str, place: str) -> None:
        p, place = normalise(phone), (place or "").strip()
        if not p or not place:
            return
        with self._lock, self._connect() as c:
            c.execute(
                """INSERT INTO destinations (phone, place, times_used, last_used)
                   VALUES (?, ?, 1, ?)
                   ON CONFLICT(phone, place) DO UPDATE SET
                     times_used = times_used + 1,
                     last_used  = excluded.last_used""",
                (p, place, _now()),
            )

    def log_call(
        self,
        call_id: str,
        *,
        phone: str = "",
        started_at: str = "",
        ended_at: str = "",
        reason: str = "",
        summary: str = "",
        transcript: str = "",
    ) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                """INSERT OR REPLACE INTO call_log
                   (call_id, phone, started_at, ended_at, reason, summary, transcript)
                   VALUES (?,?,?,?,?,?,?)""",
                (call_id, normalise(phone), started_at, ended_at, reason,
                 summary, transcript),
            )

    def block(self, phone: str, blocked: bool = True) -> None:
        p = normalise(phone)
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO callers (phone, blocked, first_seen, last_seen) "
                "VALUES (?,?,?,?) ON CONFLICT(phone) DO UPDATE SET blocked = excluded.blocked",
                (p, 1 if blocked else 0, _now(), _now()),
            )

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM callers ORDER BY last_seen DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Turning a caller record into something an assistant can use
# ---------------------------------------------------------------------------


def summary_for(rec: dict[str, Any] | None) -> str:
    """
    The paragraph that goes into the system prompt so the assistant already
    knows who it's talking to before they say a word.
    """
    if not rec or not rec.get("call_count"):
        return "This is a new caller. You have not spoken before."

    name = (rec.get("name") or "").strip()
    dests = rec.get("destinations") or []
    bits = []
    if name:
        bits.append(f"Their name is {name}.")
    if rec.get("pickup_address"):
        bits.append(f"They are usually picked up at {rec['pickup_address']}.")
    if dests:
        bits.append("Places they've been before: " + ", ".join(dests) + ".")
    if rec.get("notes"):
        bits.append(f"Notes from previous calls: {rec['notes']}")
    bits.append(f"They have called {rec.get('call_count', 1)} time(s) before.")
    return " ".join(bits)


def greeting_for(rec: dict[str, Any] | None, business_name: str) -> str:
    """
    The first thing the caller hears. Three jobs, in this order:

    1. Confirm they reached the right place.
    2. Tell them what to do in an emergency. This comes SECOND, before any
       disclosure, because someone whose mother has just collapsed should not
       have to listen to an AI notice before being told to dial 911. It is also
       belt and braces: if the model ever fails to recognise an emergency, the
       caller has already been told what to do.
    3. Disclose that it's an assistant and that the call is written down.
       Nevada requires all-party consent to record a phone call and a violation
       is a felony -- see NEVADA-NOTES.md. No audio is recorded at all here, but
       the transcript still needs disclosing, and burying it later in the call
       would defeat the point.

    The caller CAN talk over this. That's deliberate: the 911 line is already
    out before anything else, so there's no reason to trap someone in a
    greeting they don't need.
    """
    emergency = (
        "If this is an emergency, or someone's life is in danger, "
        "please hang up and call 9 1 1. "
    )
    disclosure = (
        "I'm Dee's automated assistant, and this call is written down so we "
        "get your details right. "
    )

    if rec and rec.get("call_count") and (rec.get("name") or "").strip():
        first = rec["name"].split()[0]
        return (
            f"Thank you for calling {business_name}. "
            + emergency
            + f"Welcome back, {first}. "
            + disclosure
            + "How can I help you today?"
        )

    return (
        f"Thank you for calling {business_name}. "
        + emergency
        + disclosure
        + "How can I help you today?"
    )
