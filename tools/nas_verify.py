#!/usr/bin/env python3
"""nas_verify.py - T1014 phase 2 check, run ON the NAS (Python 3.8). Read-only.

Run (NAS):  python3 ~/t1014/nas_verify.py
1) every ok line of journal-1-place.jsonl: the file is at dst.
2) 300 random duplicate lines of journal-2-hold.jsonl: the held copy and the copy that stays (ops 'need') have the
   same SHA-256.
3) totals: files and GB per share (no @eaDir/#recycle) and in the holding folder.
4) 20 photos for Jason's phone spot check, spread over the kinds of move.
OUTCOME 2026-11-17: written for T1014 phase 2.
HISTORICAL 2026-11-24: the library moved from /volume1/Pictures to /volume1/homes/jay/Photos/Library; the
11-24 checks are photos_syno/nas_move.py verify and photos_syno/nas_dedupe.py verify.
"""
import hashlib
import json
import os
import random
from pathlib import Path

T = os.path.expanduser("~/t1014/")
HOLD = "/volume1/Computer backup/_T1014-hold"


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(4 << 20), b""):
            h.update(c)
    return h.hexdigest()


def last(journal):
    out = {}
    with open(T + journal, encoding="utf-8") as fh:
        for l in fh:
            d = json.loads(l)
            out[d["n"]] = d
    return out


def tree(root):
    n = b = 0
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in ("@eaDir", "#recycle", "#snapshot")]
        for f in fns:
            n += 1
            b += os.path.getsize(os.path.join(dp, f))
    return n, b


def main():
    place = last("journal-1-place.jsonl")
    gone = [d["dst"] for d in place.values() if d.get("ok") and not os.path.exists(d["dst"])]
    print("place: ok", sum(1 for d in place.values() if d.get("ok")), "missing at dst:", len(gone))
    with open(T + "ops-2-hold.jsonl", encoding="utf-8") as fh_:
        ops = {o["n"]: o for o in map(json.loads, fh_)}
    hold = last("journal-2-hold.jsonl")
    dups = [d for d in hold.values() if d.get("ok") and d["act"] == "duplicate"]
    random.seed(1014)
    def kept(p):  # a kept RAW may itself have gone to hold later (raw-with-jpg)
        return p if os.path.exists(p) else HOLD + p[len("/volume1"):]

    bad = [d["dst"] for d in random.sample(dups, 300) if sha(d["dst"]) != sha(kept(ops[d["n"]]["need"]))]
    print("hold: 300 random duplicates re-hashed, mismatches:", len(bad), bad[:3])
    for name, root in (("Pictures", "/volume1/Pictures"), ("photo", "/volume1/photo"), ("hold", HOLD)):
        n, b = tree(root)
        print(f"after: {name} {n} files {b / 1e9:.1f} GB")
    ok = [d for d in place.values() if d.get("ok") and d["dst"].lower().endswith((".jpg", ".jpeg", ".heic", ".png"))]
    picks = ([d for d in ok if "/undated/" in d["dst"]], [d for d in ok if d["act"] == "move-from-Z"],
             [d for d in ok if "/undated/" not in d["dst"] and d["act"] == "relocate-in-P"])
    conv = [o for o in ops.values() if o["act"] == "raw-convert" and os.path.exists(o["need"])]
    out = random.sample(picks[0], 4) + picks[1][:2] + random.sample(picks[2], 11)
    lines = [f"{d['dst']}   (was {d['src']})" for d in out]
    lines += [f"{o['need']}   (made from RAW {o['src']})" for o in random.sample(conv, 3)]
    Path(T + "spot-check-20.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("spot check list:", len(lines), "->", T + "spot-check-20.txt")


if __name__ == "__main__":
    main()
