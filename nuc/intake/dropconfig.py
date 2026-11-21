"""dropconfig.py - Atomic Drop / Garden Drop settings from ONE file instead of tables in the code (T0927 slice 1).

Why (Jason 2026-11-19 + 2026-11-21): the drop should be a boilerplate someone else can run, "change just the
logo and it remains flexible" (Garden Drop for Robin, T0942), and the household's email addresses must leave the
code (personal data; the code is mirrored to Gitea and, scrubbed, to a public repo). Before this file the same
people lived in four drifting tables: portal.WHO, portal.PRIVATE_OK, portal.NAMES, classify.PEOPLE, classify.SHELF.

The file (DROP_CONFIG env, default /config/drop.toml; on the NUC ~/.config/drop/drop.toml, mode 600, outside git):

    [brand]
    name = "Atomic Share"            # page titles, share-sheet name
    short_name = "Atomic Share"
    theme = "atomic"                 # atomic (default) | garden (T0942)

    [[person]]
    id = "jason"                     # shelf and folder name: a-z, 0-9, - only
    name = "Jason"                   # how pages greet them
    emails = ["a@example.com"]       # sign-in addresses that are this person
    aliases = ["j", "jason", "me"]   # caption words that file a thing for them (classify.person_from_caption)
    private = true                   # may use the private video shelf (true = all their emails, or a list of some)
    [person.shelves]
    manga = "/srv/manga/jason"
    books = "/srv/books/jason"

FAILS CLOSED: a missing, unreadable or invalid file gives EMPTY people tables (nobody is recognised, nobody is
private; uploads still land in unsorted) and BRAND defaults, and logs one ERROR line. It never falls back to a
built-in list of people, so a broken config cannot grant anything.

Variables (module level, read by portal.py / classify.py / app.py):
    CONFIG_PATH  the file read          ERROR    why it could not be used ("" when fine)
    BRAND        {"name", "short_name", "theme"}
    WHO          email (lower case) -> person id          (was portal.WHO)
    PRIVATE_OK   emails allowed on the private shelf      (was portal.PRIVATE_OK)
    NAMES        person id -> display name                (was portal.NAMES)
    PEOPLE       caption alias (lower case) -> person id  (was classify.PEOPLE)
    SHELF        person id -> {"manga": path, "books": path}  (was classify.SHELF)
OUTCOME 2026-11-21: tables built from the generated file equal the old code tables exactly (test_dropconfig.py).
"""
from __future__ import annotations

import logging
import os
import re
import tomllib
from pathlib import Path

log = logging.getLogger("dropconfig")
CONFIG_PATH = Path(os.environ.get("DROP_CONFIG", "/config/drop.toml"))
DEFAULT_BRAND = {"name": "Atomic Share", "short_name": "Atomic Share", "theme": "atomic"}
THEMES = {"atomic", "garden"}
ID_RE = re.compile(r"^[a-z0-9-]{1,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


def build(data: dict) -> dict:
    """Validated tables from parsed TOML. Raises ValueError with a short reason (never echoing an email)."""
    brand = dict(DEFAULT_BRAND)
    b = data.get("brand", {})
    if not isinstance(b, dict):
        raise ValueError("[brand] must be a table")
    for k in ("name", "short_name"):
        if k in b:
            if not isinstance(b[k], str) or not 0 < len(b[k]) <= 40:
                raise ValueError(f"brand.{k} must be 1-40 characters")
            brand[k] = b[k]
    if "theme" in b:
        if b["theme"] not in THEMES:
            raise ValueError(f"brand.theme must be one of {sorted(THEMES)}")
        brand["theme"] = b["theme"]
    who, private, names, people, shelf = {}, set(), {}, {}, {}
    for n, p in enumerate(data.get("person", []), 1):
        pid = p.get("id", "")
        if not ID_RE.match(pid):
            raise ValueError(f"person {n}: id must be a-z, 0-9 or -")
        if pid in names:
            raise ValueError(f"person {pid}: listed twice")
        names[pid] = str(p.get("name") or pid.title())
        for e in p.get("emails", []):
            if not isinstance(e, str) or not EMAIL_RE.match(e):
                raise ValueError(f"person {pid}: an email is not an address")
            e = e.strip().lower()
            if e in who and who[e] != pid:
                raise ValueError(f"person {pid}: an email already belongs to {who[e]}")
            who[e] = pid
            if p.get("private") is True:
                private.add(e)
        priv = p.get("private")
        if isinstance(priv, list):   # only some of this person's addresses may use the private shelf
            mine = {str(e).strip().lower() for e in p.get("emails", [])}
            for e in priv:
                e = str(e).strip().lower()
                if e not in mine:
                    raise ValueError(f"person {pid}: a private address is not one of their emails")
                private.add(e)
        elif priv not in (None, True, False):
            raise ValueError(f"person {pid}: private must be true, false or a list of their emails")
        for a in p.get("aliases", []):
            a = str(a).strip().lower()
            if a in people and people[a] != pid:
                raise ValueError(f"person {pid}: alias {a!r} already belongs to {people[a]}")
            people[a] = pid
        sh = p.get("shelves")
        if sh is not None:
            if not (isinstance(sh, dict) and set(sh) <= {"manga", "books"}
                    and all(isinstance(v, str) and v.startswith("/srv/") and ".." not in v for v in sh.values())):
                raise ValueError(f"person {pid}: shelves must be manga/books paths under /srv/")
            shelf[pid] = dict(sh)
    return {"BRAND": brand, "WHO": who, "PRIVATE_OK": private, "NAMES": names, "PEOPLE": people, "SHELF": shelf}


def load(path: Path = CONFIG_PATH) -> tuple[dict, str]:
    """(tables, error). On any problem: empty people tables + default brand, and the reason."""
    empty = {"BRAND": dict(DEFAULT_BRAND), "WHO": {}, "PRIVATE_OK": set(), "NAMES": {}, "PEOPLE": {}, "SHELF": {}}
    try:
        with open(path, "rb") as fh:
            return build(tomllib.load(fh)), ""
    except FileNotFoundError:
        reason = f"no config file at {path}"
    except (OSError, tomllib.TOMLDecodeError, ValueError, TypeError, AttributeError) as e:
        reason = f"config {path} not used: {type(e).__name__}: {str(e)[:120]}"
    log.error("dropconfig: %s -- nobody is recognised until it is fixed (fails closed)", reason)
    return empty, reason


_t, ERROR = load()
BRAND, WHO, PRIVATE_OK = _t["BRAND"], _t["WHO"], _t["PRIVATE_OK"]
NAMES, PEOPLE, SHELF = _t["NAMES"], _t["PEOPLE"], _t["SHELF"]
