#!/usr/bin/env python3
"""unified_backup.py - one unified copy of the whole site (PC, NUC, Pi, Garden for All) on the NAS, never overwriting.

How to run (PC):  py -3 -I D:\\scratchpad\\tools\\unified_backup.py            full run
                  py -3 -I D:\\scratchpad\\tools\\unified_backup.py --dry      count only, write nothing
                  py -3 -I D:\\scratchpad\\tools\\unified_backup.py --only pi-site   one source (names below)
Where it writes:  \\\\192.168.1.68\\Website backup\\unified\\  (NAS), plus in that folder:
                  _manifest.csv (path, sha256, size, first source), _CONFLICTS.csv, _runs.log
How to test:      run twice; the second run must report "new 0" and "variants 0" if nothing changed.

THE RULE (Jason, 2026-11-16): a unified copy, never different copies over each other. Per file:
  - path free                     -> copy it under its normal name
  - same content already there    -> skip (it is the same file, maybe from another computer)
  - DIFFERENT content at that path -> keep BOTH: the new one goes next to it as
                                      name.~<computer>-<first 8 of sha256>.ext and a row in _CONFLICTS.csv
  Nothing on the NAS is ever replaced or deleted (files are written as .part, then renamed; rename fails if
  the name exists). Paths are compared case-insensitively because the NAS share is.
Source order decides who gets the plain name when two differ: PC first (the working copy), then NUC, then Pi.
Left out, as in Backup-ToNas.ps1: .git (repos are on Gitea), build/venv/cache folders, and .env files
(secrets live in Proton Pass; .env.example is kept).
Reads: the PC folders directly; the NUC and Pi as tar streams over ssh (read-only on their side). Files a
remote user cannot read (root-owned, e.g. auth-data) are skipped by tar and listed in _runs.log.
OUTCOME 2026-11-16: written for the first unified run; see _runs.log for counts.
"""
from __future__ import annotations

import csv
import hashlib
import os
import re
import subprocess
import sys
import tarfile
import time
from datetime import datetime
from pathlib import Path, PureWindowsPath

DEST = Path(r"\\192.168.1.68\Website backup\unified")
NUC = ["ssh", "-o", "BatchMode=yes", "server@192.168.1.86"]
PI = ["ssh", "-o", "BatchMode=yes", "-p", "1143", "jason@192.168.1.69"]
# (name, computer, kind, where, prefix in the unified tree) - order matters, see the docstring
SOURCES = [
    ("pc-site", "pc", "local", r"C:\Jasons New Site\www", "website"),
    ("pc-garden", "pc", "local", r"D:\Garden for all", "garden-for-all"),   # includes the backlog (_local-data\pipeline)
    ("pc-garden-website", "pc", "local", r"C:\Users\USER\Projects\garden-for-all-website", "garden-for-all-repos/website"),
    ("pc-garden-rust", "pc", "local", r"C:\Users\USER\Projects\garden-for-all-rust", "garden-for-all-repos/rust"),
    # the phone apps (Sprout Pals Android + iPhone, Cut The Fluff, MessageJay, Robin's Garden app); added 11-16
    ("pc-phone-apps", "pc", "local", r"C:\Users\USER\AndroidStudioProjects", "phone-apps"),
    ("nuc-homelab", "nuc", "ssh", (NUC, "nuc-homelab"), "nuc-homelab"),
    ("nuc-learning-src", "nuc", "ssh", (NUC, "learning-src"), "learning-src"),
    ("nuc-bin", "nuc", "ssh", (NUC, "bin"), "nuc-bin"),
    ("nuc-garden", "nuc", "ssh", (NUC, "garden-for-all"), "garden-for-all"),
    ("pi-site", "pi", "ssh", (PI, "Jasons-Site"), "website"),
]
SKIP_DIRS = {".git", "node_modules", "target", ".venv", "venv", "__pycache__", ".claude", ".cache", "secrets-review",
             "env-backups", "secrets-ui-test", "work-full", "work-current", "work-gaptest", "work-hosts",
             "$recycle.bin", "system volume information",
             "build", "build-release", ".gradle", ".cxx", ".idea", ".kotlin"}   # Android/Gradle output, rebuilt from source
BAD_CHARS = re.compile(r'[<>:"\\|?*\x00-\x1f]')   # not allowed in Windows/SMB names


def skip_file(name: str) -> bool:
    """True for secret-bearing env files (but .env.example and friends are kept)."""
    n = name.lower()
    return (n == ".env" or n.endswith(".env") or n.startswith(".env.")) and not n.endswith((".example", ".sample", ".template"))


def skip_path(parts) -> bool:
    return any(p.lower() in SKIP_DIRS for p in parts)


