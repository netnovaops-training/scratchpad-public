#!/usr/bin/env python3
"""unify_plan.py - T1014 phase 1: turn the cached inventory/hashes/dates into a DRY-RUN plan. Changes no photo.

Run:     python D:\\scratchpad\\tools\\unify_photos.py plan     (unify_photos.py calls build() here)
Reads:   D:\\backups\\photo-verify\\unify\\inventory.csv, hashes.csv, meta.csv (opens no photo)
Writes:  D:\\backups\\photo-verify\\unify-plan.csv    one row per file on P:, Z: and the M:-only list
         D:\\backups\\photo-verify\\unify-summary.txt totals per action, with GB
Actions (Jason's rules 2026-11-17: P:\\ is the one master, P:\\<year>\\<month>, one copy, no RAW):
  keep             already in P:\\YYYY\\MM, stays
  relocate-in-P    on P: but outside YYYY\\MM; would move to target (Jason decides if he wants this)
  move-from-Z      only copy is on Z:; would move to target
  copy-from-M      only copy is on M:; would be copied to target
  duplicate        same full SHA-256 as keep_path; would go to the NAS #recycle (M: copies: Windows Recycle Bin
                   later). proof column: 'sha256', or 'unique size' for files no other file could equal.
  raw-with-jpg     RAW whose JPG/HEIF of the same shot exists (raw_match); RAW would go to #recycle
  raw-convert      RAW is the only copy; would be converted to JPG at target (date/EXIF kept), then RAW to #recycle
  raw-check        RAW with a same-name JPG elsewhere but no capture time to prove it is the same shot: Jason looks
  unreadable       could not be read for hashing; nothing planned
  not-hashed       shares its size with another file but has no hash yet (NAS run unfinished); nothing planned
  not-photo / app-data   sidecars, music, program caches: left alone
Keeper choice in a duplicate group: P: before Z: before M:; inside YYYY\\MM first; not a backup/Takeout staging
folder; then shortest path.
OUTCOME 2026-11-17: written for T1014 phase 1.
"""
import csv
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import photo_meta as pm
from unify_photos import (
    HASHES,
    META,
    OUT,
    ROOTS,
    collisions,
    in_layout,
    load_cache,
    log,
    nas_hashes,
)

PLAN, SUMMARY = OUT / "unify-plan.csv", OUT / "unify-summary.txt"
STAGING = {"takeout-rawonly", "takeout-reconcile", "backups", "backup 2", "backup 3", "master backup 1",
           "master backup 4", "_backups", "from-m-pictures", "new folder"}
JPG_LIKE = pm.JPG | {"heic", "heif", "hif"}
COLS = ["action", "src", "path", "size_bytes", "kind", "fingerprint", "keep_path", "raw_match", "match_how",
        "target", "date", "date_source", "proof"]
YEAR_DIR = re.compile(r"[\\/]((?:19|20)\d\d)[\\/](?:(0[1-9]|1[0-2])[\\/])?")


RAW_CHECK_DIR = "_T1014-raw-check"


def undated(date_source):
    """Sub-folder for files whose month is unknown: Jason 11-17 chose P:\\YYYY\\undated over YYYY\\01."""
    return "undated" if date_source == "folder-year-only" else None


def keeper_rank(r):
    """Sort key: the lowest-ranked file of a duplicate group is the one that stays."""
    return ({"P": 0, "Z": 1, "M": 2}[r["src"]], 0 if in_layout(r) else 1,
            1 if r["top"].lower() in STAGING else 0, len(r["path"]), r["path"].lower())


class Dates:
    """Capture time for a file: EXIF, file name, Takeout sidecar, YYYY[\\MM] folder, file time (in that order).
    EXIF and sidecar dates come from meta.csv ('exif:<iso>' / 'json:<iso>'), so nothing is opened here."""

    def __init__(self, meta):
        self.meta = meta

    def exif(self, path):
        """EXIF capture time of a file, or None (sidecar dates do not count: pairing needs the camera clock)."""
        v = self.meta.get(path, "")
        return datetime.fromisoformat(v[5:]) if v.startswith("exif:") else None

    def get(self, r):
        """(datetime, source) for one inventory row."""
        v = self.meta.get(r["path"], "")
        if v.startswith("exif:"):
            return datetime.fromisoformat(v[5:]), "exif"
        n = pm.name_date(Path(r["path"]).stem)
        if n:
            return n, "name"
        if v.startswith("json:"):
            return datetime.fromisoformat(v[5:]), "takeout-json"
        mt = datetime.fromtimestamp(r["mtime"])
        m = YEAR_DIR.search(r["path"])
        if m:
            y, mo = int(m.group(1)), m.group(2)
            if mo:
                return datetime(y, int(mo), 1), "folder-year-month"
            return (mt, "folder-year+file-time") if mt.year == y else (datetime(y, 1, 1), "folder-year-only")
        return mt, "file-time"


