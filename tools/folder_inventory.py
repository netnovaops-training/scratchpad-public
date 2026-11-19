#!/usr/bin/env python3
"""folder_inventory.py - read-only list of every top folder on this machine, one CSV row each (T0002 step 1).

Usage:
    python3 folder_inventory.py MACHINE OUT.csv ROOT[:DEPTH] [ROOT[:DEPTH] ...]
    e.g. python3 folder_inventory.py nuc inv.csv /home/user:1 /mnt/vault:1
         python  folder_inventory.py pc  inv.csv "C:\\Users\\USER:1" "D:\\:1"

One row per folder DEPTH levels below each ROOT (depth 1 = the root's children), plus one row for loose
files sitting directly in that root. Columns:
    machine, path, size_bytes, size_human, files, dirs, newest_change, placeholders, errors, git, git_remote
- size/files/newest come from directory listings only (os.scandir + lstat). File contents are NEVER read, so
  online-only cloud placeholders (M:\\Pictures\\SynologyDrive, OneDrive; T0005 warning) are not downloaded.
  Windows placeholders are counted in "placeholders" (attribute RECALL_ON_DATA_ACCESS / RECALL_ON_OPEN / OFFLINE);
  their size is the logical size, not space on disk.
- Symlinks and Windows junctions are not followed (no loops, no double counting).
- git: "repo" if the folder is a work tree, "inside:<path>" if a parent is, "N repos inside" if it holds repos,
  "none" otherwise. git_remote is the origin URL with any user:password@ part removed (never print secrets).
- Unreadable folders are counted in "errors", never fatal.
Read-only: the script writes only OUT.csv.
OUTCOME: (2026-11-18) written for T0002 slice 1; results in ~/nuc-homelab/audit/inventory/.
"""
import csv
import os
import re
import stat
import sys
import time

SKIP_WIN = {"$recycle.bin", "system volume information", "windows", "program files", "program files (x86)",
            "programdata", "recovery", "$windows.~bt", "$winreagent", "config.msi", "pagefile.sys", "hiberfil.sys",
            "swapfile.sys", "dumpstack.log.tmp"}
SKIP_LINUX = {"proc", "sys", "dev", "run"}
PLACEHOLDER = 0x00400000 | 0x00040000 | 0x00001000  # RECALL_ON_DATA_ACCESS | RECALL_ON_OPEN | OFFLINE
CRED = re.compile(r"(://)[^/@\s]+@")


def human(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0


def is_link(entry):
    """Symlink or Windows junction/reparse point: listed, never entered."""
    try:
        if entry.is_symlink():
            return True
        st = entry.stat(follow_symlinks=False)
        return bool(getattr(st, "st_file_attributes", 0) & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except OSError:
        return True


def origin(gitdir):
    """origin URL from .git/config with credentials stripped; '' when none."""
    try:
        with open(os.path.join(gitdir, "config"), encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return ""
    m = re.search(r'\[remote "origin"\][^\[]*?url\s*=\s*(\S+)', text)
    return CRED.sub(r"\1", m.group(1)) if m else ""


def parent_repo(path):
    p = os.path.dirname(os.path.abspath(path))
    while True:
        if os.path.isdir(os.path.join(p, ".git")):
            return p
        up = os.path.dirname(p)
        if up == p:
            return ""
        p = up


def walk(top):
    """Totals for one folder: size, files, dirs, newest mtime, placeholders, errors, repos found inside."""
    t = {"size": 0, "files": 0, "dirs": 0, "newest": 0.0, "ph": 0, "err": 0, "repos": 0}
    stack = [top]
    while stack:
        d = stack.pop()
        try:
            it = os.scandir(d)
        except OSError:
            t["err"] += 1
            continue
        with it:
            for e in it:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    t["err"] += 1
                    continue
                t["newest"] = max(t["newest"], st.st_mtime)
                if stat.S_ISDIR(st.st_mode) and not is_link(e):
                    t["dirs"] += 1
                    if e.name == ".git":
                        t["repos"] += 1
                    stack.append(e.path)
                elif stat.S_ISREG(st.st_mode):
                    t["files"] += 1
                    t["size"] += st.st_size
                    if getattr(st, "st_file_attributes", 0) & PLACEHOLDER:
                        t["ph"] += 1
    return t


def targets(root, depth):
    """Folders exactly `depth` levels below root (shallower ones too if they have no subfolders)."""
    skip = SKIP_WIN if os.name == "nt" else SKIP_LINUX
    level = [root]
    for _ in range(depth):
        nxt = []
        for d in level:
            try:
                kids = [e for e in os.scandir(d) if e.is_dir(follow_symlinks=False) and not is_link(e)
                        and e.name.lower() not in skip]
            except OSError:
                continue
            nxt.extend(sorted(e.path for e in kids))
        level = nxt
    return level


def row(machine, path, t, git, remote):
    newest = time.strftime("%Y-%m-%d %H:%M", time.localtime(t["newest"])) if t["newest"] else ""
    return [machine, path, t["size"], human(t["size"]), t["files"], t["dirs"], newest, t["ph"], t["err"], git, remote]


def main(machine, out, specs):
    rows = []
    for spec in specs:
        root, _, dep = spec.rpartition(":") if re.search(r":\d+$", spec) else (spec, "", "1")
        depth = int(dep)
        if not os.path.isdir(root):
            print("missing root:", root, file=sys.stderr)
            continue
        # loose files directly in the root
        loose = {"size": 0, "files": 0, "dirs": 0, "newest": 0.0, "ph": 0, "err": 0}
        try:
            for e in os.scandir(root):
                if e.is_file(follow_symlinks=False):
                    st = e.stat(follow_symlinks=False)
                    loose["files"] += 1
                    loose["size"] += st.st_size
                    loose["newest"] = max(loose["newest"], st.st_mtime)
        except OSError:
            loose["err"] += 1
        rows.append(row(machine, os.path.join(root, "(loose files)"), loose, "", ""))
        for d in targets(root, depth):
            t0 = time.time()
            t = walk(d)
            if os.path.isdir(os.path.join(d, ".git")):
                git, remote = "repo", origin(os.path.join(d, ".git"))
            else:
                pr = parent_repo(d)
                if pr:
                    git, remote = "inside:" + pr, origin(os.path.join(pr, ".git"))
                elif t["repos"]:
                    git, remote = "{} repos inside".format(t["repos"]), ""
                else:
                    git, remote = "none", ""
            rows.append(row(machine, d, t, git, remote))
            print("{:6.1f}s {!s:>10} {}".format(time.time() - t0, human(t["size"]), d), file=sys.stderr, flush=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["machine", "path", "size_bytes", "size_human", "files", "dirs", "newest_change",
                    "placeholders", "errors", "git", "git_remote"])
        w.writerows(rows)
    print("rows", len(rows), "->", out)


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
