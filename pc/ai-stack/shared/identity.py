"""
Who is talking to Hermes, on whichever surface they are talking from.

ONE SOURCE OF TRUTH, ALREADY WRITTEN
`people.json` is the household's identity file -- the same one the Rust auth
service reads and the admin panel writes. It already carries everything a
per-person assistant needs: the email the OIDC id_token will present, a
display name, the per-app permission list, and a theme. So there is nothing
to "set up per person": adding somebody to the household already gives
Hermes everything it needs to treat them as an individual.

This module adds exactly TWO optional fields, and is careful to preserve any
field it does not understand when it writes:

    telegram_id   the numeric Telegram user id, bound by the person
                  themselves via a one-time code (see LinkCodes below).
    nas_home      the DSM username whose home directory holds their memory,
                  for when the memory root is the NAS's /homes. Only needed
                  because DSM usernames and SSO emails are different
                  namespaces and nothing maps between them automatically.

WHY A LINK CODE RATHER THAN AN ADMIN TYPING IDS
A Telegram user id is a number nobody knows by heart, and a mistyped one
silently hands one person's memory to another. Instead the person asks
Hermes for a code on Telegram, then enters it while signed in to the
household SSO. The binding is then made by two things that were both
already authenticated, and nobody transcribes anything.
"""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


PEOPLE_FILE = os.environ.get("PEOPLE_FILE", "/data/people.json")
LINKS_FILE = os.environ.get("HERMES_LINKS_FILE", "/state/telegram-links.json")

# A link code is short enough to retype from a phone screen and short-lived
# enough that a shoulder-surfed one is useless by the time it is used.
CODE_TTL_SECONDS = 10 * 60


@dataclass
class Person:
    email: str
    name: str = ""
    apps: list[str] = field(default_factory=list)
    apps_unrestricted: bool = False
    theme: str = ""
    telegram_id: int | None = None
    nas_home: str = ""
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def display(self) -> str:
        return self.name or self.email.split("@")[0]

    def may_use(self, app: str) -> bool:
        return self.apps_unrestricted or app in self.apps


def _read_json(path: str | Path, default: Any) -> Any:
    try:
        # utf-8-sig: the admin panel has written this file from Windows
        # before, and a BOM makes json.load fail with a message that does not
        # mention a BOM.
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def load_people(path: str | Path | None = None) -> list[Person]:
    rows = _read_json(path or PEOPLE_FILE, [])
    if isinstance(rows, dict):
        rows = rows.get("people", [])
    out = []
    for r in rows:
        if not isinstance(r, dict) or not r.get("email"):
            continue
        tid = r.get("telegram_id")
        out.append(Person(
            email=str(r["email"]).strip().lower(),
            name=r.get("name", ""),
            apps=list(r.get("apps") or []),
            apps_unrestricted=bool(r.get("apps_unrestricted")),
            theme=r.get("theme", ""),
            telegram_id=int(tid) if tid else None,
            nas_home=r.get("nas_home", ""),
            raw=r,
        ))
    return out


def by_email(email: str, path: str | Path | None = None) -> Person | None:
    want = (email or "").strip().lower()
    return next((p for p in load_people(path) if p.email == want), None)


def by_telegram_id(tid: int, path: str | Path | None = None) -> Person | None:
    return next((p for p in load_people(path) if p.telegram_id == int(tid)), None)


def set_telegram_id(email: str, tid: int, path: str | Path | None = None) -> bool:
    """Bind a Telegram id to an email, preserving every other field.

    Read-modify-write of a file another service also writes. That is a race,
    and the honest mitigation here is that it happens roughly never (a person
    links once) and the write is atomic, so the loser of a race loses their
    edit rather than corrupting the file. If people.json ever gains a busy
    writer, this needs a lock.
    """
    p = Path(path or PEOPLE_FILE)
    rows = _read_json(p, None)
    if not isinstance(rows, list):
        return False
    want = (email or "").strip().lower()
    hit = False
    for r in rows:
        if str(r.get("email", "")).strip().lower() == want:
            r["telegram_id"] = int(tid)
            hit = True
        elif r.get("telegram_id") == int(tid):
            # One Telegram account maps to one person. Rebinding moves it
            # rather than leaving two rows claiming the same id, which would
            # make by_telegram_id() depend on file order.
            r.pop("telegram_id", None)
    if not hit:
        return False
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, p)
    return True


class LinkCodes:
    """Short-lived codes that bind a Telegram account to an SSO identity."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path or LINKS_FILE)

    def _load(self) -> dict[str, Any]:
        return _read_json(self.path, {}) or {}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8", newline="\n")
        os.replace(tmp, self.path)

    def issue(self, telegram_id: int) -> str:
        """Called from Telegram: give this chat a code to enter on the web."""
        data = self._load()
        now = time.time()
        # Drop expired entries on every write, so the file cannot grow without
        # bound and an old code can never be redeemed.
        data = {k: v for k, v in data.items()
                if now - v.get("issued", 0) < CODE_TTL_SECONDS}
        # Unambiguous alphabet: no O/0, no I/1/l.
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "".join(secrets.choice(alphabet) for _ in range(6))
        data[code] = {"telegram_id": int(telegram_id), "issued": now}
        self._save(data)
        return code

    def redeem(self, code: str, email: str,
               people_path: str | Path | None = None) -> int | None:
        """Called from the web, by somebody already signed in as `email`.

        Returns the Telegram id now bound, or None if the code is unknown or
        expired. The code is consumed either way.
        """
        data = self._load()
        entry = data.pop((code or "").strip().upper(), None)
        self._save(data)
        if not entry:
            return None
        if time.time() - entry.get("issued", 0) >= CODE_TTL_SECONDS:
            return None
        tid = int(entry["telegram_id"])
        return tid if set_telegram_id(email, tid, people_path) else None


def memory_root_for(person: Person, root: str | Path) -> Path:
    """Where this person's memory directory lives under `root`.

    When the root is the NAS's /homes, the directory is the person's OWN DSM
    home -- so the NAS enforces the boundary as well as Hermes does, and the
    person can open their own memory in File Station without being given
    anything new. `nas_home` carries the DSM username because it is a
    different namespace from the SSO email and nothing maps between them.

    Without a nas_home the email-derived key is used, which is correct for a
    plain directory root and merely unshared on a /homes root.
    """
    from .memory import person_key
    root = Path(root)
    if person.nas_home:
        return root / person.nas_home / "hermes-memory"
    return root / person_key(person.email)