class Targets:
    """Hands out P:\\YYYY\\MM\\name paths that clash with nothing already there or already handed out."""

    def __init__(self, staying):
        self.used = {p.lower() for p in staying}

    def get(self, when, name, sub=None):
        """Free target path for a file taken at `when`, in P:\\YYYY\\MM (or P:\\YYYY\\<sub>, or P:\\<sub> if when
        is None)."""
        if when is None:
            folder = os.path.join(ROOTS["P"], sub)
        else:
            folder = os.path.join(ROOTS["P"], f"{when.year:04d}", sub or f"{when.month:02d}")
        stem, ext = os.path.splitext(name)
        cand, i = os.path.join(folder, name), 0
        while cand.lower() in self.used:
            i += 1
            cand = os.path.join(folder, f"{stem}_{i}{ext}")
        self.used.add(cand.lower())
        return cand


def pair_raws(keepers, media_rows, dates):
    """For each RAW keeper: (action, jpg path, how). Same folder + shot name wins; elsewhere the times must agree."""
    jpgs = defaultdict(list)
    for r in media_rows:
        if r["kind"] == "image" and r["ext"] in JPG_LIKE:
            jpgs[pm.shot_key(Path(r["path"]).stem)].append(r)
    out = {}
    for k in keepers:
        if k["kind"] != "raw":
            continue
        cands = jpgs.get(pm.shot_key(Path(k["path"]).stem), [])
        same = [j for j in cands if os.path.dirname(j["path"]) == os.path.dirname(k["path"])]
        if same:
            out[k["path"]] = ("raw-with-jpg", same[0]["path"], "same folder + name")
            continue
        kt = dates.exif(k["path"])
        timed = [j for j in cands if kt and dates.exif(j["path"])
                 and abs((dates.exif(j["path"]) - kt).total_seconds()) <= 2]
        if timed:
            out[k["path"]] = ("raw-with-jpg", timed[0]["path"], "same name + capture time")
        elif cands and (not kt or not any(dates.exif(j["path"]) for j in cands)):
            out[k["path"]] = ("raw-check", cands[0]["path"], "same name, no time to compare")
        else:
            out[k["path"]] = ("raw-convert", "", "no JPG of this shot")
    return out


def build(rows):
    """Write unify-plan.csv and unify-summary.txt from the cached stages."""
    hashes = {k[0]: v for k, v in load_cache(HASHES, ["path"], "sha256").items()}  # M: files, hashed on the PC
    hashes.update(nas_hashes())                                                     # P: and Z:, hashed on the NAS
    meta = {k[0]: v for k, v in load_cache(META, ["path"], "date").items() if v}
    dates = Dates(meta)
    media_rows = [r for r in rows if r["kind"] in ("image", "raw", "video")]
    need = {r["path"] for r in collisions(rows)}
    plan = []
    groups = defaultdict(list)
    for r in media_rows:
        h = hashes.get(r["path"], "")
        r["sha"] = h
        if h and not h.startswith("ERROR"):
            r["proof"] = "sha256"
            groups[h].append(r)
        elif r["path"] in need:  # shares its size with another file but has no hash: plan nothing for it
            plan.append(dict(r, action="unreadable" if h else "not-hashed"))
        else:
            r["proof"] = "unique size"
            groups[("unique-size", r["path"])].append(r)
    for r in rows:
        if r["kind"] in ("other", "app-data"):
            plan.append(dict(r, action="not-photo" if r["kind"] == "other" else "app-data"))
    keepers = []
    for grp in groups.values():
        grp.sort(key=keeper_rank)
        keepers.append(grp[0])
        for d in grp[1:]:
            plan.append(dict(d, action="duplicate", keep_path=grp[0]["path"]))
    raw = pair_raws(keepers, media_rows, dates)
    targets = Targets(k["path"] for k in keepers if in_layout(k) and k["kind"] != "raw")
    for k in sorted(keepers, key=lambda r: r["path"].lower()):
        if k["kind"] == "raw":
            act, match, how = raw[k["path"]]
            row = dict(k, action=act, raw_match=match, match_how=how)
            if act == "raw-convert":
                when, src = dates.get(k)
                row.update(target=targets.get(when, Path(k["path"]).stem + ".jpg", undated(src)), date=when,
                           date_source=src)
            elif act == "raw-check":  # Jason 11-17: keep as RAW, all in one folder to check by eye
                row.update(target=targets.get(None, os.path.basename(k["path"]), RAW_CHECK_DIR))
            plan.append(row)
        elif in_layout(k):
            plan.append(dict(k, action="keep"))
        else:
            when, src = dates.get(k)
            act = {"P": "relocate-in-P", "Z": "move-from-Z", "M": "copy-from-M"}[k["src"]]
            plan.append(dict(k, action=act, target=targets.get(when, os.path.basename(k["path"]), undated(src)),
                             date=when, date_source=src))
    write_plan(plan)
    write_summary(plan)


