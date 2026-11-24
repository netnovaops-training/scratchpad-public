#!/usr/bin/env python3
"""pc_fill.py - plan step 5: copy PC photos/videos that are NOT on the NAS into Photos/Library/YYYY/MM.
COPY ONLY: nothing on the PC is moved, renamed or deleted (D:\\Garden for all keeps its copies).

  python pc_fill.py plan   -> OUT\\step5-plan.csv + OUT\\step5-dryrun.txt   (dry run, nothing copied)
  python pc_fill.py copy   -> copies one file at a time (one SMB stream) with shutil.copy2; log OUT\\step5-copy.csv
                              restartable: a target already present with the right size is not copied again
  python pc_fill.py nas-verify-list -> OUT\\step5-verify.jsonl ({"p": NAS path, "h": expected sha}) for nas_check.py
Inputs: OUT\\pc-sha.csv (pc_scan.py hash), OUT\\nas-index.jsonl (copy of the NAS index after steps 3-4:
        every hash anywhere in Pictures, Photos and the hold area counts as "on the NAS"),
        OUT\\step4-dedupe-log.jsonl (hashes of deleted duplicates; their kept twin is on the NAS).
Which PC files: photo/video extensions (pc_scan.py rules), not empty, hash not on the NAS; one source per hash
(shortest path wins, so a camera folder beats a deep app copy). Too-small images are app icons/thumbnails,
not photos: images < 20 KB and videos < 100 KB are left out (listed in the dry-run report by folder).
Date (11-17 rules, photo_meta.py): EXIF, then file name, then file mtime -> Library\\YYYY\\MM\\<name>;
a name already taken gets " (2)", " (3)" ...; never overwritten.
Target: P:\\Library (P: = \\\\192.168.1.68\\home\\Photos, mapped by map_p.py).
OUTCOME 2026-11-24: written for plan step 5.
"""
import csv
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"D:\scratchpad\tools")
from photo_meta import exif_date, kind, name_date  # noqa: E402

OUT = Path(r"D:\backups\photo-verify\2026-11-24")
LIB_SMB = "P:\\Library"
LIB_NAS = "/volume1/homes/jay/Photos/Library"
# Not personal photos (dry run 11-24 showed them): security-camera recordings, Garden for All / website / project
# assets and app data. They are listed in the dry-run report for Jason, never copied into his photo library.
EXCLUDE_PREFIX = tuple(p.lower() for p in (
    r"M:\vault-backup\camera", r"D:\Garden for all", r"D:\gfa-", r"D:\Robin-garden-template", r"D:\garden-page-builder",
    r"D:\_parked", r"C:\Jasons New Site", r"D:\ai-stack", r"D:\stock-list", r"D:\archive", r"D:\overnight",
    r"D:\scratchpad", r"D:\projects-src", r"D:\Toolbox", r"D:\tools", r"C:\AMD", r"D:\Backups", r"D:\backups",
    r"C:\Users\USER\Desktop\plant-photos-to-tag"))
EXCLUDE_PART = ("\\.claude\\", "\\.vscode\\", "\\brave_backup\\", "\\luminar neo catalog\\", "\\.cursor\\")
# Second dry run 11-24 still held course videos, Calibre covers, Traktor, toolchain docs: so only folders that
# hold Jason's own photos/videos are copied (everything else not on the NAS is listed for him to decide).
INCLUDE = re.compile(r"^(c:\\users\\[^\\]+\\(pictures|videos|onedrive[^\\]*\\pictures)\\"
                     r"|c:\\users\\USER_0lyp1qt\\desktop\\backup\\"
                     r"|m:\\vault-backup\\drive\\(my photos\\|casey bday part 2\\|videoes\\|[^\\]+$)"
                     r"|c:\\our-crew\\|d:\\_work\\photos-20261120\\)", re.I)
MIN_IMAGE = 20 * 1024
MIN_VIDEO = 100 * 1024


def nas_hashes():
    hs = set()
    for l in open(OUT / "nas-index.jsonl", encoding="utf-8"):
        try:
            h = json.loads(l)["h"]
        except (ValueError, KeyError):
            continue
        if not h.startswith("ERROR"):
            hs.add(h)
    # 11-17 hashes of the Pictures and photo shares: nothing was deleted without a kept twin since, so each is
    # still on the NAS somewhere (covers the photo share's Immich/Gemini/Backups leftovers, not in the 11-24 index)
    for l in open(r"D:\backups\photo-verify\unify\nas-sha.jsonl", encoding="utf-8"):
        try:
            h = json.loads(l)["h"]
        except (ValueError, KeyError):
            continue
        if not str(h).startswith("ERROR"):
            hs.add(h)
    log = OUT / "step4-dedupe-log.jsonl"
    if log.exists():
        for l in open(log, encoding="utf-8"):
            try:
                hs.add(json.loads(l)["h"])
            except (ValueError, KeyError):
                pass
    return hs


def date_of(p):
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


