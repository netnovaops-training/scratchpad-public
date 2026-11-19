"""
Texting — the same number people call.

This is what you wanted a short code for, and you don't need one. A regular
Twilio local number does voice AND SMS on the same line for $1.15/month.
Short codes are messaging-only, cost $500-1,000/month, and take 6-12 weeks of
carrier approval. Nobody needs that to text a booking link.

So the card Dee leaves at the house says one number. Call it, or text it.

    BOOK            the booking link
    12345           a client number -> their next visit and how it's going
    NOTE <text>     leave something for Dee to read
    RATE 5          rate the last visit, optionally with a comment
    STOP / HELP     the ones carriers require

Anything else gets a short, human reply pointing at the options.

One piece of paperwork: business SMS on a US 10-digit number needs A2P 10DLC
registration in Twilio — a one-time brand registration and a campaign, a few
dollars a month. It's a form, not a hurdle, but messages get filtered if you
skip it.
"""

from __future__ import annotations

import logging
import re

from .clients import ClientStore
from .outbound import thanks_text

log = logging.getLogger("telephony.sms")

MAX_SMS = 320   # two segments; longer gets split and looks messy


def _reply(text: str) -> str:
    """TwiML for a single SMS reply."""
    safe = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )[:MAX_SMS]
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f"<Response><Message>{safe}</Message></Response>"
    )


def _no_reply() -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n<Response/>'


def handle(
    body: str,
    from_phone: str,
    *,
    clients: ClientStore,
    business_name: str,
    booking_url: str,
    ratings,
    outbound=None,
    review_link: str = "",
    tip_link: str = "",
) -> str:
    """Returns TwiML. Kept deliberately dumb — no model in the loop."""
    text = (body or "").strip()
    upper = text.upper()
    client = clients.by_phone(from_phone)

    # --- carrier-required keywords ------------------------------------------
    if upper in {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT"}:
        # Twilio blocks further messages itself; we record it too so the
        # scheduler stops queueing them in the first place.
        if outbound:
            outbound.revoke_consent(from_phone)
        return _no_reply()

    if upper in {"START", "UNSTOP", "YES"} and outbound:
        outbound.grant_consent(from_phone, source="sms-start")
        return _reply(f"You're subscribed to {business_name} updates again. "
                      f"Reply STOP any time.")

    if upper in {"HELP", "INFO"}:
        return _reply(
            f"{business_name}: text BOOK for the booking page, NOTE <message> to "
            f"leave a note for Dee, or RATE 1-5 after a visit. Or just call this "
            f"number and talk to us. Reply STOP to opt out."
        )

    # --- booking link --------------------------------------------------------
    if upper in {"BOOK", "BOOKING", "CALENDAR", "CALENDER", "TIMES", "APPOINTMENT"}:
        # Asking us for the booking link is itself consent to be texted back.
        if outbound:
            outbound.grant_consent(from_phone, source="sms-book")
        return _reply(f"Book a visit with Miss Dee here: {booking_url}")

    if upper in {"TIP"} and tip_link:
        return _reply(f"That's very kind. Here you go: {tip_link}")

    if upper in {"REVIEW"} and review_link:
        return _reply(f"Thank you — it really helps: {review_link}")

    # --- rating --------------------------------------------------------------
    # Match against `text`, not `upper` -- otherwise the comment gets stored
    # SHOUTING, and "she was lovely" arrives in Dee's inbox as "SHE WAS LOVELY".
    score: int | None = None
    comment = ""
    m = re.match(r"^\s*RATE\s*([1-5])\b[\s.,:;-]*(.*)$", text, re.IGNORECASE | re.DOTALL)
    if m:
        score = int(m.group(1))
        comment = (m.group(2) or "").strip()
    else:
        # A bare "5" counts only if we actually asked this number for a rating.
        m2 = re.match(r"^\s*([1-5])\s*$", text)
        if m2 and ratings.awaiting(from_phone):
            score = int(m2.group(1))

    if score is not None:
        if not client:
            return _reply(
                "Thank you. We couldn't match your number to a client — reply with "
                "your 5-digit client number and we'll link it up."
            )
        ratings.record(client["code"], score, comment, from_phone)
        return _reply(thanks_text(score, review_link=review_link, tip_link=tip_link))

    # --- leave a note --------------------------------------------------------
    m = re.match(r"^\s*NOTE\b[\s:,-]*(.+)$", text, re.IGNORECASE | re.DOTALL)
    if m:
        note = m.group(1).strip()
        if not client:
            return _reply(
                "Send your 5-digit client number first so we know who this is "
                "about, then send your note."
            )
        clients.add_note(client["code"], note, from_phone=from_phone)
        return _reply("Written down. Miss Dee will read it before her next visit.")

    # --- a bare client number ------------------------------------------------
    digits = re.sub(r"\D", "", text)
    if len(digits) == 5:
        found = clients.get(digits)
        if not found:
            return _reply(
                "That client number isn't one of ours. Check the card Miss Dee "
                "left, or just call this number and we'll sort it out."
            )
        clients.link_phone(digits, from_phone)
        notes = clients.recent_notes(digits, limit=1)
        last = f" Last note: {notes[0]['note'][:90]}" if notes else ""
        return _reply(
            f"You're linked to {found['name']}. Text NOTE <message> to leave "
            f"something for Dee, BOOK for the calendar, or call us any time.{last}"
        )

    # --- anything else -------------------------------------------------------
    who = f", {client['name'].split()[0]}" if client else ""
    return _reply(
        f"Hello{who}. Text BOOK for the booking page, NOTE <message> to leave a "
        f"note for Miss Dee, or RATE 1-5 after a visit. You can also just call "
        f"this number."
    )
