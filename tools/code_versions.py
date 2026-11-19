#!/usr/bin/env python3
"""code_versions.py - keep two months of compressed code versions on D: (2026-11-19).

    python code_versions.py                    daily run: weekly FULL, otherwise only CHANGED files
    python code_versions.py --full             force a full version now
    python code_versions.py --list             list the versions on D:
    python code_versions.py --restore 2026-12-01 --to D:/restore-test [--source www]
                                               rebuild the code as it was at the end of that day

Why: Jason 2026-11-19: "a way to keep versions for two months in case something really breaks ...
.bak files will have date and folder structure and just the files changed ... a super compressed
version on D drive". Git keeps history only for what is committed; this also keeps uncommitted work.

What it writes: D:/Backups/code-versions/<YYYY-MM-DD_HHMM>_<full|changes>.bak.zip (LZMA, the best
compression zipfile has). Inside: <source>/<path as on disk> for every new or changed file, plus
MANIFEST.txt (added / changed / deleted paths). A FULL is made when the newest full is 7+ days old.
Keeps 60 days; the newest full older than 60 days is kept while newer change-sets still need it.

Only code and text: no .git, build output, media, models or databases, and nothing over 20 MB (photos
and media go to the NAS/Kopia, see to-do T0899). The state of the last run is state.json beside the
archives (size + mtime per file; contents are never read except to archive them).
"""
import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path

OUT = Path(r"D:\Backups\code-versions")
SOURCES = {
    "scratchpad": r"D:\scratchpad",
    "www": r"C:\Jasons New Site\www",
    "projects": r"C:\Users\USER\Projects",
    "android": r"C:\Users\USER\AndroidStudioProjects",
}
SKIP_DIRS = {".git", "node_modules", "target", "build", "dist", "__pycache__", ".venv", "venv", ".gradle",
             "models", "media", "transcripts", "site-data", "backups", ".idea", ".pytest_cache", "mirror"}
SKIP_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".nef", ".dng", ".mp4", ".mov", ".webm", ".mkv",
            ".mp3", ".wav", ".m4a", ".flac", ".ogg", ".zip", ".7z", ".gz", ".tar", ".iso", ".bin", ".gguf",
            ".safetensors", ".pt", ".onnx", ".apk", ".aab", ".ipa", ".pdf", ".db", ".sqlite", ".exe", ".dll",
            ".jar", ".tmp", ".log"}
MAX_BYTES = 20_000_000
KEEP_DAYS = 60
FULL_EVERY_DAYS = 7


def scan() -> dict:
    """Return {source: {relpath: [size, mtime]}} for every file worth keeping."""
    out = {}
    for name, root in SOURCES.items():
        files = {}
        for d, dirs, fs in os.walk(root):
            dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
            for f in fs:
                if os.path.splitext(f)[1].lower() in SKIP_EXT:
                    continue
                p = os.path.join(d, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_size > MAX_BYTES:
                    continue
                files[os.path.relpath(p, root).replace("\\", "/")] = [st.st_size, int(st.st_mtime)]
        out[name] = files
    return out


def versions() -> list[Path]:
    """All archives, oldest first (the file names sort by time)."""
    return sorted(OUT.glob("*.bak.zip"))


def make(full: bool) -> Path | None:
    """Write one archive (full or changed files only); return it, or None when nothing changed."""
    OUT.mkdir(parents=True, exist_ok=True)
    state_p = OUT / "state.json"
    old = json.loads(state_p.read_text(encoding="utf-8")) if state_p.exists() else {}
    fulls = [v for v in versions() if "_full" in v.name]
    newest_full = fulls[-1].stat().st_mtime if fulls else 0
    full = full or not old or (time.time() - newest_full) > FULL_EVERY_DAYS * 86400
    now = scan()
    added, changed, deleted, pick = [], [], [], []
    for src, files in now.items():
        before = old.get(src, {})
        for rel, sig in files.items():
            if rel not in before:
                added.append(f"{src}/{rel}")
            elif before[rel] != sig:
                changed.append(f"{src}/{rel}")
            else:
                if full:
                    pick.append((src, rel))
                continue
            pick.append((src, rel))
        deleted += [f"{src}/{rel}" for rel in before if rel not in files]
    if not full and not (added or changed or deleted):
        print("no changes since the last version")
        return None
    kind = "full" if full else "changes"
    arc = OUT / f"{time.strftime('%Y-%m-%d_%H%M')}_{kind}.bak.zip"
    tmp = arc.with_suffix(".part")
    errors = []
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_LZMA) as z:
        for src, rel in pick:
            try:
                z.write(os.path.join(SOURCES[src], rel), f"{src}/{rel}")
            except OSError as e:  # a file locked or gone mid-run: noted, the rest still saved
                errors.append(f"{src}/{rel}: {e}")
        manifest = [f"# {kind} version {arc.name}", f"# sources: {json.dumps(SOURCES)}"]
        manifest += [f"A {p}" for p in added] + [f"M {p}" for p in changed] + [f"D {p}" for p in deleted]
        manifest += [f"! {e}" for e in errors]
        z.writestr("MANIFEST.txt", "\n".join(manifest) + "\n")
    tmp.replace(arc)  # appears only when complete
    state_p.write_text(json.dumps(now), encoding="utf-8")
    print(f"{arc.name}: {len(pick)} files ({len(added)} new, {len(changed)} changed, {len(deleted)} deleted), "
          f"{arc.stat().st_size / 1e6:.1f} MB" + (f", {len(errors)} unreadable" if errors else ""))
    return arc


def prune() -> None:
    """Delete versions older than KEEP_DAYS, but keep the newest full the remaining change-sets need."""
    cutoff = time.time() - KEEP_DAYS * 86400
    old = [v for v in versions() if v.stat().st_mtime < cutoff]
    keep_full = [v for v in old if "_full" in v.name][-1:]
    for v in old:
        if v not in keep_full:
            v.unlink()
            print("pruned", v.name)


def restore(day: str, to: str, only: str | None) -> None:
    """Rebuild the tree as of the end of `day`: newest full on/before it, then every change-set after."""
    vs = [v for v in versions() if v.name[:10] <= day]
    fulls = [i for i, v in enumerate(vs) if "_full" in v.name]
    if not fulls:
        sys.exit(f"no full version on or before {day}")
    chain = vs[fulls[-1]:]
    dest = Path(to)
    for v in chain:
        with zipfile.ZipFile(v) as z:
            for n in z.namelist():
                if n == "MANIFEST.txt" or (only and not n.startswith(only + "/")):
                    continue
                z.extract(n, dest)
            for line in z.read("MANIFEST.txt").decode("utf-8").splitlines():
                if line.startswith("D "):
                    p = dest / line[2:]
                    if (not only or line[2:].startswith(only + "/")) and p.is_file():
                        p.unlink()
    print(f"restored {len(chain)} version(s) up to {chain[-1].name} into {dest}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--restore")
    ap.add_argument("--to")
    ap.add_argument("--source")
    a = ap.parse_args()
    if a.list:
        for v in versions():
            print(f"{v.name}  {v.stat().st_size / 1e6:.1f} MB")
    elif a.restore:
        if not a.to:
            sys.exit("--restore needs --to <empty folder>")
        restore(a.restore, a.to, a.source)
    else:
        make(a.full)
        prune()
