#!/usr/bin/env python3
"""nas_dedupe.py - plan step 4: delete exact duplicates (same SHA-256) in the photo library. Runs ON the NAS.

  python3 nas_dedupe.py plan   INDEX.jsonl PLAN.jsonl REPORT.txt     dry run: nothing changes
  python3 nas_dedupe.py run    PLAN.jsonl LOG.jsonl --from N --count 5000   delete one batch (restartable)
  python3 nas_dedupe.py verify LOG.jsonl                             re-hash every kept twin named in the log
Scope (Jason's plan): Photos/Library, Photos/MobileBackup and the 11-17 hold area
(/volume1/Computer backup/_T1014-hold). Keepers are only ever in Library or MobileBackup.
Rule: a file is deleted only if another file with the SAME SHA-256 is kept in Library or MobileBackup.
Keep: every MobileBackup copy (never deleted: the phone app owns that folder) > Library (oldest mtime, then
shortest path) . Deleted: the other Library copies, and every hold-area copy, of a hash that has a keeper.
Amended 11-24 (PROTECT rule): MobileBackup is a live path (phone app), so it is protected and does not count as
the kept twin; a duplicate is deleted only when an unprotected Library copy stays. MobileBackup-only twins: both kept.
Not touched: anything outside the three areas (Pictures leftovers, loose files in Photos/, Photos/Nikon, ...),
empty files, hashes with no keeper, index ERROR lines.
Before each delete: the file must still have the indexed size+mtime (so the hash still describes it) and the
kept twin must exist with the same size; otherwise the file is skipped and logged.
OUTCOME 2026-11-24: written for plan functional-greeting-horizon step 4.
"""
import hashlib
import json
import os
import random
import sys
import time

LIB = "/volume1/homes/jay/Photos/Library/"
MB = "/volume1/homes/jay/Photos/MobileBackup/"
HOLD = "/volume1/Computer backup/_T1014-hold/"


# PROTECT list (coordinator 11-24, built from the live users on the NUC/PC, see step4-protect.txt): the phone
# backup folder is the only live path inside the dedupe scope; the other live NAS paths (photo/Immich, videos,
# private, Downloads, Paperless, Website backup, Computer backup/_T1018-drop, Pictures for Abyss) are outside it.
PROTECT = (MB, "/volume1/photo/", "/volume1/Pictures/", "/volume1/videos/", "/volume1/private/",
           "/volume1/Downloads/", "/volume1/Paperless/", "/volume1/Computer backup/_T1018-drop/")


def protected(p):
    return p.startswith(PROTECT)


def area(p):
    return "MB" if p.startswith(MB) else "LIB" if p.startswith(LIB) else "HOLD" if p.startswith(HOLD) else None


def load_index(path):
    """Latest index line per existing path in scope, whose size+mtime still match the disk."""
    idx = {}
    for l in open(path, encoding="utf-8"):
        try:
            j = json.loads(l)
        except ValueError:
            continue
        if area(j["p"]):
            idx[j["p"]] = j
    out = {}
    for p, j in idx.items():
        if j["h"].startswith("ERROR") or j["s"] == 0:
            continue
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_size == j["s"] and int(st.st_mtime) == j["m"]:
            out[p] = j
    return out


