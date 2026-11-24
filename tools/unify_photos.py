#!/usr/bin/env python3
"""unify_photos.py - T1014 phase 1 (READ-ONLY): inventory, hash and date every photo on P:, Z: and the M:-only files.

Run (PC, detached):  python D:\\scratchpad\\tools\\unify_photos.py all
Stages (each is cached and restartable; rerun skips finished work):
  walk   list P:\\ (\\\\192.168.1.68\\Pictures) and Z:\\ (\\\\192.168.1.68\\photo) -> unify\\inventory.csv;
         the M: files with no NAS copy come from D:\\backups\\photo-verify\\report.csv (NO COPY rows that are NOT under
         M:\\Pictures\\SynologyDrive\\, which are Synology Drive online-only placeholders whose real files are on the NAS)
  hash   full SHA-256 of every media file whose size is shared with another file (unique size = cannot be a
         copy). M: files are hashed here (local disk) -> unify\\hashes.csv. NAS files are written to
         unify\\nas-list.jsonl and hashed ON the NAS by nas_sha.py (ssh jay@100.64.0.12, ~/t1014/); its
         sha.jsonl is copied back to unify\\nas-sha.jsonl and read by the plan.
  meta   capture time (EXIF, first 64 KB; else the Google Takeout .json sidecar) for RAWs, JPGs that may pair
         with a RAW, and files outside P:\\<year>\\<month> with no date in their name -> unify\\meta.csv
  plan   unify_plan.py: D:\\backups\\photo-verify\\unify-plan.csv + unify-summary.txt
Progress: D:\\backups\\photo-verify\\unify\\progress.log
Safety: opens files for reading only. Never moves, copies, renames or deletes anything (phase 2 is a separate,
Jason-approved script). Skips Synology system folders (#recycle, #snapshot, @eaDir, .Synology*, .tmp.drivedownload).
OUTCOME 2026-11-17: written for T1014 phase 1; first run started detached the same day (see progress.log).
HISTORICAL 2026-11-24: the library left the Pictures share for \\\\192.168.1.68\\home\\Photos\\Library (P: maps to
\\\\192.168.1.68\\home\\Photos now). These ROOTS describe the 11-17 layout; the current tools are in
D:\\scratchpad\\tools\\photos_syno\\.
"""
import csv
import hashlib
import json
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import photo_meta as pm

ROOTS = {"P": r"\\192.168.1.68\Pictures", "Z": r"\\192.168.1.68\photo"}
REPORT = Path(r"D:\backups\photo-verify\report.csv")
OUT = Path(r"D:\backups\photo-verify")
WORK = OUT / "unify"
INV, HASHES, META = WORK / "inventory.csv", WORK / "hashes.csv", WORK / "meta.csv"
SKIP_DIRS = {"#recycle", "#snapshot", "@eaDir", ".SynologyWorkingDirectory", ".tmp.drivedownload"}
# Folders that hold program data, not photos (previews, caches). Listed in the plan as 'app-data', never touched.
APP_DATA = ("\\luminar neo catalog\\", "\\cachedocuments\\", "\\previewcache\\", "\\thumbs\\", "\\encoded-video\\")
NAS_ROOTS = {"P": "/volume1/Pictures", "Z": "/volume1/photo"}
NAS_LIST, NAS_SHA = WORK / "nas-list.jsonl", WORK / "nas-sha.jsonl"
INV_COLS = ["src", "path", "size", "mtime", "ext", "kind", "top"]


def log(msg):
    """Append one time-stamped line to progress.log and echo it."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    with open(WORK / "progress.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def classify(path, ext):
    """File kind, but 'app-data' for program caches whatever the extension."""
    low = path.lower()
    if any(a in low for a in APP_DATA):
        return "app-data"
    return pm.kind(ext)


def file_ext(path):
    """Lower-case extension without the dot. Strips trailing spaces and SMB-mapped characters (U+F000-U+F0FF:
    the share shows a CR at the end of a name as U+F00D). OUTCOME 2026-11-17: 820 Takeout-RawOnly RAWs end in
    '.NEF<U+F00D>' / '.dng<U+F00D>'; plain splitext read them as not-photo until this fix."""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    return "".join(c for c in ext if not "" <= c <= "").strip()


def row_for(src, path, root):
    """Inventory row for one file, or None if it cannot be stat'ed (e.g. an online-only placeholder)."""
    try:
        st = os.stat(path)
    except OSError:
        return None
    ext = file_ext(path)
    rel =os.path.relpath(path, root)
    top = rel.split(os.sep)[0] if os.sep in rel else "(root)"
    return {"src": src, "path": path, "size": st.st_size, "mtime": int(st.st_mtime), "ext": ext,
            "kind": classify(path, ext), "top": top}


