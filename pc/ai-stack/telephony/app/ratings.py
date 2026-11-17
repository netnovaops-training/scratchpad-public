"""
How did the visit go?

A rating on a companionship service isn't a star score for a product — it's an
early warning. A 5 means keep doing what you're doing. A 2 means something
happened, and Dee needs to hear about it today, not at the end of the month.

So: anything at or below the alert threshold is flagged, and the reply says
Dee will call — because for this business a bad rating is a phone call, not a
dashboard entry.

Ratings arrive by text after a visit. Nobody is asked to rate anything during a
call; being asked to score a companion who just sat with your dying mother
would be grotesque.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from .callers import normalise

log = logging.getLogger("telephony.ratings")

SCHEMA = """
CREATE TABLE IF NOT EXISTS ratings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    code        TEXT NOT NULL,
    score       INTEGER NOT NULL,
    comment     TEXT,
    from_phone  TEXT,
    created_at  TEXT NOT NULL,
    flagged     INTEGER NOT NULL DEFAULT 0,
    handled     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ratings_code ON ratings(code, created_at);

CREATE TABLE IF NOT EXISTS rating_requests (
    phone       TEXT PRIMARY KEY,
    code        TEXT NOT NULL,
    asked_at    TEXT NOT NULL
);
"""

ALERT_AT_OR_BELOW = 3
REQUEST_VALID_HOURS = 48


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RatingStore:
    def __init__(self, state_dir: str) -> None:
        d = Path(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / "ratings.db"
        self._lock = threading.Lock()
        with self._connect() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
        finally:
            conn.close()

    # -- asking ---------------------------------------------------------------

    def request(self, code: str, phone: str) -> None:
        """Note that we've asked this number to rate a visit."""
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO rating_requests (phone, code, asked_at) VALUES (?,?,?) "
                "ON CONFLICT(phone) DO UPDATE SET code=excluded.code, asked_at=excluded.asked_at",
                (normalise(phone), code, _now()),
            )

    def awaiting(self, phone: str) -> str | None:
        """
        Are we expecting a rating from this number?

        Used so a bare "5" texted back counts, without a lone digit from a
        random number being treated as a score.
        """
        with self._connect() as c:
            row = c.execute(
                "SELECT code, asked_at FROM rating_requests WHERE phone = ?",
                (normalise(phone),),
            ).fetchone()
        if not row:
            return None
        try:
            asked = datetime.fromisoformat(row["asked_at"])
        except ValueError:
            return None
        if datetime.now(timezone.utc) - asked > timedelta(hours=REQUEST_VALID_HOURS):
            return None
        return row["code"]

    # -- recording ------------------------------------------------------------

    def record(
        self, code: str, score: int, comment: str = "", from_phone: str = ""
    ) -> dict[str, Any]:
        score = max(1, min(5, int(score)))
        flagged = 1 if score <= ALERT_AT_OR_BELOW else 0
        with self._lock, self._connect() as c:
            cur = c.execute(
                "INSERT INTO ratings (code, score, comment, from_phone, created_at, flagged) "
                "VALUES (?,?,?,?,?,?)",
                (code, score, comment.strip(), normalise(from_phone), _now(), flagged),
            )
            c.execute("DELETE FROM rating_requests WHERE phone = ?", (normalise(from_phone),))
            rid = cur.lastrowid
        if flagged:
            log.warning("LOW RATING %s/5 for client %s: %s", score, code, comment or "(no comment)")
        else:
            log.info("rating %s/5 for client %s", score, code)
        return {"id": rid, "score": score, "flagged": bool(flagged)}

    # -- reading --------------------------------------------------------------

    def needs_attention(self) -> list[dict[str, Any]]:
        """Low ratings Dee hasn't dealt with. This is the one she should see."""
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM ratings WHERE flagged = 1 AND handled = 0 "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def mark_handled(self, rating_id: int) -> None:
        with self._lock, self._connect() as c:
            c.execute("UPDATE ratings SET handled = 1 WHERE id = ?", (rating_id,))

    def for_client(self, code: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM ratings WHERE code = ? ORDER BY created_at DESC LIMIT ?",
                (code, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def summary(self) -> dict[str, Any]:
        with self._connect() as c:
            row = c.execute(
                "SELECT COUNT(*) n, AVG(score) avg, "
                "SUM(CASE WHEN flagged=1 AND handled=0 THEN 1 ELSE 0 END) open_flags "
                "FROM ratings"
            ).fetchone()
        return {
            "count": row["n"] or 0,
            "average": round(row["avg"], 2) if row["avg"] else None,
            "needs_attention": row["open_flags"] or 0,
        }
