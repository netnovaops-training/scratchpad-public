"""rescan_held.py - T0962 point 4: re-check uploads that were held only because the virus scanner was down.

    docker exec intake python rescan_held.py          (hourly from the server crontab, at :23)

Why (2026-11-21): T0920 holds a file in quarantine when clamd does not answer ("error"), so nothing is ever
filed unchecked. But nothing looked at those files again: once the scanner was back they stayed held forever.
Now, for every quarantine folder whose WHY.txt says state "error" and that still holds its .held file:
  - scanner answers clean  -> the file goes to UNSORTED through app.place() (same duplicate check and atomic
                              rename as any upload) with a .intake.json sidecar saying why; WHY.txt becomes
                              state "released" with where it went. Unsorted, not its library: the person,
                              shelf and type choice from the upload are not kept, so the owner sorts it.
  - scanner says infected  -> it stays held; WHY.txt becomes state "infected" with the signature.
  - scanner still down     -> nothing changes; the next hour tries again.
"infected" folders are never touched. Nothing is deleted, with one exception: when a released file is an exact
duplicate (same sha256) of a file already in unsorted, app.place() drops the held copy, because its twin is
already there (no sidecar then). The folder and its WHY.txt stay as the record.
One run at a time: an exclusive flock on QUARANTINE/.rescan.lock; a second run exits quietly (exit 0).
Variables: counts (clean / infected / down / skipped / failed), lock (the open lock file), why_file / held / why
(one folder's note, its held file, the parsed note), name / digest / verdict / res (one release).
Exit code: 0, or 1 when any folder failed (named on stderr, so the cron log shows it); one summary line.
OUTCOME 2026-11-21 (T0962 follow-up): lock, failed count and exit code added after the doc review.
"""
from __future__ import annotations

import fcntl
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
    why_file = folder / "WHY.txt"
    held = [p for p in folder.iterdir() if p.suffix == ".held" and p.is_file()]
    try:
        why = json.loads(why_file.read_text())
    except (OSError, ValueError):
        counts["skipped"] += 1
        return
    if why.get("state") != "error" or len(held) != 1:
        counts["skipped"] += 1
        return
    state, detail = scan.scan(held[0])
    if state == "error":
        counts["down"] += 1
        return
    if state == "infected":
        why.update(state="infected", detail=detail, rescanned=time.strftime("%F %T"))
        why_file.write_text(json.dumps(why, indent=1))
        counts["infected"] += 1
        return
    # clean (or "skipped": too big to stream, which an upload would have filed as before)
    name = held[0].name[:-len(".held")] or "file"
    digest = app.sha256(held[0])
    verdict = {"kind": "unknown", "person": None, "dest": None,
               "why": f"held while the virus check was down; re-scanned {state} at {time.strftime('%F %T')}"}
    res = app.place(held[0], app.UNSORTED, name, digest, verdict)
    if res["status"] == "filed":
        app.write_sidecar(res["path"], {**verdict, "source": "quarantine re-scan", "sha256": digest,   # R5
                                        "held_in": folder.name, "at": time.strftime("%F %T")})
    why.update(state="released", released_to=res["path"], released_status=res["status"],
               rescanned=time.strftime("%F %T"))
    why_file.write_text(json.dumps(why, indent=1))
    counts["clean"] += 1


if __name__ == "__main__":
    c = main()
    print(f"rescan_held: released {c['clean']}, now infected {c['infected']}, scanner down {c['down']}, "
          f"left alone {c['skipped']}, failed {c['failed']}")
    sys.exit(1 if c["failed"] else 0)
