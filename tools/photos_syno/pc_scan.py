#!/usr/bin/env python3
"""pc_scan.py - list and SHA-256 every photo/video file on the PC drives C: D: G: M:. Read-only on the PC.

Run:  python D:\\scratchpad\\tools\\photos_syno\\pc_scan.py list   -> OUT\\pc-files.csv (path,size,mtime,kind)
      python D:\\scratchpad\\tools\\photos_syno\\pc_scan.py hash   -> OUT\\pc-sha.csv (path,size,mtime,sha256); restartable
      python D:\\scratchpad\\tools\\photos_syno\\pc_scan.py count  -> one line: file count + bytes of the same scan
OUT = D:\\backups\\photo-verify\\2026-11-24
Skipped folders (plan step 5): Windows, Program Files*, ProgramData, AppData, node_modules, .git, Steam/Epic/game
folders, browser caches, $Recycle.Bin, System Volume Information, plus recovery/system folders, Python envs and
build caches (they hold app icons, not photos), and the Synology Drive mirrors M:\\Pictures\\SynologyDrive and
M:\\New Music Backup\\SynologyDrive (they ARE the NAS home folders, on-demand placeholders: reading downloads them).
Extensions: photo_meta.IMAGE | RAW | VIDEO (the 11-17 rules).
OUTCOME 2026-11-24: written for step 5 of plan functional-greeting-horizon.
"""
import csv
import ctypes
import hashlib
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\scratchpad\tools")
from photo_meta import IMAGE, RAW, VIDEO, kind  # noqa: E402

OUT = Path(r"D:\backups\photo-verify\2026-11-24")
DRIVES = ["C:\\", "D:\\", "G:\\", "M:\\"]
EXTS = IMAGE | RAW | VIDEO
SKIP_NAMES = {
    "windows", "program files", "program files (x86)", "programdata", "appdata", "node_modules", ".git",
    "$recycle.bin", "system volume information", "$sysreset", "$windows.~ws", "$winreagent", "recovery",
    "config.msi", "esd", "msys64", "python314", ".venv", "venv", "site-packages", "__pycache__", ".cache",
    "cache", "code cache", "gpucache", "steam", "steamapps", "steamlibrary", "epic games", "epicgames",
    "gog galaxy", "gog games", "riot games", "xboxgames", "battle.net", "ubisoft", "ea games", "ycswap",
    ".gradle", ".m2", ".npm", ".nuget", "perflogs", "documents and settings", "onedrivetemp",
}


def walk():
    """Yield (path, size, mtime) of every photo/video file on DRIVES, honoring SKIP_NAMES."""
    for drive in DRIVES:
        for d, dirs, files in os.walk(drive, onerror=lambda e: None):
            dirs[:] = [x for x in dirs if x.lower() not in SKIP_NAMES]
            if d.lower().startswith(("m:\\pictures\\synologydrive", "m:\\new music backup\\synologydrive")):
                dirs[:] = []
                continue  # Synology Drive on-demand mirror of the NAS itself (coordinator 11-24); reading would download
            for f in files:
                ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
                if ext not in EXTS:
                    continue
                p = os.path.join(d, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                yield p, st.st_size, int(st.st_mtime), kind(ext)


def do_list():
    OUT.mkdir(parents=True, exist_ok=True)
    n = b = 0
    with open(OUT / "pc-files.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["path", "size", "mtime", "kind"])
        for row in walk():
            w.writerow(row)
            n += 1
            b += row[1]
    print(f"listed {n} files, {b / 1e9:.1f} GB -> {OUT / 'pc-files.csv'}")


def do_count():
    n = b = 0
    for row in walk():
        n += 1
        b += row[1]
    print(f"{time.strftime('%F %T')} count {n} files {b} bytes")


def sha(p):
    """SHA-256 of p; refuses cloud placeholders (RECALL_ON_DATA_ACCESS / OFFLINE) so nothing is downloaded."""
    a = ctypes.windll.kernel32.GetFileAttributesW(p)
    if a != 0xFFFFFFFF and a & (0x400000 | 0x1000):
        return "SKIP:cloud-placeholder"
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def do_hash():
    src = OUT / "pc-files.csv"
    dst = OUT / "pc-sha.csv"
    done = set()
    if dst.exists():
        with open(dst, newline="", encoding="utf-8") as f:
            done = {(r["path"], r["size"], r["mtime"]) for r in csv.DictReader(f)}
    with open(src, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if (r["path"], r["size"], r["mtime"]) not in done]
    total = sum(int(r["size"]) for r in rows)
    print(time.strftime("%F %T"), f"to hash {len(rows)} files {total / 1e9:.1f} GB; done before {len(done)}", flush=True)
    new = not dst.exists()
    n = b = 0
    t0 = time.time()
    with open(dst, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["path", "size", "mtime", "sha256"])
        for r in rows:
            try:
                h = sha(r["path"])
            except OSError as e:
                h = "ERROR:" + type(e).__name__
            w.writerow([r["path"], r["size"], r["mtime"], h])
            n += 1
            b += int(r["size"])
            if n % 2000 == 0:
                f.flush()
                print(time.strftime("%F %T"), f"{n}/{len(rows)} {b / 1e9:.1f}/{total / 1e9:.1f} GB "
                      f"{b / 1e6 / max(time.time() - t0, 1):.0f} MB/s", flush=True)
    print(time.strftime("%F %T"), "hash done", flush=True)


if __name__ == "__main__":
    {"list": do_list, "hash": do_hash, "count": do_count}[sys.argv[1]]()
