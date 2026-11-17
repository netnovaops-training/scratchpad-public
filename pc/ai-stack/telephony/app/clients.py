"""
Clients, and the people who call about them.

The distinction this file exists for: the person Miss Dee visits is often NOT
the person on the phone. A daughter in Portland books visits for her mother in
North Las Vegas. Her brother calls too, from a different number. Ruth herself
calls sometimes.

If we key everything on the caller's phone number — which is what the caller
memory alone does — those are three strangers, and every one of them gets asked
to spell the same street. So:

    CLIENT      the person being visited. Has a 5-digit code.
    CONTACT     a phone number that belongs to a client. Many per client.

A caller is recognised either because their number is already linked to a
client, or because they say the code. Saying the code once links their number
forever after.

Codes are 5 digits, no leading zero, and deliberately avoid anything that reads
badly aloud. They go on a card Dee leaves at the house and on the booking form.
"""

from __future__ import annotations

import json
import random
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .callers import normalise

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    code        TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    address     TEXT,
    notes       TEXT,
    likes       TEXT,
    created_at  TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS contacts (
    phone       TEXT PRIMARY KEY,
    code        TEXT NOT NULL,
    label       TEXT,
    linked_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_code ON contacts(code);

CREATE TABLE IF NOT EXISTS notes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    from_phone  TEXT,
    from_name   TEXT,
    note        TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    seen        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notes_code ON notes(code, created_at);
"""

# 5 digits, never starting with 0 or 1 (people drop leading zeros, and a
# leading 1 gets heard as a country code).
_FIRST = "23456789"
_REST = "0123456789"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def spoken_code(code: str) -> str:
    """'40213' -> '4 0 2 1 3' so the voice model reads digits, not a number."""
    return " ".join(str(code))


class ClientStore:
    def __init__(self, state_dir: str) -> None:
        d = Path(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / "clients.db"
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

    # -- codes ---------------------------------------------------------------

    def _new_code(self, conn: sqlite3.Connection) -> str:
        for _ in range(200):
            code = random.choice(_FIRST) + "".join(random.choice(_REST) for _ in range(4))
            if not conn.execute("SELECT 1 FROM clients WHERE code = ?", (code,)).fetchone():
                return code
        raise RuntimeError("could not allocate a client code")

    def create(
        self, name: str, *, address: str = "", notes: str = "", likes: str = ""
    ) -> dict[str, Any]:
        with self._lock, self._connect() as c:
            code = self._new_code(c)
            c.execute(
                "INSERT INTO clients (code, name, address, notes, likes, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (code, name.strip(), address.strip(), notes.strip(), likes.strip(), _now()),
            )
        return self.get(code) or {}

    # -- lookup --------------------------------------------------------------

    def get(self, code: str) -> dict[str, Any] | None:
        code = (code or "").strip()
        if not code:
            return None
        with self._connect() as c:
            row = c.execute(
                "SELECT * FROM clients WHERE code = ? AND active = 1", (code,)
            ).fetchone()
            if not row:
                return None
            contacts = c.execute(
                "SELECT phone, label FROM contacts WHERE code = ?", (code,)
            ).fetchall()
        out = dict(row)
        out["contacts"] = [dict(x) for x in contacts]
        return out

    def by_phone(self, phone: str) -> dict[str, Any] | None:
        p = normalise(phone)
        if not p:
            return None
        with self._connect() as c:
            row = c.execute("SELECT code FROM contacts WHERE phone = ?", (p,)).fetchone()
        return self.get(row["code"]) if row else None

    def find_by_name(self, name: str) -> list[dict[str, Any]]:
        """Fallback when someone doesn't know the code but gives a name."""
        n = (name or "").strip()
        if len(n) < 3:
            return []
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM clients WHERE active = 1 AND LOWER(name) LIKE ? LIMIT 5",
                (f"%{n.lower()}%",),
            ).fetchall()
        return [dict(r) for r in rows]

    # -- linking -------------------------------------------------------------

    def link_phone(self, code: str, phone: str, label: str = "") -> bool:
        """
        Remember that this number belongs to this client.

        Called the first time someone gives their code from a new phone, so
        they never have to give it again.
        """
        p = normalise(phone)
        if not p or not self.get(code):
            return False
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO contacts (phone, code, label, linked_at) VALUES (?,?,?,?) "
                "ON CONFLICT(phone) DO UPDATE SET code = excluded.code",
                (p, code, label.strip(), _now()),
            )
        return True

    def update(self, code: str, **fields: Any) -> None:
        allowed = {"name", "address", "notes", "likes"}
        sets, vals = [], []
        for k, v in fields.items():
            if k in allowed and v:
                sets.append(f"{k} = ?")
                vals.append(str(v).strip())
        if not sets:
            return
        vals.append(code)
        with self._lock, self._connect() as c:
            c.execute(f"UPDATE clients SET {', '.join(sets)} WHERE code = ?", vals)

    # -- notes ---------------------------------------------------------------

    def add_note(
        self, code: str, note: str, *, from_phone: str = "", from_name: str = ""
    ) -> bool:
        note = (note or "").strip()
        if not note or not self.get(code):
            return False
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO notes (code, from_phone, from_name, note, created_at) "
                "VALUES (?,?,?,?,?)",
                (code, normalise(from_phone), from_name.strip(), note, _now()),
            )
        return True

    def recent_notes(self, code: str, limit: int = 10) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM notes WHERE code = ? ORDER BY created_at DESC LIMIT ?",
                (code, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def unseen_notes(self, limit: int = 50) -> list[dict[str, Any]]:
        """What Dee hasn't read yet."""
        with self._connect() as c:
            rows = c.execute(
                "SELECT n.*, c.name AS client_name FROM notes n "
                "JOIN clients c ON c.code = n.code "
                "WHERE n.seen = 0 ORDER BY n.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_seen(self, note_id: int) -> None:
        with self._lock, self._connect() as c:
            c.execute("UPDATE notes SET seen = 1 WHERE id = ?", (note_id,))

    def all_clients(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM clients WHERE active = 1 ORDER BY name LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# what the assistant is told
# ---------------------------------------------------------------------------


def client_summary(client: dict[str, Any] | None) -> str:
    if not client:
        return ""
    bits = [f"This call is about {client['name']} (client number {client['code']})."]
    if client.get("address"):
        bits.append(f"They live at {client['address']}.")
    if client.get("likes"):
        bits.append(f"Things they enjoy: {client['likes']}")
    if client.get("notes"):
        bits.append(f"Practical notes: {client['notes']}")
    return " ".join(bits)
