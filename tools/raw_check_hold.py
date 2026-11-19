#!/usr/bin/env python3
"""raw_check_hold.py - T0005: move the 57 'raw-check' RAWs that have a same-name JPG into the holding folder.

    python raw_check_hold.py list                     (PC) write raw-check-pairs.tsv from unify-plan.csv
    python3 raw_check_hold.py move PAIRS.tsv          (NAS) dry run: check each pair, change nothing
    python3 raw_check_hold.py move PAIRS.tsv --write  (NAS) move, write raw-check-hold-journal.tsv beside PAIRS

Why: Jason 2026-11-19: "remove Raw and NEF if I have a copy of the same file and name as a jpg".
Every raw-check row in unify-plan.csv has a same-name JPG (raw_match). Phase 2 moved that JPG into
P:\\YYYY\\MM, so its new path is that JPG's own plan row 'target'. A RAW moves ONLY if that JPG is found
at its new place. "Remove" = move to the holding folder used for every other dupe
(/volume1/Computer backup/_T1014-hold/Pictures/_T1014-raw-check), never a delete: Jason empties the
holding folder himself after the phone check.
Two halves because the PC's SMB login cannot read _T1014-raw-check (Permission denied, 2026-11-19);
the NAS user jay made that folder in phase 2 and can.
OUTCOME 2026-11-19: first --write used os.rename and failed all 57 with Errno 18 (Invalid cross-device
link): each share is its own btrfs subvolume, so the move must copy then delete (shutil.move). Some names
end in U+F00D (a Windows-illegal character Synology maps); the paths are kept exactly, never cleaned.
Undo: the journal lists original <TAB> hold for every moved file; mv each back.
"""
import csv
import os
import shutil
import sys
from pathlib import Path

PLAN = Path(r"D:\backups\photo-verify\unify-plan.csv")
PAIRS = Path(r"D:\backups\photo-verify\raw-check-pairs.tsv")
HOLD = "/volume1/Computer backup/_T1014-hold/Pictures/_T1014-raw-check"


def to_nas(unc: str) -> str:
    r"""Map \\192.168.1.68\Share\a\b to /volume1/Share/a/b."""
    parts = unc.lstrip("\\").split("\\")
    return "/volume1/" + "/".join(parts[1:])


def build_list() -> int:
    """PC half: one line per raw-check RAW: its place now and the same-name JPG's place now (NAS paths)."""
    with PLAN.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    target_of = {r["path"]: r["target"] for r in rows if r["target"]}
    n = 0
    with PAIRS.open("w", encoding="utf-8", newline="\n") as out:
        for r in rows:
            if r["action"] == "raw-check":
                jpg_now = target_of.get(r["raw_match"], r["raw_match"])  # an unmoved JPG keeps its old path
                out.write(f"{to_nas(r['target'])}\t{to_nas(jpg_now)}\n")
                n += 1
    print(f"{n} pairs -> {PAIRS}")
    return 0


def move(pairs: str, write: bool) -> int:
    """NAS half: move a RAW to the holding folder only when its JPG exists; never overwrite."""
    with open(pairs, encoding="utf-8") as fh:
        lines = [l.rstrip("\n").split("\t") for l in fh if l.strip()]
    ok, skip = [], []
    for raw, jpg in lines:
        hold = os.path.join(HOLD, os.path.basename(raw))
        if not os.path.exists(raw):
            skip.append((raw, "RAW missing"))
        elif not os.path.exists(jpg):
            skip.append((raw, "JPG missing: " + jpg))
        elif os.path.exists(hold):
            skip.append((raw, "already in hold"))
        else:
            ok.append((raw, hold))
    print(f"move {len(ok)}, skip {len(skip)}")
    for s in skip:
        print("SKIP", *s)
    if not write:
        return 0
    os.makedirs(HOLD, exist_ok=True)
    journal = os.path.join(os.path.dirname(os.path.abspath(pairs)), "raw-check-hold-journal.tsv")
    with open(journal, "a", encoding="utf-8") as j:
        for raw, hold in ok:
            try:
                shutil.move(raw, hold)  # copy + delete: Computer backup is its own subvolume
                j.write(f"{raw}\t{hold}\tok\n")
            except OSError as e:
                j.write(f"{raw}\t{hold}\terror: {e}\n")
                print("ERROR", raw, e)
    print("journal:", journal)
    return 0


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[:1] == ["list"]:
        sys.exit(build_list())
    if a[:1] == ["move"] and len(a) >= 2:
        sys.exit(move(a[1], "--write" in a))
    sys.exit(__doc__)