def m_only_paths():
    """M: files with no NAS copy, minus the Synology Drive placeholders."""
    with open(REPORT, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            p = r["m_path"]
            if r["nas_copy"] == "NO COPY" and not p.lower().startswith("m:\\pictures\\synologydrive\\"):
                yield p


def stage_walk():
    """Write inventory.csv (skipped if it already exists; delete it to rescan)."""
    if INV.exists():
        log(f"walk: {INV} exists, reusing")
        return
    tmp = INV.with_suffix(".tmp")
    n = 0
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, INV_COLS)
        w.writeheader()
        for src, root in ROOTS.items():
            log(f"walk: {src}: {root}")
            for dp, dns, fns in os.walk(root):
                dns[:] = [d for d in dns if d not in SKIP_DIRS and not d.startswith(".Synology")]
                for fn in fns:
                    r = row_for(src, os.path.join(dp, fn), root)
                    if r:
                        w.writerow(r)
                        n += 1
                        if n % 10000 == 0:
                            log(f"walk: {n} files")
        missing = 0
        for p in m_only_paths():
            r = row_for("M", p, r"M:\Pictures")
            if r:
                w.writerow(r)
                n += 1
            else:
                missing += 1
        log(f"walk: M-only rows done; {missing} listed M: files no longer readable")
    tmp.replace(INV)
    log(f"walk: done, {n} files")


