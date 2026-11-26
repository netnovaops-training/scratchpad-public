#!/usr/bin/env python3
"""nas_move.py - plan step 3: move the photo library from the Pictures share into Jason's personal space.
Runs ON the Synology NAS (Python 3.8) as the DSM user. Never overwrites, never deletes data.

  python3 nas_move.py plan  OPS.jsonl            dry run: writes the op list, prints count / bytes / clashes
  python3 nas_move.py run   OPS.jsonl JOURNAL.jsonl --batch 2019    carry out the ops of one batch (a year
                                                 folder, or "From-M-Pictures"); restartable (journal)
  python3 nas_move.py verify OPS.jsonl JOURNAL.jsonl INDEX.jsonl [N]   count/bytes at dst + N random SHA-256
                                                 (default 300) against the pre-move index
What moves: /volume1/Pictures/<YYYY>/... -> /volume1/homes/jay/Photos/Library/<YYYY>/... (same sub-path;
the 11-17 unify already filed them as YYYY/MM or YYYY/undated). Not moved: folders starting with _ # . and
@eaDir (Synology thumbnails, rebuilt by Photos), Thumbs.db / desktop.ini / .DS_Store.
/volume1/Pictures/From-M-Pictures (coordinator 11-24): photo/video files only, filed by date like 11-17
(EXIF, then file name, then file mtime) to Library/YYYY/MM; its non-photo files (mp3, xmp) stay.
Name clash at the destination: " (2)", " (3)" ... before the extension; never overwritten.
Move: os.rename; Pictures and homes are separate btrfs subvolumes, so EXDEV falls back to
cp --reflink=always -p (shares the data blocks, no copy; tested 11-17), size check, then unlink the source.
OUTCOME 2026-11-24: written for plan functional-greeting-horizon step 3.
"""
import errno
import json
import os
import random
import re
import subprocess
import sys
import time
import hashlib
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from photo_meta import exif_date, name_date, kind  # noqa: E402

SRC = "/volume1/Pictures"
DST = "/volume1/homes/jay/Photos/Library"
SKIP_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}
YEAR = re.compile(r"^\d{4}$")


def unique(dst, taken):
    """dst, or 'name (2).ext' ... so nothing existing (on disk or planned) is overwritten."""
    if dst not in taken and not os.path.lexists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 2
    while True:
        c = f"{base} ({i}){ext}"
        if c not in taken and not os.path.lexists(c):
            return c
        i += 1


def file_date(p):
    """(datetime, source) by the 11-17 rules: EXIF, then file name, then file mtime."""
    name = os.path.basename(p)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    d = exif_date(p, ext)
    if d is None and kind(ext) == "raw":
        d = exif_date(p, ext, 512 * 1024)
    if d:
        return d, "exif"
    d = name_date(name)
    if d:
        return d, "name"
    return datetime.fromtimestamp(os.stat(p).st_mtime), "file-time"


def plan(ops_path):
    ops, taken, clashes = [], set(), 0
    tops = sorted(x for x in os.listdir(SRC) if YEAR.match(x) and os.path.isdir(os.path.join(SRC, x)))
    for top in tops + ["From-M-Pictures"]:
        root = os.path.join(SRC, top)
        if not os.path.isdir(root):
            continue
        for d, dirs, files in os.walk(root):
            dirs[:] = sorted(x for x in dirs if not x.startswith(("_", "#", ".", "@")))
            for f in sorted(files):
                if f.lower() in SKIP_FILES:
                    continue
                p = os.path.join(d, f)
                if os.path.islink(p) or not os.path.isfile(p):
                    continue
                how = ""
                if top == "From-M-Pictures":
                    ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
                    if kind(ext) == "other":
                        continue
                    dt, how = file_date(p)
                    want = os.path.join(DST, f"{dt.year:04d}", f"{dt.month:02d}", f)
                else:
                    want = os.path.join(DST, os.path.relpath(p, SRC))
                dst = unique(want, taken)
                clashes += dst != want
                taken.add(dst)
                ops.append({"n": len(ops) + 1, "batch": top, "src": p, "dst": dst, "size": os.path.getsize(p),
                            "date_from": how, "clash": dst != want})
    with open(ops_path, "w", encoding="utf-8") as out:
        for o in ops:
            out.write(json.dumps(o, ensure_ascii=False) + "\n")
    by = {}
    for o in ops:
        b = by.setdefault(o["batch"], [0, 0])
        b[0] += 1
        b[1] += o["size"]
    print(f"plan: {len(ops)} files, {sum(o['size'] for o in ops)} bytes "
          f"({sum(o['size'] for o in ops) / 1e9:.1f} GB), name clashes renamed: {clashes}")
    for k, (n, b) in by.items():
        print(f"  {k}: {n} files {b / 1e9:.2f} GB")