class Unified:
    """Holds the manifest and makes the copy / skip / variant decision for each file."""

    def __init__(self, dry: bool):
        self.dry = dry
        self.man: dict[str, tuple[str, int, str]] = {}   # lower(rel) -> (sha, size, source)
        self.conflicts: list[list[str]] = []
        self.n = {"new": 0, "same": 0, "variants": 0, "variant-known": 0, "renamed-chars": 0, "errors": 0}
        mf = DEST / "_manifest.csv"
        if mf.exists():
            with mf.open(encoding="utf-8", newline="") as f:
                for row in csv.reader(f):
                    if row and row[0] != "path":
                        self.man[row[0].lower()] = (row[1], int(row[2]), row[3])

    def existing_sha(self, rel: str) -> str | None:
        """sha256 of what is already on the NAS at rel (manifest first, else hash the file), None if free."""
        hit = self.man.get(rel.lower())
        if hit:
            return hit[0]
        p = DEST / rel
        if not p.exists():
            return None
        h = hashlib.sha256()
        with p.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        self.man[rel.lower()] = (h.hexdigest(), p.stat().st_size, "found-on-nas")
        return h.hexdigest()

    def put(self, rel: str, data: bytes, mtime: float, source: str, computer: str):
        clean = "/".join(BAD_CHARS.sub("_", part) for part in rel.split("/"))
        if clean != rel:
            self.n["renamed-chars"] += 1
        rel = clean
        sha = hashlib.sha256(data).hexdigest()
        have = self.existing_sha(rel)
        if have == sha:
            self.n["same"] += 1
            return
        target = rel
        if have is not None:   # different content at the same path: keep both
            stem, dot, ext = rel.rpartition(".") if "." in rel.rsplit("/", 1)[-1] else (rel, "", "")
            target = f"{stem}.~{computer}-{sha[:8]}{dot}{ext}" if dot else f"{rel}.~{computer}-{sha[:8]}"
            if self.existing_sha(target) == sha:
                self.n["variant-known"] += 1
                return
            self.conflicts.append([rel, have[:12], target, sha[:12], source])
            self.n["variants"] += 1
        else:
            self.n["new"] += 1
        self.man[target.lower()] = (sha, len(data), source)
        if self.dry:
            return
        out = DEST / target
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            part = out.with_name(out.name + ".part")
            part.write_bytes(data)
            os.rename(part, out)          # fails if `out` exists: nothing is ever replaced
            os.utime(out, (mtime, mtime))
        except OSError as e:
            self.n["errors"] += 1
            self.log(f"ERROR {target}: {e}")

    def log(self, msg: str):
        line = f"{datetime.now():%Y-%m-%d %H:%M:%S} {msg}"
        print(line, flush=True)
        if not self.dry:
            with (DEST / "_runs.log").open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    def save(self):
        if self.dry:
            return
        tmp = DEST / "_manifest.csv.new"
        with tmp.open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["path", "sha256", "size", "source"])
            for k, (s, z, src) in sorted(self.man.items()):
                w.writerow([k, s, z, src])
        os.replace(tmp, DEST / "_manifest.csv")   # the manifest is ours to replace; backed-up files never are
        if self.conflicts:
            new = not (DEST / "_CONFLICTS.csv").exists()
            with (DEST / "_CONFLICTS.csv").open("a", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["path", "sha_already_there", "kept_as", "sha_new", "from_source"])
                w.writerows(self.conflicts)
            self.conflicts = []


def local_files(root: str):
    base = Path(root)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
        for fn in filenames:
            if skip_file(fn):
                continue
            p = Path(dirpath, fn)
            try:
                st = p.stat()
                if st.st_size > 4 << 30:
                    continue
                yield PureWindowsPath(p.relative_to(base)).as_posix(), p.read_bytes(), st.st_mtime
            except OSError:
                continue


def remote_files(ssh: list[str], folder: str, u: Unified):
    excl = " ".join(f"--exclude='{d}'" for d in SKIP_DIRS if "$" not in d and " " not in d)
    cmd = ssh + [(f"cd ~ && tar -cf - --ignore-failed-read {excl} {folder} 2>/tmp/unified-tar-errors.txt; "
                 f"echo unreadable: $(grep -c 'Cannot open' /tmp/unified-tar-errors.txt) >&2")]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
        for m in tf:
            if not m.isfile():
                continue
            parts = m.name.split("/")[1:]          # drop the top folder name
            if not parts or skip_path(parts[:-1]) or skip_file(parts[-1]):
                continue
            f = tf.extractfile(m)
            if f:
                yield "/".join(parts), f.read(), float(m.mtime)
    err = proc.stderr.read().decode("utf-8", "replace").strip()
    proc.wait()
    u.log(f"  {folder}: {err or 'tar ok'}")


def main():
    dry = "--dry" in sys.argv
    only = sys.argv[sys.argv.index("--only") + 1] if "--only" in sys.argv else None
    if not dry:
        DEST.mkdir(parents=True, exist_ok=True)
    u = Unified(dry)
    u.log(f"start {'(dry run) ' if dry else ''}-> {DEST}")
    for name, computer, kind, where, prefix in SOURCES:
        if only and name != only:
            continue
        t0, before = time.time(), dict(u.n)
        files = local_files(where) if kind == "local" else remote_files(where[0], where[1], u)
        for i, (rel, data, mtime) in enumerate(files, 1):
            u.put(f"{prefix}/{rel}", data, mtime, name, computer)
            if i % 5000 == 0:
                u.log(f"  {name}: {i} files ...")
                u.save()
        diff = {k: u.n[k] - before[k] for k in u.n}
        u.log(f"{name}: {diff} in {time.time() - t0:.0f}s")
        u.save()
    u.log(f"done: {u.n}")
    return 1 if u.n["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
