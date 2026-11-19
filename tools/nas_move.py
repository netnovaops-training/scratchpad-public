#!/usr/bin/env python3
"""nas_move.py - T1014 phase 2: carry out a move list ON the Synology NAS (Python 3.8), journaled, restartable.

Run (NAS):  python3 ~/t1014/nas_move.py OPS.jsonl JOURNAL.jsonl [--minutes 15]
OPS.jsonl:  lines {"n", "act", "src", "dst", "size", "need", "need_size"} from unify_ops.py (/volume1 paths).
JOURNAL:    one line per finished op {"n", "src", "dst", "how", "ok": true} or {"n", ..., "ok": false, "err"};
            a rerun skips every op already journaled ok. nas_restore.py reverses it.
Each op: src must exist with its listed size, dst must not exist, and 'need' (the copy that stays) must exist
with need_size bytes; otherwise the op is refused and journaled as an error, nothing changes. Move = rename in
the same share; across shares (another btrfs subvolume) = cp --reflink=always -p (shares the data blocks, no
copy), size check, then the source name is removed. Never deletes data: every byte stays reachable at dst.
--minutes stops cleanly after that long (Jason gets a summary every 15 minutes).
OUTCOME 2026-11-17: reflink across shares tested by hand on the NAS (cp 8.30, works); written for T1014 phase 2.
"""
import errno
import json
import os
import subprocess
import sys
import time


def move(src, dst):
    """Move one file; returns 'rename' or 'reflink'. Raises OSError/RuntimeError on failure."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        os.rename(src, dst)
        return "rename"
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    subprocess.run(["cp", "--reflink=always", "-p", src, dst], check=True, capture_output=True)
    if os.path.getsize(dst) != os.path.getsize(src):
        raise RuntimeError("size differs after reflink copy")
    os.remove(src)
    return "reflink"


def check(op):
    """Reason the op may not run, or None."""
    if not os.path.exists(op["src"]):
        return "src missing"
    if op.get("size") is not None and os.path.getsize(op["src"]) != op["size"]:
        return "src size changed"
    if os.path.lexists(op["dst"]):
        return "dst exists"
    need = op.get("need")
    if need:
        if not os.path.exists(need):
            return "kept copy missing: " + need
        if op.get("need_size") is not None and os.path.getsize(need) != op["need_size"]:
            return "kept copy size differs"
    return None


def main(argv):
    ops_path, journal = argv[0], argv[1]
    minutes = float(argv[argv.index("--minutes") + 1]) if "--minutes" in argv else 1e9
    done = set()
    if os.path.exists(journal):
        with open(journal, encoding="utf-8") as fh:
            for l in fh:
                try:
                    d = json.loads(l)
                except ValueError:
                    continue
                if d.get("ok"):
                    done.add(d["n"])
    with open(ops_path, encoding="utf-8") as fh_:
        ops = [json.loads(l) for l in fh_ if l.strip()]
    todo = [o for o in ops if o["n"] not in done]
    print(time.strftime("%F %T"), os.path.basename(ops_path), "to do:", len(todo), "done before:", len(done),
          flush=True)
    t0, n, ok, bad, gb = time.time(), 0, 0, 0, 0
    with open(journal, "a", encoding="utf-8") as j:
        for op in todo:
            if time.time() - t0 > minutes * 60:
                print(time.strftime("%F %T"), "time limit reached", flush=True)
                break
            rec = {"n": op["n"], "act": op["act"], "src": op["src"], "dst": op["dst"]}
            why = check(op)
            if why is None:
                try:
                    rec.update(how=move(op["src"], op["dst"]), ok=True)
                    ok += 1
                    gb += op.get("size") or 0
                except (OSError, RuntimeError, subprocess.CalledProcessError) as e:
                    rec.update(ok=False, err=f"{type(e).__name__}: {e}"[:300])
                    bad += 1
            else:
                rec.update(ok=False, err=why)
                bad += 1
            j.write(json.dumps(rec, ensure_ascii=False) + "\n")
            j.flush()
            n += 1
            if n % 2000 == 0:
                print(time.strftime("%F %T"), f"{n}/{len(todo)} ok {ok} errors {bad} {gb / 1e9:.1f} GB", flush=True)
    left = len(todo) - n
    print(time.strftime("%F %T"), f"spurt end: ok {ok}, errors {bad}, {gb / 1e9:.1f} GB, left {left}",
          "| done" if left == 0 else "", flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