def plan(index_path, plan_path, report_path):
    idx = load_index(index_path)
    groups = {}
    for p, j in idx.items():
        groups.setdefault(j["h"], []).append(j)
    dels, keep_files, kept_both = [], 0, 0
    for h, js in groups.items():
        # PROTECT rule (coordinator 11-24): a protected file is never deleted, and a protected twin does not
        # count as "the kept twin": if the only twin is protected, both stay.
        lib = sorted((j for j in js if area(j["p"]) == "LIB" and not protected(j["p"])),
                     key=lambda j: (j["m"], len(j["p"]), j["p"]))
        hold = [j for j in js if area(j["p"]) == "HOLD" and not protected(j["p"])]
        if not lib:
            if hold and any(area(j["p"]) == "MB" for j in js):
                kept_both += len(hold)
            continue
        keeper, losers = lib[0], lib[1:] + hold
        keep_files += 1
        for j in losers:
            dels.append({"p": j["p"], "s": j["s"], "m": j["m"], "h": h, "area": area(j["p"]),
                         "keep": keeper["p"], "keep_s": keeper["s"]})
    dels.sort(key=lambda d: d["p"])
    with open(plan_path, "w", encoding="utf-8") as f:
        for i, d in enumerate(dels, 1):
            d["n"] = i
            f.write(json.dumps(d, ensure_ascii=False) + "\n")
    by = {}
    for d in dels:
        k = f"{d['area']} (twin kept in {area(d['keep'])})"
        n, b = by.get(k, (0, 0))
        by[k] = (n + 1, b + d["s"])
    tot = {}
    for j in idx.values():
        n, b = tot.get(area(j["p"]), (0, 0))
        tot[area(j["p"])] = (n + 1, b + j["s"])
    lines = [f"Step 4 dedupe dry run {time.strftime('%F %T')} (nothing deleted)",
             "indexed in scope: " + ", ".join(f"{a} {n} files {b / 1e9:.1f} GB" for a, (n, b) in sorted(tot.items())),
             f"to delete: {len(dels)} files, {sum(d['s'] for d in dels) / 1e9:.2f} GB"]
    lines += [f"  {k}: {n} files {b / 1e9:.2f} GB" for k, (n, b) in sorted(by.items())]
    lines.append(f"kept both (only twin is in protected MobileBackup): {kept_both} hold files")
    mb_lib = sum(1 for js in groups.values() if any(area(j["p"]) == "MB" for j in js)
                 and any(area(j["p"]) == "LIB" for j in js))
    lines.append(f"hashes present in both MobileBackup and Library (both kept): {mb_lib}")
    lines.append("50 random sample pairs (deleted  <=  kept twin, same sha256):")
    for d in random.sample(dels, min(50, len(dels))):
        lines.append(f"  DEL {d['p']}\n      KEEP {d['keep']}  [{d['h'][:12]} {d['s']} B]")
    open(report_path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[:5 + len(by)]))


def run(plan_path, log_path, start, count):
    done = set()
    if os.path.exists(log_path):
        for l in open(log_path, encoding="utf-8"):
            try:
                done.add(json.loads(l)["n"])
            except (ValueError, KeyError):
                pass
    todo = []
    for l in open(plan_path, encoding="utf-8"):
        d = json.loads(l)
        if start <= d["n"] < start + count and d["n"] not in done:
            todo.append(d)
    ok = skip = 0
    with open(log_path, "a", encoding="utf-8") as log:
        for d in todo:
            rec = {"n": d["n"], "p": d["p"], "s": d["s"], "h": d["h"], "keep": d["keep"]}
            try:
                st = os.stat(d["p"])
                if st.st_size != d["s"] or int(st.st_mtime) != d["m"]:
                    raise RuntimeError("changed since indexed")
                if os.path.getsize(d["keep"]) != d["keep_s"]:
                    raise RuntimeError("kept twin size changed")
                if os.path.samefile(d["p"], d["keep"]):
                    raise RuntimeError("same file")
                os.unlink(d["p"])
                rec["ok"] = True
                ok += 1
            except (OSError, RuntimeError) as e:
                rec["ok"] = False
                rec["err"] = str(e)[:200]
                skip += 1
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
    os.sync()
    print(time.strftime("%F %T"), f"batch from {start}: deleted {ok}, skipped {skip}", flush=True)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify(log_path):
    recs = [json.loads(l) for l in open(log_path, encoding="utf-8") if l.strip()]
    recs = [r for r in recs if r.get("ok")]
    want = {}
    for r in recs:
        want.setdefault(r["keep"], set()).add(r["h"])
    bad = []
    for k, hs in want.items():
        try:
            h = sha(k)
        except OSError as e:
            bad.append((k, "MISSING " + type(e).__name__))
            continue
        if hs != {h}:
            bad.append((k, "HASH MISMATCH"))
    still = sum(1 for r in recs if os.path.lexists(r["p"]))
    print(f"deleted (logged ok): {len(recs)} files, {sum(r['s'] for r in recs) / 1e9:.2f} GB; "
          f"kept twins re-hashed: {len(want)}; problems: {len(bad)}; deleted paths still present: {still}")
    for b in bad[:30]:
        print("  ", b)


if __name__ == "__main__":
    a = sys.argv
    if a[1] == "plan":
        plan(a[2], a[3], a[4])
    elif a[1] == "run":
        run(a[2], a[3], int(a[a.index("--from") + 1]), int(a[a.index("--count") + 1]))
    elif a[1] == "verify":
        verify(a[2])
