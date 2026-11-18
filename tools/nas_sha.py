#!/usr/bin/env python3
"""nas_sha.py - T1014: full SHA-256 of a list of files, run ON the Synology NAS (Python 3.8). Read-only.

Run (NAS, detached):  nohup python3 ~/t1014/nas_sha.py ~/t1014/list.jsonl ~/t1014/sha.jsonl > ~/t1014/sha.log 2>&1 &
list.jsonl: one JSON string per line, a /volume1/... path (written by unify_photos.py nas-list on the PC).
sha.jsonl:  one JSON object per line {"p": path, "s": size, "h": sha256 or "ERROR:<reason>"}; appended as it goes,
            so a rerun skips paths already done.
Why on the NAS: over SMB from the PC only ~4 files/s (tested 2026-11-17); local disk reads are many times faster.
OUTCOME 2026-11-17: written; first run started the same day (see ~/t1014/sha.log on the NAS).
"""
import hashlib
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor


def sha(p):
    """(path, size, sha256 hex) or (path, -1, 'ERROR:<reason>')."""
    h = hashlib.sha256()
    try:
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(4 << 20), b""):
                h.update(chunk)
        return p, os.path.getsize(p), h.hexdigest()
    except OSError as e:
        return p, -1, "ERROR:" + type(e).__name__


def main(src, dst):
    """Hash every path in src not yet in dst."""
    todo = [json.loads(l) for l in open(src, encoding="utf-8") if l.strip()]
    done = set()
    if os.path.exists(dst):
        for l in open(dst, encoding="utf-8"):
            try:
                done.add(json.loads(l)["p"])
            except ValueError:
                pass  # half-written last line after a kill
    todo = [p for p in todo if p not in done]
    print(time.strftime("%F %T"), "to do:", len(todo), "files; already done:", len(done), flush=True)
    n, b, t0 = 0, 0, time.time()
    with open(dst, "a", encoding="utf-8") as out, ThreadPoolExecutor(2) as ex:
        for p, s, h in ex.map(sha, todo):
            out.write(json.dumps({"p": p, "s": s, "h": h}) + "\n")
            n += 1
            b += max(s, 0)
            if n % 1000 == 0:
                out.flush()
                el = max(time.time() - t0, 1)
                print(time.strftime("%F %T"), f"{n}/{len(todo)} files, {b / 1e9:.1f} GB, {b / 1e6 / el:.0f} MB/s",
                      flush=True)
    print(time.strftime("%F %T"), "done", flush=True)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
