"""
Consent, and what gets kept.

WHY THIS EXISTS
===============
Nevada is an all-party consent state for phone calls. NRS 200.620, as read by
the Nevada Supreme Court in Lane v. Allstate (1998), prohibits a party from
recording their OWN call without the other side's consent -- the "but I'm a
party to the conversation" argument was the dissent, and it lost 3-2. Penalty
is a category D felony, plus civil liquidated damages of $100/day with
attorney's fees, which is the part that actually hurts.

The system is hosted in New York, and Ditech v. Buckles (Nev. 2017) held that
NRS 200.620 doesn't reach interceptions made outside Nevada -- so New York's
one-party rule arguably governs. That is a real defence. It is not one to rely
on: it has never been tested for a NEVADA business that put its hardware out of
state, and it does nothing at all for a caller phoning from California, whose
own courts apply California's all-party law to calls recorded elsewhere
(Kearney v. Salomon Smith Barney).

So: ask. It costs one sentence and it collapses the entire question.

THE TWO THINGS THIS SEPARATES
=============================
Taking the call is not the same as keeping a transcript of it, and conflating
them is what makes systems like this legally awkward.

    ANSWERING          always happens. The assistant is a party to the call,
                       the same as a person answering a phone.
    KEEPING THE WORDS  happens only if the caller agreed.

A caller who says nothing, is confused, or can't work a keypad still gets
helped -- they are never hung up on. They just don't get transcribed. That is
the right default for a service whose callers are elderly and, sometimes,
dying.

WHAT IS KEPT WITHOUT CONSENT
============================
The facts a business is entitled to write down about its own phone:
who called, when, how long, and what they wanted (a booking, a message, a
callback). No verbatim content. That is a business record, not a recording.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("telephony.consent")

# Master switch. false = transcripts are NEVER stored, whatever anyone says.
# The safest setting, and the default, because the phone line works completely
# without them.
STORE_TRANSCRIPTS = os.environ.get("STORE_TRANSCRIPTS", "false").lower() == "true"

# How long a stored transcript survives. Short on purpose: a transcript of an
# elderly client's personal and financial details is a high-consequence thing
# to be holding, and the business value of one decays within days.
RETAIN_DAYS = int(os.environ.get("TRANSCRIPT_RETAIN_DAYS", "30"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS consent (
    phone       TEXT NOT NULL,
    call_id     TEXT NOT NULL,
    granted     INTEGER NOT NULL,
    method      TEXT,               -- keypad | spoken | none
    heard       TEXT,               -- what they actually said, for the record
    wording_id  TEXT,               -- which disclosure they were read
    created_at  TEXT NOT NULL,
    PRIMARY KEY (call_id)
);
CREATE INDEX IF NOT EXISTS idx_consent_phone ON consent(phone, created_at DESC);
"""

# The disclosure, versioned. If the wording changes, the id changes, so an old
# consent record still says exactly what that person agreed to.
WORDING_ID = "2026-08-a"

DISCLOSURE = (
    "Thank you for calling {business}. "
    "If this is an emergency, or someone's life is in danger, "
    "please hang up and call 9 1 1. "
    "I'm Dee's automated assistant. "
    "To help me get your details right, I can write down what we say. "
    "If that's alright, press any key, or say yes. "
    "If you'd rather I didn't, just stay on the line and I'll still help you."
)

# Deliberately generous. "yeah", "yep", "sure", "go ahead", "that's fine" are
# all how an actual person says yes, and a strict matcher would silently record
# refusals as non-answers.
_YES = re.compile(
    r"\b(yes|yeah|yep|yup|sure|ok|okay|fine|alright|all right|go ahead|"
    r"that'?s fine|i don'?t mind|no problem|of course|please do)\b",
    re.I,
)
_NO = re.compile(
    r"\b(no|nope|don'?t|do not|rather not|prefer not|stop|private)\b",
    re.I,
)

