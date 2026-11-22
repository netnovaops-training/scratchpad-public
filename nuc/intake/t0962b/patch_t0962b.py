#!/usr/bin/env python3
"""patch_t0962b.py - T0962 point 5 + the rescan follow-ups from the 11-21 doc review, in a COPY of intake.

    python3 patch_t0962b.py ~/sandbox/t0962b/intake

5) Quarantine notes kept the uploader's EMAIL for files from the drop page (route_file), personal data in a folder
   that is mailed about. Jason 11-21 chose the recommended option: keep the person id. route_file now passes
   "drop:<person>" (or "drop:unknown"), like /intake already passes "<door>:<person>".
Rescan follow-ups (rescan_held.py, scan.py):
  - one run at a time: rescan_held takes an exclusive flock on QUARANTINE/.rescan.lock (a second run exits quietly)
  - a folder that fails (unreadable, move error) is counted as "failed", logged with its name, and the run exits 1
    so the cron log shows it; the other folders still run
  - the unused names (module UNSORTED, header "Q") are gone; the header says a duplicate is dropped by app.place
    because its twin is already in unsorted
  - scan.held_count(): one unreadable folder no longer turns the whole count into 0
Refuses on the live folder (by realpath) or if any old text is not found exactly once.
Variables: folder (the copy), ap / rs / sc (file texts), swap() (exact-once replace).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser() if len(sys.argv) == 2 else None
if folder is None or not (folder / "rescan_held.py").is_file():
    raise SystemExit("usage: patch_t0962b.py <copy of the intake folder>")
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")


def swap(text: str, old: str, new: str, name: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:60]!r}, found {text.count(old)}")
    return text.replace(old, new)


ap = (folder / "app.py").read_text(encoding="utf-8")
if "drop:{who" in ap:
    raise SystemExit("already patched")
ap = swap(ap, "            scan.hold(tmp, name, email, state, detail, digest)\n",
          "            # T0962 point 5 (Jason 11-21): the note keeps the person id, not the email address.\n"
          "            scan.hold(tmp, name, f\"drop:{who or 'unknown'}\", state, detail, digest)\n", "app.py")
compile(ap, "app.py", "exec")

sc = (folder / "scan.py").read_text(encoding="utf-8")
sc = swap(sc, '''    try:
        # T0964: only folders that still hold a file; rescan_held.py leaves released folders as a record.
        return sum(1 for d in QUARANTINE.iterdir() if d.is_dir() and any(p.suffix == ".held" for p in d.iterdir()))
    except OSError:
        return 0
''', '''    # T0964: only folders that still hold a file; rescan_held.py leaves released folders as a record.
    # T0962 follow-up: an unreadable folder is skipped, not a reason to report 0 for all of them.
    try:
        folders = [d for d in QUARANTINE.iterdir() if d.is_dir()]
    except OSError:
        return 0
    n = 0
    for d in folders:
        try:
            n += any(p.suffix == ".held" for p in d.iterdir())
        except OSError:
            continue
    return n
''', "scan.py")
compile(sc, "scan.py", "exec")

rs = (folder / "rescan_held.py").read_text(encoding="utf-8")
rs = swap(rs, '''"infected" folders are never touched. Nothing is deleted (app.place() drops only an exact duplicate whose twin
is already in unsorted). The folder and its WHY.txt stay as the record.
Variables: Q (quarantine folder), UNSORTED (where released files go), counts (clean/infected/down/skipped).
Exit code 0 always; one summary line on stdout.
''', '''"infected" folders are never touched. Nothing is deleted, with one exception: when a released file is an exact
duplicate (same sha256) of a file already in unsorted, app.place() drops the held copy, because its twin is
already there (no sidecar then). The folder and its WHY.txt stay as the record.
One run at a time: an exclusive flock on QUARANTINE/.rescan.lock; a second run exits quietly (exit 0).
Variables: counts (clean / infected / down / skipped / failed), lock (the open lock file), why_file / held / why
(one folder's note, its held file, the parsed note), name / digest / verdict / res (one release).
Exit code: 0, or 1 when any folder failed (named on stderr, so the cron log shows it); one summary line.
OUTCOME 2026-11-21 (T0962 follow-up): lock, failed count and exit code added after the doc review.
''', "rescan_held.py")
rs = swap(rs, '''import json
import pathlib
import time

import scan

UNSORTED = None  # set in main() from app.UNSORTED, so the same setting decides both


def main() -> dict:
    import app  # the running container's settings (UNSORTED, place, sha256)
    counts = {"clean": 0, "infected": 0, "down": 0, "skipped": 0}
    if not scan.ENABLED or not scan.QUARANTINE.is_dir():
        return counts
    for folder in sorted(p for p in scan.QUARANTINE.iterdir() if p.is_dir()):
''', '''import fcntl
import json
import pathlib
import sys
import time

import scan


def main() -> dict:
    import app  # the running container's settings (UNSORTED, place, sha256)
    counts = {"clean": 0, "infected": 0, "down": 0, "skipped": 0, "failed": 0}
    if not scan.ENABLED or not scan.QUARANTINE.is_dir():
        return counts
    lock = open(scan.QUARANTINE / ".rescan.lock", "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("rescan_held: another run holds the lock; nothing done")
        return counts
    for folder in sorted(p for p in scan.QUARANTINE.iterdir() if p.is_dir()):
        try:
            one(app, folder, counts)
        except Exception as e:  # noqa: BLE001  -- one bad folder must not stop the others; counted and named
            counts["failed"] += 1
            print(f"rescan_held: FAILED {folder.name}: {type(e).__name__}: {e}", file=sys.stderr)
    return counts


def one(app, folder: pathlib.Path, counts: dict) -> None:
    """Re-check one quarantine folder (see the header)."""
''', "rescan_held.py")
# The old loop body (8-space indent, `continue` to skip a folder) becomes the body of one(): dedent it by 4
# and turn its `continue` into `return`. It ends at the blank lines before `if __name__`.
start = rs.index('    """Re-check one quarantine folder (see the header)."""\n') + len('    """Re-check one quarantine folder (see the header)."""\n')
end = rs.index("\n\nif __name__")
body = "\n".join((line[4:] if line.startswith("        ") else line) for line in rs[start:end].split("\n"))
body = body.replace("        continue\n", "        return\n")
rs = rs[:start] + body + rs[end:]
rs = swap(rs, '''    counts["clean"] += 1
    return counts
''', '''    counts["clean"] += 1
''', "rescan_held.py")   # (already dedented above)
rs = swap(rs, '''    print(f"rescan_held: released {c['clean']}, now infected {c['infected']}, scanner down {c['down']}, "
          f"left alone {c['skipped']}")
''', '''    print(f"rescan_held: released {c['clean']}, now infected {c['infected']}, scanner down {c['down']}, "
          f"left alone {c['skipped']}, failed {c['failed']}")
    sys.exit(1 if c["failed"] else 0)
''', "rescan_held.py")
compile(rs, "rescan_held.py", "exec")

for name, text in (("app.py", ap), ("scan.py", sc), ("rescan_held.py", rs)):
    (folder / name).write_text(text, encoding="utf-8")
print("patched: app.py (person id in quarantine notes), scan.py (held_count), rescan_held.py (lock, failed, exit)")