def load(path):
    out = []
    if os.path.exists(path):
        for l in open(path, encoding="utf-8"):
            try:
                out.append(json.loads(l))
            except ValueError:
                pass  # half-written last line after a kill
    return out


def move(src, dst):
    """Move one file without overwriting; returns 'rename' or 'reflink'."""
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.lexists(dst):
        raise RuntimeError("dst exists")
    try:
        os.rename(src, dst)  # same subvolume only; never reached for an existing dst (checked above)
        return "rename"
    except OSError as e:
        if e.errno != errno.EXDEV:
            raise
    r = subprocess.run(["cp", "--reflink=always", "-p", "-n", "--", src, dst], capture_output=True, text=True)
    if r.returncode != 0:  # source untouched; any partial dst is left for inspection
        raise RuntimeError("cp failed: " + r.stderr.strip()[:200])
    if os.path.getsize(dst) != os.path.getsize(src):
        raise RuntimeError("size mismatch after reflink")
    os.unlink(src)
    return "reflink"


def run(ops_path, journal_path, batch):
    done = {j["n"] for j in load(journal_path) if j.get("ok")}
    ops = [o for o in load(ops_path) if o["batch"] == batch and o["n"] not in done]
    print(time.strftime("%F %T"), f"batch {batch}: {len(ops)} ops to do", flush=True)
    ok = bad = 0
    with open(journal_path, "a", encoding="utf-8") as j:
        for o in ops:
            rec = {"n": o["n"], "src": o["src"], "dst": o["dst"]}
            try:
                if not os.path.isfile(o["src"]) or os.path.getsize(o["src"]) != o["size"]:
                    raise RuntimeError("source missing or size changed")
                rec["how"] = move(o["src"], o["dst"])
                rec["ok"] = True
                ok += 1
            except (OSError, RuntimeError) as e:
                rec["ok"] = False
                rec["err"] = str(e)[:200]
                bad += 1
            j.write(json.dumps(rec, ensure_ascii=False) + "\n")
            if (ok + bad) % 2000 == 0:
                j.flush()
    os.sync()
    print(time.strftime("%F %T"), f"batch {batch}: ok {ok}, errors {bad}", flush=True)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(ops_path, journal_path, index_path, n_sample=300):
    ops = {o["n"]: o for o in load(ops_path)}
    okj = [j for j in load(journal_path) if j.get("ok")]
    missing, wrong, nbytes = 0, 0, 0
    for j in okj:
        o = ops[j["n"]]
        try:
            s = os.path.getsize(o["dst"])
        except OSError:
            missing += 1
            continue
        nbytes += s
        wrong += s != o["size"]
    left = sum(1 for j in okj if os.path.lexists(ops[j["n"]]["src"]))
    print(f"sources still present after an ok move (should be 0): {left}")
    planned = sum(o["size"] for o in ops.values())
    print(f"ops {len(ops)} planned {planned} bytes; journaled ok {len(okj)}; at dst {len(okj) - missing} "
          f"files {nbytes} bytes; missing at dst {missing}; size mismatches {wrong}")
    idx = {}
    for l in open(index_path, encoding="utf-8"):
        try:
            r = json.loads(l)
            idx[r["p"]] = r["h"]
        except (ValueError, KeyError):
            pass
    pool = [j for j in okj if ops[j["n"]]["src"] in idx and not idx[ops[j["n"]]["src"]].startswith("ERROR")]
    sample = random.sample(pool, min(n_sample, len(pool)))
    bad = [ops[j["n"]]["dst"] for j in sample if sha(ops[j["n"]]["dst"]) != idx[ops[j["n"]]["src"]]]
    print(f"sha256 sample: {len(sample)} checked against the pre-move index, mismatches {len(bad)}")
    for b in bad[:20]:
        print("  MISMATCH", b)


if __name__ == "__main__":
    a = sys.argv
    if a[1] == "plan":
        plan(a[2])
    elif a[1] == "run":
        run(a[2], a[3], a[a.index("--batch") + 1])
    elif a[1] == "verify":
        verify(a[2], a[3], a[4], int(a[5]) if len(a) > 5 else 300)
