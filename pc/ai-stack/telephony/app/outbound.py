"""
The texts that go out: confirmation, reminder, follow-up.

This is the only part of the system that contacts people who didn't contact us
first, so it's the only part with real rules attached.

WHAT THE LAW WANTS (not legal advice — see NEVADA-NOTES.md)
  * Prior express consent before sending. We record it when they book and we
    never text a number that hasn't given it.
  * STOP must work, immediately and forever. Twilio enforces the opt-out itself;
    we also record it so we stop scheduling.
  * Quiet hours. TCPA restricts calls and texts to 8am-9pm in the RECIPIENT's
    local time. We use 9am-8pm to leave a margin, because "close to the line"
    is not where you want a care business.

WHAT DECENCY WANTS
  * One follow-up. Not three. If someone doesn't reply to "how did it go", they
    have answered.
  * No follow-up at all on a visit that ended in bereavement. A text asking a
    grieving family to rate their experience would be unforgivable, so a
    booking can be marked `suppress` and nothing further is ever sent.
  * The tip link is mentioned once, gently, and only after a good rating.
    Nobody should feel dunned by a companionship service.

Nothing here sends immediately. The scheduler decides what is DUE; sending is
a separate step, so the decision logic is testable without Twilio.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .callers import normalise

log = logging.getLogger("telephony.outbound")

SCHEMA = """
CREATE TABLE IF NOT EXISTS consent (
    phone       TEXT PRIMARY KEY,
    granted     INTEGER NOT NULL DEFAULT 1,
    source      TEXT,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sent (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reference   TEXT NOT NULL,
    kind        TEXT NOT NULL,
    phone       TEXT NOT NULL,
    body        TEXT,
    sent_at     TEXT NOT NULL,
    UNIQUE(reference, kind)
);

CREATE TABLE IF NOT EXISTS suppressed (
    reference   TEXT PRIMARY KEY,
    reason      TEXT,
    created_at  TEXT NOT NULL
);
"""

QUIET_START = time(9, 0)    # don't text before
QUIET_END = time(20, 0)     # don't text after

KINDS = ("confirmation", "reminder", "followup")


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class Message:
    reference: str
    kind: str
    phone: str
    body: str


class OutboundStore:
    def __init__(self, state_dir: str) -> None:
        d = Path(state_dir)
        d.mkdir(parents=True, exist_ok=True)
        self.path = d / "outbound.db"
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

    # -- consent -------------------------------------------------------------

    def grant_consent(self, phone: str, source: str = "booking") -> None:
        p = normalise(phone)
        if not p:
            return
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO consent (phone, granted, source, updated_at) VALUES (?,1,?,?) "
                "ON CONFLICT(phone) DO UPDATE SET granted=1, updated_at=excluded.updated_at",
                (p, source, _now().isoformat()),
            )

    def revoke_consent(self, phone: str) -> None:
        p = normalise(phone)
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT INTO consent (phone, granted, source, updated_at) VALUES (?,0,'stop',?) "
                "ON CONFLICT(phone) DO UPDATE SET granted=0, updated_at=excluded.updated_at",
                (p, _now().isoformat()),
            )
        log.info("opted out: %s", p)

    def has_consent(self, phone: str) -> bool:
        p = normalise(phone)
        if not p:
            return False
        with self._connect() as c:
            row = c.execute("SELECT granted FROM consent WHERE phone = ?", (p,)).fetchone()
        return bool(row and row["granted"])

    # -- suppression ---------------------------------------------------------

    def suppress(self, reference: str, reason: str = "") -> None:
        """Stop everything further for this booking. Used after a bereavement."""
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT OR REPLACE INTO suppressed (reference, reason, created_at) "
                "VALUES (?,?,?)",
                (reference, reason, _now().isoformat()),
            )
        log.info("suppressed all messages for %s (%s)", reference, reason or "no reason given")

    def is_suppressed(self, reference: str) -> bool:
        with self._connect() as c:
            return bool(
                c.execute("SELECT 1 FROM suppressed WHERE reference = ?", (reference,)).fetchone()
            )

    # -- sent log ------------------------------------------------------------

    def already_sent(self, reference: str, kind: str) -> bool:
        with self._connect() as c:
            return bool(
                c.execute(
                    "SELECT 1 FROM sent WHERE reference = ? AND kind = ?", (reference, kind)
                ).fetchone()
            )

    def record_sent(self, msg: Message) -> None:
        with self._lock, self._connect() as c:
            c.execute(
                "INSERT OR IGNORE INTO sent (reference, kind, phone, body, sent_at) "
                "VALUES (?,?,?,?,?)",
                (msg.reference, msg.kind, normalise(msg.phone), msg.body, _now().isoformat()),
            )

    def history(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._connect() as c:
            rows = c.execute(
                "SELECT * FROM sent ORDER BY sent_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# what to say
# ---------------------------------------------------------------------------


def _spoken_day(dt: datetime, tz: ZoneInfo) -> str:
    loc = dt.astimezone(tz)
    hour = loc.strftime("%I").lstrip("0") or "12"
    minute = "" if loc.minute == 0 else f":{loc.minute:02d}"
    return f"{loc.strftime('%A')} the {loc.day} at {hour}{minute} {loc.strftime('%p').lower()}"


def confirmation_text(business: str, name: str, when: datetime, tz: ZoneInfo,
                      reference: str, booking_link: str) -> str:
    first = (name or "").split()[0] if name else "there"
    return (
        f"Hi {first} — Miss Dee is booked for {_spoken_day(when, tz)}. "
        f"Your reference is {reference}. "
        f"Need to change it? Just reply or call. Reply STOP to stop texts."
    )


def reminder_text(business: str, name: str, when: datetime, tz: ZoneInfo) -> str:
    first = (name or "").split()[0] if name else "there"
    return (
        f"Hi {first} — a reminder that Miss Dee is coming {_spoken_day(when, tz)}. "
        f"Reply or call if anything's changed. Reply STOP to stop texts."
    )


def followup_text(business: str, name: str, review_link: str = "") -> str:
    first = (name or "").split()[0] if name else "there"
    base = (
        f"Hi {first} — how was the visit with Miss Dee? "
        f"Reply RATE 1-5, and add anything you'd like her to know. "
        f"Text BOOK any time for another visit."
    )
    return base + " Reply STOP to stop texts."


def thanks_text(score: int, review_link: str = "", tip_link: str = "") -> str:
    """Sent after a rating comes in. Only mentions money on a good one, once."""
    if score <= 3:
        return "Thank you for telling us — I'm sorry it wasn't better. Miss Dee will call you herself."
    parts = ["Thank you. Miss Dee will be glad to hear that."]
    if review_link:
        parts.append(f"If you'd like to leave a review: {review_link}")
    if tip_link:
        parts.append(f"If you'd ever like to leave her a tip: {tip_link}")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# when to say it
# ---------------------------------------------------------------------------


def within_quiet_hours(now_utc: datetime, tz: ZoneInfo) -> bool:
    """True if it's an acceptable hour to text in the recipient's timezone."""
    local = now_utc.astimezone(tz).time()
    return QUIET_START <= local <= QUIET_END


def due_messages(
    bookings: list[dict[str, Any]],
    store: OutboundStore,
    *,
    now_utc: datetime,
    tz: ZoneInfo,
    business: str,
    booking_link: str,
    reminder_hours: int = 24,
    followup_hours: int = 3,
) -> list[Message]:
    """
    Decide what should go out right now. Sends nothing — returns a list.

    Separated from sending on purpose: every rule below is testable without a
    Twilio account, and the rules are the part that can hurt someone.
    """
    out: list[Message] = []
    if not within_quiet_hours(now_utc, tz):
        return out

    for b in bookings:
        ref = b.get("reference") or ""
        phone = normalise(b.get("client_phone") or "")
        name = b.get("client_name") or ""
        status = b.get("status", "confirmed")
        if not ref or not phone or status != "confirmed":
            continue
        if store.is_suppressed(ref) or not store.has_consent(phone):
            continue

        try:
            start = datetime.fromisoformat(str(b["start_utc"]))
        except (KeyError, ValueError):
            continue
        if start.tzinfo is None:
            start = start.replace(tzinfo=UTC)

        until = (start - now_utc).total_seconds() / 3600.0
        since = (now_utc - start).total_seconds() / 3600.0

        # 1. Confirmation — as soon as it's booked.
        if not store.already_sent(ref, "confirmation") and until > 0:
            out.append(Message(ref, "confirmation", phone,
                               confirmation_text(business, name, start, tz, ref, booking_link)))
            continue   # one message per booking per pass; don't stack texts

        # 2. Reminder — the day before.
        if (0 < until <= reminder_hours
                and not store.already_sent(ref, "reminder")):
            out.append(Message(ref, "reminder", phone,
                               reminder_text(business, name, start, tz)))
            continue

        # 3. Follow-up — a few hours after, once, and never again.
        duration = float(b.get("duration_hours") or 2)
        if (since >= duration + followup_hours
                and since < duration + 72          # a week later is just odd
                and not store.already_sent(ref, "followup")):
            out.append(Message(ref, "followup", phone, followup_text(business, name)))

    return out
