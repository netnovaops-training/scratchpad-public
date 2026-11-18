#!/usr/bin/env python3
"""unify_ops.py - T1014 phase 2: turn unify-plan.csv into the NAS move list (ops.jsonl). Changes no file.

Run:    python D:\\scratchpad\\tools\\unify_ops.py
Reads:  D:\\backups\\photo-verify\\unify-plan.csv
Writes: D:\\backups\\photo-verify\\unify\\ops-1-place.jsonl   keepers to their new home (P:\\YYYY\\MM, YYYY\\undated,
                                                          _T1014-raw-check); run first
        D:\\backups\\photo-verify\\unify\\ops-2-hold.jsonl    dupes, RAW-with-JPG, converted RAWs, Z: sidecars to
                                                          the holding folder; run after the RAW->JPG conversion
One JSON object per line: {"n", "act", "src", "dst", "size", "need", "need_size"} with /volume1 paths.
'need' is the file that must exist (with need_size bytes) before src may leave: the keeper's FINAL path for a
duplicate, the JPG's final path for a RAW. nas_move.py refuses the op if it is not there.
Holding folder (Jason 11-17, instead of #recycle, because DSM's 'Empty Recycle' task empties #recycle twice a
week): /volume1/Computer backup/_T1014-hold/<share>/<original path>. Outside the photo shares, so Synology
Photos never indexes it; same volume, so nas_move.py moves by reflink (no data copied).
M: duplicates are not in the list: M:\\Pictures is cleared by Jason later, after the phone spot-check.
OUTCOME 2026-11-17: written for T1014 phase 2.
"""
import csv
import json
import os
from pathlib import Path

from unify_photos import WORK, to_nas

PLAN = Path(r"D:\backups\photo-verify\unify-plan.csv")
HOLD = "/volume1/Computer backup/_T1014-hold"
PLACE_ACTS = ("relocate-in-P", "move-from-Z", "raw-check")


def hold_path(nas_path):
    """/volume1/<share>/a/b.jpg -> HOLD/<share>/a/b.jpg."""
    return HOLD + nas_path[len("/volume1"):]


def build(rows):
    """(place ops, hold ops) from plan rows."""
    by_path = {r["path"]: r for r in rows}
    final = {}  # keeper path -> where it ends up
    for r in rows:
        if r["action"] in PLACE_ACTS:
            final[r["path"]] = r["target"]
        elif r["action"] in ("keep", "raw-convert", "raw-with-jpg"):
            final[r["path"]] = r["path"]

    def resolve(p):
        """Final path of the kept copy of p (p itself, or its keeper if p is a duplicate)."""
        r = by_path.get(p)
        if r and r["action"] == "duplicate":
            p = r["keep_path"]
        return final.get(p), int(by_path[p]["size_bytes"]) if p in by_path else None

    place, hold, problems = [], [], []
    for r in rows:
        a, src = r["action"], r["path"]
        if r["src"] == "M":
            continue
        op = {"act": a, "src": to_nas(src), "size": int(r["size_bytes"]), "need": None, "need_size": None}
        if a in PLACE_ACTS:
            place.append(dict(op, dst=to_nas(r["target"])))
            continue
        if a == "duplicate":
            need, ns = resolve(r["keep_path"])
        elif a == "raw-with-jpg":
            need, ns = resolve(r["raw_match"])
        elif a == "raw-convert":
            need, ns = r["target"], None  # the JPG made by raw_to_jpg.py; size unknown until made
        elif a == "not-photo" and r["src"] == "Z":
            need, ns = None, None
        else:
            continue
        if a != "not-photo" and (not need or not need.startswith("\\\\")):
            problems.append((a, src, need))
            continue
        hold.append(dict(op, dst=hold_path(op["src"]), need=to_nas(need) if need else None, need_size=ns))
    return place, hold, problems


def write(ops, path):
    """Numbered JSON lines."""
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for i, op in enumerate(ops, 1):
            f.write(json.dumps(dict(op, n=i), ensure_ascii=False) + "\n")


def main():
    with open(PLAN, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    place, hold, problems = build(rows)
    write(place, WORK / "ops-1-place.jsonl")
    write(hold, WORK / "ops-2-hold.jsonl")
    print(f"place {len(place)} ops, {sum(o['size'] for o in place) / 1e9:.1f} GB")
    print(f"hold  {len(hold)} ops, {sum(o['size'] for o in hold) / 1e9:.1f} GB")
    print(f"problems (left out): {len(problems)}")
    for p in problems[:10]:
        print("  ", p)
    dst = [o["dst"].lower() for o in place + hold]
    print("duplicate destinations:", len(dst) - len(set(dst)))


if __name__ == "__main__":
    main()
