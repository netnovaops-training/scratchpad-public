"""
Invite links for the guest bot: one tap to join, nothing to transcribe.

HOW IT WORKS, and why it needs no new protocol. Telegram already has this:

    https://t.me/<botname>?start=<payload>

Tapping that opens the bot and sends it `/start <payload>` silently. So an
invite IS a payload -- issue one, put it in a link, send the link. The friend
taps once and is enrolled. Nobody reads a user id aloud and nobody edits an
allow-list by hand, which is the step that gets a digit wrong and hands one
person's memory to another.

WHY NOT JUST ALLOW EVERYONE WHO FINDS THE BOT. Because a bot username is
guessable and Telegram search is public. Without a redemption step, "who can
talk to this" is "anyone who types the right name", which is not a decision
anybody made.

THE FOUR PROPERTIES THAT MAKE A LINK SAFE TO SEND, each for a reason that has
a failure behind it:

  * SINGLE USE by default. A group chat forwards a link without anyone
    deciding to; if one invite enrols everyone who taps it, the guest list is
    set by whoever shares fastest.
  * EXPIRING. A link sits in a message history forever. An invite that is
    still live in six months is a credential nobody remembers issuing.
  * UNGUESSABLE. secrets.token_urlsafe, not a counter and not a name. A
    sequential invite is an invitation to try the next number.
  * ISSUED FOR SOMEONE. The record says who it was meant for, so when it is
    redeemed by a different account that discrepancy is visible rather than
    silent. This does not prevent forwarding; it makes forwarding legible.

WHAT THIS DELIBERATELY DOES NOT DO: decide what a guest may then access. It
answers "is this person allowed in at all". What they can reach once inside
is the guest bot's own configuration -- no tools, no workspace -- and that
separation is the point. An invite should never be able to grant capability.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Telegram's start payload allows A-Za-z0-9_- and at most 64 characters.
# token_urlsafe stays inside that with room to spare.
_TOKEN_BYTES = 12

DEFAULT_TTL_DAYS = 7
INVITES_FILE = os.environ.get(
    "INVITES_FILE", "/data/auth/invites.json"
)


@dataclass
class Invite:
    token: str
    issued_for: str
    issued_at: float
    expires_at: float
    uses_left: int
    redeemed_by: list[int]

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def spent(self) -> bool:
        return self.uses_left <= 0

    def link(self, bot_username: str) -> str:
        return "https://t.me/{}?start={}".format(bot_username.lstrip("@"), self.token)


def _read(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    # Atomic, like LinkCodes and MemoryStore: a half-written invite file would
    # either lock everyone out or let a spent invite look fresh.
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True),
                   encoding="utf-8", newline="\n")
    os.replace(tmp, path)


class Invites:
    """Issue and redeem guest invites. One JSON file, atomically written."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or INVITES_FILE)

    def _load(self) -> dict[str, Any]:
        data = _read(self.path)
        now = time.time()
        # Prune on every read-for-write, the same way LinkCodes does. An
        # expired invite that is merely ignored still sits in the file looking
        # valid to anything that reads it less carefully than this does.
        kept = {
            t: v for t, v in data.items()
            if v.get("expires_at", 0) > now or v.get("redeemed_by")
        }
        return kept

    def issue(self, issued_for: str, *, uses: int = 1,
              ttl_days: int = DEFAULT_TTL_DAYS) -> Invite:
        """Mint an invite. `issued_for` is a human label, not an identity."""
        if uses < 1:
            raise ValueError("an invite with no uses cannot be redeemed")
        if ttl_days < 1:
            raise ValueError("an invite that expires today is a typo")

        data = self._load()
        now = time.time()
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        inv = Invite(
            token=token,
            issued_for=issued_for.strip()[:80] or "unnamed",
            issued_at=now,
            expires_at=now + ttl_days * 86400,
            uses_left=int(uses),
            redeemed_by=[],
        )
        data[token] = {
            "issued_for": inv.issued_for,
            "issued_at": inv.issued_at,
            "expires_at": inv.expires_at,
            "uses_left": inv.uses_left,
            "redeemed_by": [],
        }
        _write(self.path, data)
        return inv

    def redeem(self, token: str, telegram_id: int) -> Invite | None:
        """Spend one use. Returns the invite, or None if it is not usable.

        Returning None for every failure mode on purpose -- expired, spent,
        never existed. The caller shows one message for all of them, because
        telling a stranger WHICH of those it was turns this into an oracle
        for probing which tokens exist.
        """
        token = (token or "").strip()
        if not token:
            return None

        data = self._load()
        rec = data.get(token)
        if not rec:
            _write(self.path, data)   # persist the prune even on a miss
            return None

        now = time.time()
        if rec.get("expires_at", 0) <= now or rec.get("uses_left", 0) <= 0:
            _write(self.path, data)
            return None

        # Re-redeeming from the same account is idempotent rather than a
        # second spend: taps get repeated, and a friend who opens the link
        # twice should not burn somebody else's seat.
        already = int(telegram_id) in rec.get("redeemed_by", [])
        if not already:
            rec["uses_left"] = int(rec["uses_left"]) - 1
            rec.setdefault("redeemed_by", []).append(int(telegram_id))

        data[token] = rec
        _write(self.path, data)
        return Invite(
            token=token,
            issued_for=rec.get("issued_for", "unnamed"),
            issued_at=rec.get("issued_at", 0.0),
            expires_at=rec.get("expires_at", 0.0),
            uses_left=rec.get("uses_left", 0),
            redeemed_by=list(rec.get("redeemed_by", [])),
        )

    def revoke(self, token: str) -> bool:
        """Kill an invite that has been sent to the wrong person."""
        data = self._load()
        if token not in data:
            _write(self.path, data)
            return False
        del data[token]
        _write(self.path, data)
        return True

    def enrolled(self) -> dict[int, str]:
        """telegram_id -> the label its invite was issued for.

        This is the guest allow-list. It is DERIVED from redemptions rather
        than maintained separately, so there is no second list to fall out of
        step with this one.
        """
        out: dict[int, str] = {}
        for rec in _read(self.path).values():
            for tid in rec.get("redeemed_by", []):
                out[int(tid)] = rec.get("issued_for", "unnamed")
        return out

    def list_open(self) -> list[Invite]:
        """Invites that could still be redeemed right now."""
        now = time.time()
        out = []
        for token, rec in _read(self.path).items():
            if rec.get("expires_at", 0) > now and rec.get("uses_left", 0) > 0:
                out.append(Invite(
                    token=token,
                    issued_for=rec.get("issued_for", "unnamed"),
                    issued_at=rec.get("issued_at", 0.0),
                    expires_at=rec.get("expires_at", 0.0),
                    uses_left=rec.get("uses_left", 0),
                    redeemed_by=list(rec.get("redeemed_by", [])),
                ))
        return sorted(out, key=lambda i: i.issued_at, reverse=True)


def guest_identity(telegram_id: int) -> str:
    """The person key for a guest who has no household SSO account.

    Email-SHAPED so person_key() in shared/memory.py validates it with the
    same rule as everyone else -- one identity format, one validator, no
    second path that could be laxer than the first.

    `.invalid` rather than a domain that could ever exist: RFC 2606 reserves
    it precisely so a synthetic address cannot collide with, or be mistaken
    for, a real mailbox. Nothing will ever try to deliver mail here.
    """
    return f"tg-{int(telegram_id)}@guest.invalid"