def plan():
    on_nas = nas_hashes()
    rows = list(csv.DictReader(open(OUT / "pc-sha.csv", newline="", encoding="utf-8")))
    best, small, excluded, present, bad = {}, {}, {}, 0, 0
    for r in rows:
        h, size, p = r["sha256"], int(r["size"]), r["path"]
        if h.startswith(("ERROR", "SKIP")) or size == 0:
            bad += 1
            continue
        if h in on_nas:
            present += 1
            continue
        pl = p.lower()
        if pl.startswith(EXCLUDE_PREFIX) or any(x in pl for x in EXCLUDE_PART) or not INCLUDE.match(p):
            top = "\\".join(p.split("\\")[:3])
            excluded[top] = excluded.get(top, 0) + 1
            continue
        ext = p.rsplit(".", 1)[-1].lower()
        k = kind(ext)
        if (k in ("image", "raw") and size < MIN_IMAGE) or (k == "video" and size < MIN_VIDEO):
            top = "\\".join(p.split("\\")[:3])
            small[top] = small.get(top, 0) + 1
            continue
        if h not in best or (len(p), p) < (len(best[h]["path"]), best[h]["path"]):
            best[h] = r
    taken, plan_rows, by_top, by_src = set(), [], {}, {}
    for h, r in sorted(best.items(), key=lambda x: x[1]["path"]):
        d, src = date_of(r["path"])
        name = os.path.basename(r["path"])
        rel = f"{d.year:04d}\\{d.month:02d}\\{name}"
        want = rel
        base, ext = os.path.splitext(rel)
        i = 2
        while rel.lower() in taken or os.path.exists(os.path.join(LIB_SMB, rel)):
            rel = f"{base} ({i}){ext}"
            i += 1
        taken.add(rel.lower())
        plan_rows.append([r["path"], r["size"], h, rel, src, "clash" if rel != want else ""])
        top = "\\".join(r["path"].split("\\")[:3])
        n, b = by_top.get(top, (0, 0))
        by_top[top] = (n + 1, b + int(r["size"]))
        by_src[src] = by_src.get(src, 0) + 1
    with open(OUT / "step5-plan.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pc_path", "size", "sha256", "library_rel", "date_from", "clash"])
        w.writerows(plan_rows)
    tot = sum(int(r[1]) for r in plan_rows)
    lines = [f"Step 5 PC fill-in dry run {time.strftime('%F %T')} (nothing copied)",
             f"PC photo/video files hashed: {len(rows)}; already on the NAS (hash): {present}; "
             f"unreadable/placeholder/empty: {bad}",
             f"to copy: {len(plan_rows)} unique files, {tot / 1e9:.2f} GB; name clashes renamed: "
             f"{sum(1 for r in plan_rows if r[5])}",
             "date from: " + ", ".join(f"{k} {v}" for k, v in sorted(by_src.items())),
             "by folder (files, GB):"]
    lines += [f"  {k}: {n} {b / 1e9:.2f}" for k, (n, b) in sorted(by_top.items(), key=lambda x: -x[1][1])]
    lines.append("left out as not personal photos (camera recordings, Garden for All, site/project/app assets), "
                 "not on the NAS, by folder (Jason decides):")
    lines += [f"  {k}: {n}" for k, n in sorted(excluded.items(), key=lambda x: -x[1])]
    lines.append("left out as too small (icons/thumbnails), by folder:")
    lines += [f"  {k}: {n}" for k, n in sorted(small.items(), key=lambda x: -x[1])[:40]]
    open(OUT / "step5-dryrun.txt", "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("\n".join(lines[:5 + min(len(by_top), 30)]))


def copy():
    rows = list(csv.DictReader(open(OUT / "step5-plan.csv", newline="", encoding="utf-8")))
    logp = OUT / "step5-copy.csv"
    done = set()
    if logp.exists():
        done = {r["pc_path"] for r in csv.DictReader(open(logp, newline="", encoding="utf-8")) if r["status"] == "copied"}
    new = not logp.exists()
    ok = err = 0
    with open(logp, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["pc_path", "nas_path", "size", "sha256", "status"])
        for i, r in enumerate(rows, 1):
            if r["pc_path"] in done:
                continue
            dst = os.path.join(LIB_SMB, r["library_rel"])
            nas = LIB_NAS + "/" + r["library_rel"].replace("\\", "/")
            try:
                if os.path.exists(dst):
                    if os.path.getsize(dst) != int(r["size"]):
                        raise RuntimeError("target exists with another size; not overwritten")
                else:
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    tmp = dst + ".part-pcfill"
                    shutil.copy2(r["pc_path"], tmp)
                    if os.path.getsize(tmp) != int(r["size"]):
                        raise RuntimeError("size mismatch after copy")
                    if os.path.exists(dst):
                        raise RuntimeError("target appeared during copy")
                    os.rename(tmp, dst)
                w.writerow([r["pc_path"], nas, r["size"], r["sha256"], "copied"])
                ok += 1
            except (OSError, RuntimeError) as e:
                w.writerow([r["pc_path"], nas, r["size"], r["sha256"], "ERROR " + str(e)[:150]])
                err += 1
            if i % 200 == 0:
                f.flush()
                print(time.strftime("%F %T"), f"{i}/{len(rows)} copied {ok} errors {err}", flush=True)
    print(time.strftime("%F %T"), f"copy done: copied {ok}, errors {err}", flush=True)


def verify_list():
    with open(OUT / "step5-verify.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in csv.DictReader(open(OUT / "step5-copy.csv", newline="", encoding="utf-8")):
            if r["status"] == "copied":
                f.write(json.dumps({"p": r["nas_path"], "h": r["sha256"]}, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    {"plan": plan, "copy": copy, "nas-verify-list": verify_list}[sys.argv[1]]()
