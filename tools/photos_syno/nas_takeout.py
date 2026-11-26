#!/usr/bin/env python3
"""nas_takeout.py - Google Takeout check (Jason 11-24): are the Takeout photos duplicates of the library?
Runs ON the NAS (Python 3.8).

  python3 nas_takeout.py report INDEX.jsonl OPS.jsonl REPORT.txt   dry run: dup vs unique counts, plan for uniques
  python3 nas_takeout.py run    OPS.jsonl JOURNAL.jsonl             reflink-copy the unique ones into Library
Takeout photos today: none are left in the share folders (Pictures/Takeout-* hold only JSON sidecars and other
non-photo files); the 11-17 unify put every Takeout photo/video in the hold area
(/volume1/Computer backup/_T1014-hold/{Pictures,photo}/Takeout-*). A Takeout photo is a DUPLICATE when its SHA-256
is in Photos/Library or Photos/MobileBackup, else UNIQUE.
Unique ones go to Library/YYYY/MM: date from the Takeout JSON sidecar (photoTakenTime.timestamp; looked up next to
the file in the hold area and at its original share path), then EXIF, then file name, then mtime. They are copied
with cp --reflink=always -p (no extra space, the hold copy stays), never overwriting (" (2)").
JSON sidecars are never deleted by this job (they stay where they are).
OUTCOME 2026-11-24: written for the coordinator's Takeout check.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from photo_meta import exif_date, kind, name_date  # noqa: E402

HOLD = "/volume1/Computer backup/_T1014-hold/"
LIB = "/volume1/homes/jay/Photos/Library/"
MB = "/volume1/homes/jay/Photos/MobileBackup/"


def sidecar_date(p):
    """photoTakenTime from a Takeout JSON next to p (hold copy or original share path), or None."""
    rel = p[len(HOLD):]  # Pictures/Takeout-Library/... or photo/Takeout-Library/...
    dirs = [os.path.dirname(p), os.path.join("/volume1", os.path.dirname(rel))]
    name = os.path.basename(p)
    cands = [name + ".json", name + ".supplemental-metadata.json", os.path.splitext(name)[0] + ".json"]
    for d in dirs:
        for c in cands:
            j = os.path.join(d, c)
            if os.path.isfile(j):
                try:
                    data = json.load(open(j, encoding="utf-8"))
                    ts = int(((data.get("photoTakenTime") or {}).get("timestamp")) or 0)
                    if ts > 0:
                        return datetime.fromtimestamp(ts), "takeout-json"
                except (ValueError, OSError):
                    pass
    return None


def date_of(p):
    r = sidecar_date(p)
    if r:
        return r
    name = os.path.basename(p)
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    d = exif_date(p, ext) or (exif_date(p, ext, 512 * 1024) if kind(ext) == "raw" else None)
    if d:
        return d, "exif"
    d = name_date(name)
    if d:
        return d, "name"
    return datetime.fromtimestamp(os.stat(p).st_mtime), "file-time"


def report(index_path, ops_path, report_path):
    latest = {}
    for l in open(index_path, encoding="utf-8"):
        try:
            j = json.loads(l)
            latest[j["p"]] = j
        except (ValueError, KeyError):
            pass
    lib_h = {j["h"] for p, j in latest.items() if p.startswith((LIB, MB)) and os.path.exists(p)}
    tk = [j for p, j in latest.items() if p.startswith(HOLD) and "/Takeout-" in p and os.path.exists(p)
          and kind(p.rsplit(".", 1)[-1].lower()) != "other" and not j["h"].startswith("ERROR")]
    dup = [j for j in tk if j["h"] in lib_h]
    uniq, seen = [], set()
    for j in sorted(tk, key=lambda j: j["p"]):
        if j["h"] not in lib_h and j["h"] not in seen:
            seen.add(j["h"])
            uniq.append(j)
    ops, taken, by_src = [], set(), {}
    for j in uniq:
        d, src = date_of(j["p"])
        want = os.path.join(LIB, f"{d.year:04d}", f"{d.month:02d}", os.path.basename(j["p"]))
        dst, i = want, 2
        base, ext = os.path.splitext(want)
        while dst in taken or os.path.lexists(dst):
            dst = f"{base} ({i}){ext}"
            i += 1
        taken.add(dst)
        by_src[src] = by_src.get(src, 0) + 1
        ops.append({"n": len(ops) + 1, "src": j["p"], "dst": dst, "size": j["s"], "h": j["h"], "date_from": src})
    with open(ops_path, "w", encoding="utf-8") as f:
        for o in ops:
            f.write(json.dumps(o, ensure_ascii=False) + "\n")
    lines = [f"Takeout check {time.strftime('%F %T')} (dry run, nothing changed)",
             "Takeout share folders (Pictures/Takeout-*, photo/Takeout-*): 0 photos/videos left (only JSON/other)",
             f"Takeout photos/videos in the hold area: {len(tk)} ({sum(j['s'] for j in tk) / 1e9:.1f} GB)",
             f"  duplicates of Library/MobileBackup (same sha256): {len(dup)} ({sum(j['s'] for j in dup) / 1e9:.1f} GB)",
             f"  unique (no copy in Library/MobileBackup): {len(tk) - len(dup)} files, {len(uniq)} distinct",
             f"  -> reflink-copy into Library/YYYY/MM: {len(ops)} ({sum(o['size'] for o in ops) / 1e9:.2f} GB); "
             "date from " + ", ".join(f"{k} {v}" for k, v in sorted(by_src.items()))]
    lines += [f"  e.g. {o['src']} -> {o['dst']}" for o in ops[:20]]
    open(report_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[:6]))


def run(ops_path, journal_path):
    done = set()
    if os.path.exists(journal_path):
        for l in open(journal_path, encoding="utf-8"):
            try:
                j = json.loads(l)
                if j.get("ok"):
                    done.add(j["n"])
            except ValueError:
                pass
    ok = bad = 0
    with open(journal_path, "a", encoding="utf-8") as jf:
        for l in open(ops_path, encoding="utf-8"):
            o = json.loads(l)
            if o["n"] in done:
                continue
            rec = {"n": o["n"], "src": o["src"], "dst": o["dst"], "h": o["h"]}
            try:
                if os.path.lexists(o["dst"]):
                    raise RuntimeError("dst exists")
                os.makedirs(os.path.dirname(o["dst"]), exist_ok=True)
                r = subprocess.run(["cp", "--reflink=always", "-p", "-n", "--", o["src"], o["dst"]],
                                   capture_output=True, text=True)
                if r.returncode != 0 or os.path.getsize(o["dst"]) != o["size"]:
                    raise RuntimeError("copy failed " + r.stderr.strip()[:150])
                rec["ok"] = True
                ok += 1
            except (OSError, RuntimeError) as e:
                rec["ok"] = False
                rec["err"] = str(e)[:200]
                bad += 1
            jf.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.sync()
    print(time.strftime("%F %T"), f"takeout copies: ok {ok}, errors {bad}")


if __name__ == "__main__":
    a = sys.argv
    if a[1] == "report":
        report(a[2], a[3], a[4])
    elif a[1] == "run":
        run(a[2], a[3])