def write_plan(plan):
    """unify-plan.csv, sorted by action then path."""
    tmp = PLAN.with_suffix(".tmp")
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        for r in sorted(plan, key=lambda r: (r["action"], r["path"].lower())):
            d = r.get("date")
            w.writerow([r["action"], r["src"], r["path"], r["size"], r["kind"], r.get("sha", ""),
                        r.get("keep_path", ""), r.get("raw_match", ""), r.get("match_how", ""), r.get("target", ""),
                        d.isoformat(sep=" ") if d else "", r.get("date_source", ""), r.get("proof", "")])
    tmp.replace(PLAN)
    log(f"plan: {len(plan)} rows -> {PLAN}")


def gb(n):
    """Bytes as 'x.x GB'."""
    return f"{n / 1e9:.1f} GB"


def write_summary(plan):
    """unify-summary.txt: totals per action and source, Z: by folder, date sources, RAW pairing."""
    cnt, size = Counter(), Counter()
    for r in plan:
        for key in (r["action"], (r["action"], r["src"])):
            cnt[key] += 1
            size[key] += r["size"]
    order = ["keep", "relocate-in-P", "move-from-Z", "copy-from-M", "duplicate", "raw-with-jpg", "raw-convert",
             "raw-check", "unreadable", "not-hashed", "not-photo", "app-data"]
    L = [f"T1014 photo unify plan (dry run, nothing changed) - {datetime.now():%Y-%m-%d %H:%M}", ""]
    for a in order:
        by = ", ".join(f"{s} {cnt[(a, s)]}" for s in "PZM" if cnt[(a, s)])
        L.append(f"{a:14} {cnt[a]:>8} files {gb(size[a]):>10}   ({by})" if cnt[a] else f"{a:14} {0:>8} files")
    gone = size["duplicate"] + size["raw-with-jpg"] + size["raw-convert"]
    lib = sum(size[a] for a in ("keep", "relocate-in-P", "move-from-Z", "copy-from-M"))
    L += ["", f"Would go to Recycle Bins (dupes + RAW): {gone / 1e9:.1f} GB",
          f"Library after (before RAW->JPG adds a little): {lib / 1e9:.1f} GB",
          "", "Z: by top folder (files, GB, action counts):"]
    ztop = defaultdict(Counter)
    zsize = Counter()
    for r in plan:
        if r["src"] == "Z":
            ztop[r["top"]][r["action"]] += 1
            zsize[r["top"]] += r["size"]
    for t in sorted(ztop, key=lambda t: -zsize[t]):
        L.append(f"  {t}: {sum(ztop[t].values())} files, {gb(zsize[t])}: "
                 + ", ".join(f"{a} {n}" for a, n in ztop[t].most_common()))
    ds = Counter(r.get("date_source") for r in plan if r.get("date_source"))
    L += ["", "Date source for files that get a new YYYY\\MM home: "
          + ", ".join(f"{k} {v}" for k, v in ds.most_common())]
    how = Counter(r["match_how"] for r in plan if r["kind"] == "raw" and r.get("match_how"))
    L += ["RAW pairing: " + ", ".join(f"{k} {v}" for k, v in how.most_common()), ""]
    SUMMARY.write_text("\n".join(L) + "\n", encoding="utf-8")
    log("plan: summary -> " + str(SUMMARY))