# Phrases that contain a refusal word but mean the opposite. "I don't mind" is
# how a lot of people over seventy say yes, and the refusal matcher would read
# the "don't" and record it as a no. Checked BEFORE _NO for that reason.
_YES_DESPITE_NO = re.compile(
    r"\b(don'?t mind|do not mind|don'?t care|no problem|no worries|"
    r"why not|can'?t see why not|no objection)\b",
    re.I,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ConsentStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(SCHEMA)

    def _db(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        return db

    def record(
        self,
        call_id: str,
        phone: str,
        granted: bool,
        method: str = "none",
        heard: str = "",
    ) -> None:
        with self._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO consent "
                "(phone, call_id, granted, method, heard, wording_id, created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                (phone, call_id, 1 if granted else 0, method, heard[:200],
                 WORDING_ID, _now()),
            )
        log.info(
            "consent %s for %s via %s",
            "GRANTED" if granted else "declined", phone or "(withheld)", method,
        )

    def granted(self, call_id: str) -> bool:
        with self._db() as db:
            row = db.execute(
                "SELECT granted FROM consent WHERE call_id = ?", (call_id,)
            ).fetchone()
        return bool(row and row["granted"])

    def purge(self, days: int | None = None) -> int:
        """Consent records outlive transcripts -- they're the proof you asked."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days or 365 * 2)).isoformat()
        with self._db() as db:
            cur = db.execute("DELETE FROM consent WHERE created_at < ?", (cutoff,))
            return cur.rowcount


def interpret(digits: str, speech: str) -> tuple[bool, str, str]:
    """
    Did they agree?

    Returns (granted, method, what_we_heard).

    ANY key counts, not just 1. An 84-year-old on a cordless handset should not
    have to find a specific button, and pressing something is unambiguous
    evidence of a deliberate act -- which is the whole point.
    """
    if digits:
        return True, "keypad", digits

    said = (speech or "").strip()
    if said:
        # Order matters, and this is the order:
        #
        #   1. "don't mind"  -- yes, despite containing a refusal word
        #   2. any refusal   -- "no, that's fine" is a NO; the yes-matcher
        #                       would otherwise catch "fine" and invert it
        #   3. any agreement
        #   4. anything else -- treated as no
        #
        # Step 4 is the one that matters. "What?", "Hello?", "I can't hear
        # you" are not consent, and a system that guesses yes when it isn't
        # sure manufactures a record saying someone agreed when they didn't.
        # Every ambiguous case resolves to NOT storing, which costs a
        # transcript and nothing else.
        if _YES_DESPITE_NO.search(said):
            return True, "spoken", said
        if _NO.search(said):
            return False, "spoken", said
        if _YES.search(said):
            return True, "spoken", said

    return False, "none", said


def should_store(call_id: str, store: ConsentStore) -> bool:
    """The only question the rest of the code needs to ask."""
    if not STORE_TRANSCRIPTS:
        return False
    return store.granted(call_id)


def purge_transcripts(root: str | Path, days: int | None = None) -> int:
    """
    Delete phone transcripts past the retention window.

    Only touches the `phone` surface. Hermes and Telegram transcripts are the
    owner's own conversations with his own assistant -- no third party, no
    consent question, no reason to expire them.
    """
    days = days if days is not None else RETAIN_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base = Path(root) / "phone"
    if not base.exists():
        return 0

    removed = 0
    for day_dir in base.iterdir():
        if not day_dir.is_dir():
            continue
        try:
            day = datetime.strptime(day_dir.name, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        if day >= cutoff:
            continue
        for f in day_dir.glob("*.jsonl"):
            try:
                f.unlink()
                removed += 1
            except OSError as e:
                log.warning("couldn't delete %s: %s", f, e)
        try:
            day_dir.rmdir()
        except OSError:
            pass

    if removed:
        log.info("purged %d phone transcripts older than %d days", removed, days)
    return removed


# --- redaction ---------------------------------------------------------------
#
# Nevada's data-protection law (NRS 603A) attaches to "personal information",
# which means a name PLUS a social security number, card number, or insurance
# ID. Names and phone numbers alone are not covered. So scrubbing those few
# patterns before anything is written keeps the whole chapter from applying --
# a genuinely cheap way to shed a category of obligation.

_SSN  = re.compile(r"\b\d{3}[- ]?\d{2}[- ]?\d{4}\b")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_ACCT = re.compile(r"\b(?:account|routing|policy|member|medicare)\s*(?:number|#|no\.?)?\s*[:# ]\s*[\w-]{5,}\b", re.I)


def redact(text: str) -> str:
    """Strip the identifiers that would drag this under NRS 603A."""
    if not text:
        return text
    text = _SSN.sub("[redacted-ssn]", text)
    text = _ACCT.sub("[redacted-account]", text)

    # Card-shaped runs only when they pass Luhn -- otherwise every phone
    # number, date and dollar amount gets mangled.
    def _card(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group(0))
        if len(digits) < 13 or not _luhn(digits):
            return m.group(0)
        return "[redacted-card]"

    return _CARD.sub(_card, text)


def _luhn(number: str) -> bool:
    total, alt = 0, False
    for ch in reversed(number):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0
