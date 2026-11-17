"""Reading and writing the JSON files in Admin\\data.

EVERYTHING ABOUT COLLISIONS IS IN HERE, because two different programs now
write this folder: the Rust admin and this app. That is the question the
Streamlit thread asked and nobody answered, so it is worth being explicit about
what this actually guarantees.

    A TORN READ IS IMPOSSIBLE.
    Every write goes to a temp file and is then renamed over the target. On the
    same filesystem a rename is atomic, so a reader sees either the whole old
    file or the whole new one. Never half of each. This matters more than it
    sounds: `auth` and `mom` read these files on live requests, and a parse
    error caused by catching a write mid-flight would look like the site
    breaking at random.

    A LOST UPDATE IS *DETECTED*, NOT PREVENTED.
    Every load records the file's modification time. Save refuses if that time
    has changed since - meaning somebody else wrote it while you had it open -
    and tells you so instead of overwriting their work. You reload and redo
    your edit. This is optimistic concurrency, and it is the honest answer for
    one person with two browser tabs: locking would be more machinery for a
    collision that will happen about twice a year.

    THE OTHER TAB IS NOT LIVE-UPDATED.
    A grid you already have open goes stale the moment somebody else saves.
    The check above is what stops that stale copy being written back on top.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(os.getenv("TABLES_DATA_DIR", "/data/site"))
BACKUP_DIR = Path(os.getenv("TABLES_BACKUP_DIR", "/data/backups"))
KEEP_BACKUPS = 10


class Stale(Exception):
    """The file changed underneath us. Raised instead of overwriting."""


def path_for(target: dict) -> Path:
    return DATA_DIR / target["file"]


def mtime(target: dict) -> float:
    try:
        return path_for(target).stat().st_mtime
    except OSError:
        # 0.0 means "was not there when I looked", which is a real state - the
        # site is on its defaults - and has to be distinguishable from a file
        # that exists, so that "somebody created it while I was editing" is
        # caught by the same check as "somebody changed it".
        return 0.0


def load(target: dict):
    """The file's contents, or None if it does not exist yet.

    A missing file is NOT an error. It means that site is running on whatever
    defaults it ships with, which is a normal state for a stack where most of
    these are optional.
    """
    p = path_for(target)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def rows(target: dict) -> list[dict]:
    """A list file's records, tolerating a bare array on disk.

    A hand-edited file is quite likely to be a bare list, and kb.py accepts
    one, so refusing it here would mean the grid disagreed with the app that
    actually reads the file.
    """
    doc = load(target)
    if doc is None:
        return []
    if isinstance(doc, list):
        return [r for r in doc if isinstance(r, dict)]
    if isinstance(doc, dict):
        got = doc.get(target["list_key"])
        if isinstance(got, list):
            return [r for r in got if isinstance(r, dict)]
    return []


def shell(target: dict) -> dict:
    """Everything in a list file EXCEPT the list.

    Preserved across a save. Somebody's hand-written `_note` at the top of the
    file surviving an edit is the difference between this being a file you own
    and a file the editor owns.
    """
    doc = load(target)
    if isinstance(doc, dict):
        return {k: v for k, v in doc.items() if k != target["list_key"]}
    return {}


def _backup(p: Path) -> str | None:
    if not p.exists():
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    # Same naming as the Rust admin's, so both programs' backups appear in one
    # list, in one order, on the admin's "Previous versions" panel.
    #
    # THE SUFFIX IS NOT DECORATION. The stamp is only accurate to the second,
    # and two saves inside one second produce the same filename - so the second
    # one silently overwrote the first, destroying the very version somebody
    # would want back. Found by a test that saved in a loop; a human double-
    # clicking Save gets there just as easily.
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{p.name}"
    n = 1
    while (BACKUP_DIR / name).exists():
        name = f"{stamp}-{n}-{p.name}"
        n += 1
    shutil.copy2(p, BACKUP_DIR / name)
    _prune(p.name)
    return name


def _prune(filename: str) -> None:
    try:
        mine = sorted(BACKUP_DIR.glob(f"*-{filename}"))
    except OSError:
        return
    for old in mine[:-KEEP_BACKUPS]:
        try:
            old.unlink()
        except OSError:
            pass


def save(target: dict, doc, expect_mtime: float | None = None) -> str | None:
    """Write the file. Returns the backup's name, or None if there was nothing
    to back up. Raises Stale if somebody else wrote it first."""
    p = path_for(target)
    if expect_mtime is not None and abs(mtime(target) - expect_mtime) > 1e-6:
        raise Stale(
            f"{target['file']} changed on disk since you opened it. "
            "Reload before saving, or your edit would wipe out theirs."
        )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    backup = _backup(p)

    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)          # atomic on the same filesystem
    return backup


def save_rows(target: dict, records: list[dict], expect_mtime: float | None = None) -> str | None:
    doc = shell(target)
    doc[target["list_key"]] = records
    return save(target, doc, expect_mtime)


def backups(target: dict) -> list[tuple[str, float]]:
    try:
        found = sorted(BACKUP_DIR.glob(f"*-{target['file']}"), reverse=True)
    except OSError:
        return []
    out = []
    for f in found:
        try:
            out.append((f.name, f.stat().st_mtime))
        except OSError:
            pass
    return out


def restore(target: dict, backup_name: str) -> str | None:
    """Put a backup back. Backs up the current file first, so restoring the
    wrong one is itself undoable - which is the whole reason anybody is brave
    enough to press the button."""
    src = BACKUP_DIR / backup_name
    if not src.is_file() or not backup_name.endswith(target["file"]):
        raise ValueError("That backup does not belong to this file.")
    doc = json.loads(src.read_text(encoding="utf-8"))
    return save(target, doc)


def describe_age(ts: float) -> str:
    if not ts:
        return "never"
    secs = max(0, time.time() - ts)
    if secs < 90:
        return "just now"
    mins = secs / 60
    if mins < 90:
        return f"{int(mins)} minutes ago"
    hours = mins / 60
    if hours < 36:
        return f"{int(hours)} hours ago"
    return datetime.fromtimestamp(ts).strftime("%d %b %Y, %H:%M")


# --------------------------------------------------------------------- rows --
#
# Turning what the grid gives back into what belongs in the file. Lives here
# rather than in app.py so it can be tested without starting Streamlit -- the
# rules below are the ones that decide whether an edit is saved correctly, and
# "it looked right on screen" is not a test.

# Fields that hold a LIST in the file but are shown as one-item-per-line text in
# the grid. A grid cell cannot hold a nested list, and JSON-in-a-cell is neither
# readable nor typeable by hand.
LIST_FIELDS = {"symptoms", "steps", "keywords", "tags", "good_for", "albums"}


def slug(s) -> str:
    out, dash = [], False
    for ch in str(s).lower():
        if ch.isalnum() and ch.isascii():
            out.append(ch)
            dash = False
        elif not dash and out:
            out.append("-")
            dash = True
    return "".join(out).rstrip("-")[:60]


def flatten(v):
    """One field, as the grid should show it."""
    if isinstance(v, list):
        return "\n".join(str(x) for x in v)
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False)
    return v


def clean_rows(target: dict, edited, columns) -> tuple[list[dict], list[str]]:
    """(records to save, problems). A non-empty problems list means SAVE NOTHING.

    Partial saves are refused on purpose: half a table written is worse than
    none, because you cannot tell by looking which half went in.
    """
    cleaned: list[dict] = []
    problems: list[str] = []
    ids: set[str] = set()

    for i, row in enumerate(edited, 1):
        rec = {}
        for c in columns:
            v = row.get(c)
            if v is None or v == "":
                continue
            if c in LIST_FIELDS and isinstance(v, str):
                parts = [p.strip() for p in v.splitlines() if p.strip()]
                if parts:
                    rec[c] = parts
                continue
            rec[c] = v
        if not rec:
            continue        # a blank row the grid left behind

        rid = str(row.get("id") or "").strip() or slug(rec.get(target["name_key"], ""))
        rid = slug(rid)
        if not rid:
            problems.append(
                f"Row {i} has no id and no {target['name_key']}, so there would "
                "be no way to tell it apart from the next one."
            )
            continue
        if rid in ids:
            problems.append(f"Row {i}: two rows would both be “{rid}”.")
            continue
        ids.add(rid)
        rec["id"] = rid
        cleaned.append(rec)

    return cleaned, problems