def load_inventory():
    """inventory.csv as a list of dicts with int size/mtime."""
    with open(INV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        r["size"], r["mtime"] = int(r["size"]), int(r["mtime"])
        r["ext"] = file_ext(r["path"])  # re-derived so an inventory walked before the file_ext fix stays right
        r["kind"] = classify(r["path"], r["ext"])
    return rows


def load_cache(path, key_cols, val_col):
    """Read a cache CSV into {(path, size/mtime...): value}."""
    out = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                out[tuple(r[c] for c in key_cols)] = r[val_col]
    return out


def sha256(path):
    """Full SHA-256 of a local file, or 'ERROR:<reason>'."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(4 << 20), b""):
                h.update(chunk)
    except OSError as e:
        return f"ERROR:{type(e).__name__}"
    return h.hexdigest()


def to_nas(path):
    """\\\\192.168.1.68\\Pictures\\a\\b.jpg -> /volume1/Pictures/a/b.jpg (the same file seen on the NAS)."""
    for src, root in ROOTS.items():
        if path.lower().startswith(root.lower() + "\\"):
            return NAS_ROOTS[src] + path[len(root):].replace("\\", "/")
    raise ValueError(path)


def from_nas(path):
    """Inverse of to_nas."""
    for src, root in NAS_ROOTS.items():
        if path.startswith(root + "/"):
            return ROOTS[src] + path[len(root):].replace("/", "\\")
    raise ValueError(path)


def nas_hashes():
    """{windows path: sha256} from the NAS run (unify\\nas-sha.jsonl), empty if not copied back yet."""
    out = {}
    if NAS_SHA.exists():
        with open(NAS_SHA, encoding="utf-8") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                out[from_nas(d["p"])] = d["h"]
    return out


def media(rows):
    """Rows that are photos/RAWs/videos (not sidecars, not program caches)."""
    return [r for r in rows if r["kind"] in ("image", "raw", "video")]


def run_cached(name, todo, fn, cache_path, cols, workers):
    """Run fn(row) over todo in a thread pool, appending each result to cache_path as it finishes."""
    new = not cache_path.exists()
    total_gb = sum(r["size"] for r in todo) / 1e9
    log(f"{name}: {len(todo)} files to do ({total_gb:.1f} GB)")
    done_n, done_b, t0 = 0, 0, time.time()
    with open(cache_path, "a", newline="", encoding="utf-8") as f, ThreadPoolExecutor(workers) as ex:
        w = csv.writer(f)
        if new:
            w.writerow(cols)
        for r, val in zip(todo, ex.map(fn, todo)):
            w.writerow([r["path"], r["size"], r["mtime"], val])
            done_n += 1
            done_b += r["size"]
            if done_n % 2000 == 0:
                f.flush()
                rate = done_n / max(time.time() - t0, 1)
                log(f"{name}: {done_n}/{len(todo)} files ({done_b / 1e9:.1f}/{total_gb:.1f} GB), {rate:.1f} files/s")
    log(f"{name}: done")


def rel_key(r):
    """Path below the share root, lower case: P:\\a\\b.jpg and Z:\\a\\b.jpg give the same key."""
    return r["path"][len(ROOTS[r["src"]]):].lower() if r["src"] in ROOTS else None


def z_twins(rows):
    """{Z: path: P: row} for Z: media with a P: file at the same relative path with the same size AND mtime.
    Why: tested 2026-11-17, 83,790 of 83,821 Z: media files are such twins (Z: is a copy of P:); reading them
    over SMB would take hours. A twin is planned as a duplicate of its P: file; phase 2 full-hashes both first."""
    p = {rel_key(r): r for r in media(rows) if r["src"] == "P"}
    out = {}
    for r in media(rows):
        if r["src"] == "Z":
            q = p.get(rel_key(r))
            if q and (q["size"], q["mtime"]) == (r["size"], r["mtime"]):
                out[r["path"]] = q
    return out


def collisions(rows):
    """Media rows whose size is shared with at least one other media file."""
    by_size = defaultdict(list)
    for r in media(rows):
        by_size[r["size"]].append(r)
    return [r for grp in by_size.values() if len(grp) > 1 for r in grp]


def stage_hash(rows):
    """Write the NAS hash list; full SHA-256 of the colliding M: files here."""
    todo = collisions(rows)
    nas = [r for r in todo if r["src"] in ROOTS]
    with open(NAS_LIST, "w", encoding="utf-8", newline="\n") as f:
        f.writelines(json.dumps(to_nas(r["path"])) + "\n" for r in nas)
    log(f"hash: {len(nas)} NAS files ({sum(r['size'] for r in nas) / 1e9:.1f} GB) -> {NAS_LIST}; run nas_sha.py on the NAS")
    cache = load_cache(HASHES, ["path", "size", "mtime"], "sha256")
    local = [r for r in todo if r["src"] == "M" and (r["path"], str(r["size"]), str(r["mtime"])) not in cache]
    run_cached("hash", local, lambda r: sha256(r["path"]), HASHES, ["path", "size", "mtime", "sha256"], 2)


def sidecar_index(rows):
    """{folder: {first 30 chars of name, lower: [json names]}} for Google Takeout .json sidecars."""
    idx = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["ext"] == "json":
            n = os.path.basename(r["path"])
            idx[os.path.dirname(r["path"])][n[:30].lower()].append(n)
    return idx


def sidecar_date(r, idx):
    """photoTakenTime from the Takeout .json next to a file (Takeout cuts names to 46 chars), or None."""
    d, name = os.path.dirname(r["path"]), os.path.basename(r["path"])
    for j in idx.get(d, {}).get(name[:30].lower(), ()):
        if j.lower().startswith(name[:46].lower()):
            try:
                with open(os.path.join(d, j), encoding="utf-8") as f:
                    return datetime.fromtimestamp(int(json.load(f)["photoTakenTime"]["timestamp"]))
            except (OSError, ValueError, KeyError, TypeError):
                return None
    return None


def date_of(r, idx):
    """'exif:<iso>' or 'json:<iso>' (Takeout sidecar) or ''. RAWs retry EXIF with a bigger read."""
    d = pm.exif_date(r["path"], r["ext"])
    if d is None and r["kind"] == "raw":
        d = pm.exif_date(r["path"], r["ext"], 512 * 1024)
    if d:
        return "exif:" + d.isoformat()
    s = sidecar_date(r, idx)
    return "json:" + s.isoformat() if s else ""


def stage_meta(rows):
    """Dates for RAWs, JPG/HEIF files sharing a shot name with a RAW (pairing needs EXIF), and files outside
    P:\\YYYY\\MM whose name holds no date (they need a YYYY\\MM target). Name-dated files are not opened."""
    twins = z_twins(rows)
    m = [r for r in media(rows) if r["path"] not in twins]  # twins become duplicates; no date needed
    raw_keys = {pm.shot_key(Path(r["path"]).stem) for r in m if r["kind"] == "raw"}
    need = []
    for r in m:
        stem = Path(r["path"]).stem
        if r["kind"] == "raw" or (r["ext"] in pm.EXIF_READABLE and pm.shot_key(stem) in raw_keys) or not in_layout(r) and not pm.name_date(stem):
            need.append(r)
    cache = load_cache(META, ["path", "size", "mtime"], "date")
    todo = [r for r in need if (r["path"], str(r["size"]), str(r["mtime"])) not in cache]
    idx = sidecar_index(rows)
    run_cached("meta", todo, lambda r: date_of(r, idx), META, ["path", "size", "mtime", "date"], 8)


def in_layout(r):
    """True if the file already sits in P:\\<year>\\<month>\\ (the Synology Photos layout Jason wants)."""
    if r["src"] != "P":
        return False
    parts = os.path.relpath(r["path"], ROOTS["P"]).split(os.sep)
    return (len(parts) >= 3 and len(parts[0]) == 4 and parts[0].isdigit()
            and len(parts[1]) == 2 and parts[1].isdigit() and 1 <= int(parts[1]) <= 12)


def main():
    """Run the named stage, or all of them in order."""
    WORK.mkdir(parents=True, exist_ok=True)
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    log(f"start: {stage}")
    if stage in ("walk", "all"):
        stage_walk()
    rows = load_inventory()
    if stage in ("hash", "all"):
        stage_hash(rows)
    if stage in ("meta", "all"):
        stage_meta(rows)
    if stage in ("plan", "all"):
        import unify_plan
        unify_plan.build(rows)
    log(f"finish: {stage}")


if __name__ == "__main__":
    main()
