#!/usr/bin/env python3
"""todo_renumber_pc.py - apply the 2026-11-18 to-do renumber map to the PC-side files that name T-numbers.

Usage: python todo_renumber_pc.py MAP.csv [--write]
Files: C:\\Jasons New Site\\www\\HANDOFF.md, todo-autopilot-task.md, CLAUDE.md, and Claude's memory folder
(C:\\Users\\USER\\.claude\\projects\\C--Jasons-New-Site-www\\memory\\*.md). One-pass rewrite of old ids (no
chains); backups to D:\\archive\\2026-11-18\\renumber\\ first. Dry run prints how many ids change per file.
OUTCOME: (2026-11-18) companion of the NUC todo_renumber.py.
"""
import csv
import re
import shutil
import sys
from pathlib import Path

FILES = [Path(r"C:\Jasons New Site\www\HANDOFF.md"), Path(r"C:\Jasons New Site\www\todo-autopilot-task.md"),
         Path(r"C:\Jasons New Site\www\CLAUDE.md")] + \
    sorted(Path(r"C:\Users\USER\.claude\projects\C--Jasons-New-Site-www\memory").glob("*.md"))
# Not after "_" or "pre-": those are folder/file names (_T1014-hold, todo_api.py.pre-T1017), which keep their
# old number. Lesson from the NUC run 11-18, where those had to be put back by hand.
REF = re.compile(r"(?<![A-Za-z0-9_])(?<!pre-)T(\d{4})(?![0-9])")


def main(map_csv, write):
    with open(map_csv, encoding="utf-8") as fh:
        mapping = {r["old"]: r["new"] for r in csv.DictReader(fh)}
    bak = Path(r"D:\archive\2026-11-18\renumber")
    for f in FILES:
        if not f.exists():
            continue
        text = f.read_text(encoding="utf-8")
        n = [0]

        def sub(m, n=n):  # bound now (B023); REF.sub calls it only for this file
            new = mapping.get("T" + m.group(1))
            if new and new != m.group(0):
                n[0] += 1
                return new
            return m.group(0)
        out = REF.sub(sub, text)
        print(f"{f.name!s:<60} {n[0]} ids")
        if write and n[0]:
            (bak / f.parent.name).mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, bak / f.parent.name / f.name)
            f.write_text(out, encoding="utf-8")
    print("written" if write else "dry run")


if __name__ == "__main__":
    main(sys.argv[1], "--write" in sys.argv)
