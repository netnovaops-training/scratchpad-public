#!/usr/bin/env python3
"""nas_index.py - SHA-256 index of the photo areas, run ON the Synology NAS (Python 3.8). Read-only on the photos.

Run (NAS, detached):  nohup python3 ~/photos_syno/nas_index.py ~/photos_syno/index.jsonl > ~/photos_syno/index.log 2>&1 &
Roots: /volume1/Pictures, /volume1/homes/jay/Photos, /volume1/Computer backup/_T1014-hold
       (skips #recycle, @eaDir, .SynologyWorkingDirectory, symlinks).
Output: one JSON per line {"p", "s", "m" (mtime int), "h" (sha256 or "ERROR:..."), "how": "reuse"|"hash"};
        appended as it goes, a rerun skips a path already indexed with the same size+mtime.
Reuse rule (plan step 4): the 11-17 hash (~/t1014/sha.jsonl) is reused when the file's 11-17 path is known
(itself, or the src of an ok line in the 11-17 move journals), the size matches, and the mtime is older than the
start of that hashing run (2026-11-17 14:02:39 EST): content not modified since it was hashed. Else hash fresh.
OUTCOME 2026-11-24: written for steps 3-4 of plan functional-greeting-horizon.
"""
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

T1014 = "/var/services/homes/jay/t1014"
ROOTS = ["/volume1/Pictures", "/volume1/homes/jay/Photos", "/volume1/Computer backup/_T1014-hold"]
SKIP_DIRS = {"#recycle", "@eaDir", ".SynologyWorkingDirectory"}
CUTOFF = time.mktime(time.strptime("2026-11-17 14:02:39", "%Y-%m-%d %H:%M:%S"))  # NAS local time (EST)


def walk():
    """Yield (path, size, mtime) of every regular file under ROOTS."""
    for root in ROOTS:
        for d, dirs, files in os.walk(root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in files:
                p = os.path.join(d, f)
                try:
                    st = os.lstat(p)
                except OSError:
                    continue
                if os.path.isfile(p) and not os.path.islink(p):
                    yield p, st.st_size, int(st.st_mtime)


def old_hashes():
    """(path -> (size, sha) from 11-17, current path -> 11-17 path) maps."""
    old = {}
    for l in open(os.path.join(T1014, "sha.jsonl"), encoding="utf-8"):
        try:
            j = json.loads(l)
        except ValueError:
            continue
        if not str(j["h"]).startswith("ERROR"):
            old[j["p"]] = (j["s"], j["h"])
    moved = {}
    for name in ("journal-1-place.jsonl", "journal-2-hold.jsonl"):
        for l in open(os.path.join(T1014, name), encoding="utf-8"):
            try:
                j = json.loads(l)
            except ValueError:
                continue
            if j.get("ok"):
                moved[j["dst"]] = j["src"]
    for l in open(os.path.join(T1014, "raw-check-hold-journal.tsv"), encoding="utf-8"):
        parts = l.rstrip("\n").split("\t")
        if len(parts) == 3 and parts[2] == "ok":
            # raw-check files went Pictures/X -> _T1014-raw-check (place) -> hold; follow both hops
            moved[parts[1]] = moved.get(parts[0], parts[0])
    return old, moved


def sha(item):
    p, s, m = item
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(4 << 20), b""):
                h.update(chunk)
        return p, s, m, h.hexdigest()
    except OSError as e:
        return p, s, m, "ERROR:" + type(e).__name__


def main(dst):
    done = set()
    if os.path.exists(dst):
        for l in open(dst, encoding="utf-8"):
            try:
                j = json.loads(l)
                done.add((j["p"], j["s"], j["m"]))
            except (ValueError, KeyError):
                pass
    old, moved = old_hashes()
    # files moved by nas_move.py (step 3): reuse this index's own hash of the source path when size AND mtime
    # are unchanged (rename and cp --reflink -p keep both)
    prev, moved_now = {}, {}
    if os.path.exists(dst):
        for l in open(dst, encoding="utf-8"):
            try:
                j = json.loads(l)
                if not j["h"].startswith("ERROR"):
                    prev[j["p"]] = (j["s"], j["m"], j["h"])
            except (ValueError, KeyError):
                pass
    mj = os.path.join(os.path.dirname(os.path.abspath(dst)), "move-journal.jsonl")
    if os.path.exists(mj):
        for l in open(mj, encoding="utf-8"):
            try:
                j = json.loads(l)
                if j.get("ok"):
                    moved_now[j["dst"]] = j["src"]
            except ValueError:
                pass
    files = list(walk())
    print(time.strftime("%F %T"), "files:", len(files), "already indexed:", len(done), flush=True)
    todo, n_reuse = [], 0
    with open(dst, "a", encoding="utf-8") as out:
        for p, s, m in files:
            if (p, s, m) in done:
                continue
            pv = prev.get(moved_now.get(p, ""))
            if pv and pv[0] == s and pv[1] == m:
                out.write(json.dumps({"p": p, "s": s, "m": m, "h": pv[2], "how": "reuse-moved"}) + "\n")
                n_reuse += 1
                continue
            src = moved.get(p, p)
            o = old.get(src)
            if o and o[0] == s and m < CUTOFF:
                out.write(json.dumps({"p": p, "s": s, "m": m, "h": o[1], "how": "reuse"}) + "\n")
                n_reuse += 1
            else:
                todo.append((p, s, m))
        out.flush()
        tb = sum(t[1] for t in todo)
        print(time.strftime("%F %T"), f"reused {n_reuse}; to hash {len(todo)} files, {tb / 1e9:.1f} GB", flush=True)
        n, b, t0 = 0, 0, time.time()
        with ThreadPoolExecutor(2) as ex:
            for p, s, m, h in ex.map(sha, todo):
                out.write(json.dumps({"p": p, "s": s, "m": m, "h": h, "how": "hash"}) + "\n")
                n += 1
                b += s
                if n % 1000 == 0:
                    out.flush()
                    el = max(time.time() - t0, 1)
                    print(time.strftime("%F %T"), f"{n}/{len(todo)} files, {b / 1e9:.1f}/{tb / 1e9:.1f} GB, "
                          f"{b / 1e6 / el:.0f} MB/s", flush=True)
    print(time.strftime("%F %T"), "done", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
