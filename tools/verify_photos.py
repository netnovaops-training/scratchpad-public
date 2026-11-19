#!/usr/bin/env python3
"""verify_photos.py - prove which files on M:\\Pictures already have an identical copy on the NAS. Deletes nothing.

Run (PC):   py -3 -I D:\\scratchpad\\tools\\verify_photos.py
Writes:     D:\\backups\\photo-verify\\report.csv   one row per M: file: path, size, sha256, nas_copy (or NO COPY)
            D:\\backups\\photo-verify\\summary.txt  counts and GB: verified copies vs files with no NAS copy
            D:\\backups\\photo-verify\\progress.txt updated as it runs
How it matches: by CONTENT, anywhere under \\\\192.168.1.68\\Pictures (the NAS folders are laid out differently
from M:). A NAS file is a candidate only if its size is identical; then both files are SHA-256 hashed and must match.
Skipped on the NAS: #recycle, #snapshot, .Synology*, @eaDir (Synology system folders).
Why: Jason, 2026-11-17: keep the NAS copy, remove M: copies that are VERIFIED exact copies. This script is only
the proof; deleting is a separate, confirmed step that reads report.csv.
OUTCOME 2026-11-17: first run started in the background.
"""
import csv
import hashlib
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

M = Path(r"M:\Pictures")
NAS = Path(r"\\192.168.1.68\Pictures")
OUT = Path(r"D:\backups\photo-verify")
SKIP = {"#recycle", "#snapshot", "@eaDir", ".SynologyWorkingDirectory", ".tmp.drivedownload"}


def sha(p, cache={}):  # noqa: B006  the shared dict IS the cache, on purpose
    """SHA-256 of a file, cached by path."""
    if p in cache:
        return cache[p]
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    cache[p] = h.hexdigest()
    return cache[p]


def walk(root, skip=()):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in skip and not d.startswith(".Synology")]
        for fn in fns:
            p = os.path.join(dp, fn)
            try:
                yield p, os.path.getsize(p)
            except OSError:
                pass


def say(msg):
    (OUT / "progress.txt").write_text(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n", encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    say("indexing NAS sizes")
    by_size = defaultdict(list)
    n_nas = 0
    for p, s in walk(NAS, SKIP):
        by_size[s].append(p)
        n_nas += 1
        if n_nas % 20000 == 0:
            say(f"indexing NAS sizes: {n_nas} files")
    m_files = list(walk(M))
    total = len(m_files)
    have = miss = 0
    gb_have = gb_miss = 0
    with open(OUT / "report.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["m_path", "size", "sha256", "nas_copy"])
        for i, (p, s) in enumerate(m_files, 1):
            match = ""
            try:
                cands = by_size.get(s, [])
                if cands and s > 0:
                    h = sha(p)
                    for c in cands:
                        try:
                            if sha(c) == h:
                                match = c
                                break
                        except OSError:
                            continue
                else:
                    h = ""
            except OSError:
                h, match = "", ""
            if match:
                have += 1; gb_have += s
            else:
                miss += 1; gb_miss += s
            w.writerow([p, s, h, match or "NO COPY"])
            if i % 500 == 0:
                say(f"{i}/{total} M: files checked; {have} verified on the NAS, {miss} with no copy")
    summary = (f"M:\\Pictures files: {total}\nNAS files indexed: {n_nas}\n"
               f"VERIFIED identical copy on the NAS: {have} files, {gb_have / 1e9:.1f} GB\n"
               f"NO identical copy on the NAS: {miss} files, {gb_miss / 1e9:.1f} GB\n"
               f"Report: {OUT / 'report.csv'}\n")
    (OUT / "summary.txt").write_text(summary, encoding="utf-8")
    say("done")
    print(summary)


if __name__ == "__main__":
    sys.exit(main())
