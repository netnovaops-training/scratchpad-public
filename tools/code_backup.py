#!/usr/bin/env python3
"""code_backup.py - back up ALL code to the NAS (2026-11-23, Jason: "Backup the entire code base to the nas all of it").

    py -3.14 -I D:\\scratchpad\\tools\\code_backup.py            full run (PC + NUC + Pi), then copy to the NAS
    py -3.14 -I D:\\scratchpad\\tools\\code_backup.py --pc-only  just the PC part (test)

Where it lands: \\\\192.168.1.68\\Computer backup\\backups\\code-backup\\<YYYY-MM-DD>\\{pc,nuc,pi}\\ (the share
KopiaUI uses; this folder sits beside, not inside, Kopia's own files). The last KEEP (8) dated folders are kept.
What each repo becomes:
  <name>.bundle       `git bundle create --all` (every branch, tag, commit), checked with `git bundle verify`
  <name>.dirty.tar    files changed or untracked in the working tree (not committed yet), if any; files over
                      DIRTY_MAX_MB (200) are listed in MANIFEST instead of copied
NUC: every working repo under ~ and every bare repo (~/git, ~/git/mirrors, ~/repos) as bundles, plus `gitea dump`
(all Gitea repos, issues, settings). Pi: its repos (~/Jasons-Site, ~/stack if git). PC: repos under
C:\\Jasons New Site\\www, C:\\Users\\USER\\Projects (NOT Projects-repo: Jason's rule) and D:\\ (depth 4), one bundle
per repository (worktrees share their main repo's bundle; their dirty files get their own tar).
MANIFEST.tsv: path, bytes, sha256, verify result. SUMMARY.txt: counts and failures. Nothing is deleted from any
source. Exit 1 if any bundle failed to verify.
Variables: DATE, STAGE (local staging on D:), NAS, results (rows for MANIFEST).
"""
from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import time

DATE = time.strftime("%Y-%m-%d")
STAGE = pathlib.Path(r"D:\code-backup") / DATE
NAS = pathlib.Path(r"\\192.168.1.68\Computer backup\backups\code-backup")
KEEP = 8
DIRTY_MAX = 200 * 1048576
NUC = "server@100.64.0.10"
PI = ["-p", "1143", "jason@100.64.0.11"]
PC_ROOTS = [pathlib.Path(r"C:\Jasons New Site\www"), pathlib.Path(r"C:\Users\USER\Projects"), pathlib.Path("D:/")]
SKIP = re.compile(r"node_modules|\\\.claude\\worktrees|Projects-repo|\\\$RECYCLE\.BIN|^D:\\archive|^D:\\code-backup", re.I)
results: list[tuple] = []


def git(repo, *args, **kw):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, **kw)


def safe(p: pathlib.Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(p).replace(":", "")).strip("_")[:150]


def find_pc_repos() -> list[pathlib.Path]:
    found = []
    for root in PC_ROOTS:
        base_depth = len(root.parts)
        for dirpath, dirnames, filenames in os.walk(root):
            if SKIP.search(dirpath) or len(pathlib.Path(dirpath).parts) - base_depth > 4:
                dirnames[:] = []
                continue
            if ".git" in dirnames or ".git" in filenames:
                found.append(pathlib.Path(dirpath))
            dirnames[:] = [d for d in dirnames if d != ".git" and not SKIP.search(os.path.join(dirpath, d))]
    return sorted(set(found))


def bundle_pc(repo: pathlib.Path, out: pathlib.Path, done_common: set) -> None:
    common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir", text=True).stdout.strip()
    if not common:
        results.append((str(repo), 0, "", "not a repo"))
        return
    if common not in done_common and git(repo, "rev-parse", "--verify", "-q", "HEAD").returncode == 0:
        done_common.add(common)
        b = out / f"{safe(repo)}.bundle"
        r = git(repo, "bundle", "create", str(b), "--all")
        ok = r.returncode == 0 and git(repo, "bundle", "verify", str(b)).returncode == 0
        results.append((str(b.relative_to(STAGE)), b.stat().st_size if b.exists() else 0, sha(b), "ok" if ok else "VERIFY FAILED"))
    dirty = git(repo, "ls-files", "-m", "-o", "--exclude-standard", "-z").stdout.split(b"\0")
    dirty = [d.decode("utf-8", "surrogateescape") for d in dirty if d]
    if dirty:
        t = out / f"{safe(repo)}.dirty.tar"
        skipped = []
        with tarfile.open(t, "w") as tar:
            for rel in dirty:
                f = repo / rel
                try:
                    if f.is_file() and f.stat().st_size <= DIRTY_MAX:
                        tar.add(f, arcname=rel)
                    elif f.is_file():
                        skipped.append(rel)
                except OSError:
                    skipped.append(rel)
        results.append((str(t.relative_to(STAGE)), t.stat().st_size, sha(t), f"dirty {len(dirty)} files, {len(skipped)} too big/unreadable"))


def sha(p: pathlib.Path) -> str:
    if not p.exists():
        return ""
    h = hashlib.sha256()
    with p.open("rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


NUC_SCRIPT = r"""
set -u
D=~/code-backup-staging/__DATE__; rm -rf ~/code-backup-staging; mkdir -p $D
safe() { echo "$1" | sed -E 's#^/home/user/##; s#[^A-Za-z0-9._-]+#_#g'; }
for r in $(find ~ -maxdepth 4 -name .git -not -path "*/archive/*" -not -path "*/sandbox/*" -not -path "*/.local/*" -not -path "*/node_modules/*" -not -path "*/code-backup-staging/*" 2>/dev/null | sed 's#/.git$##'); do
  n=$(safe $r); git -C $r rev-parse -q --verify HEAD >/dev/null 2>&1 || continue
  git -C $r bundle create $D/$n.bundle --all >/dev/null 2>&1 && git -C $r bundle verify $D/$n.bundle >/dev/null 2>&1 && echo "ok $n.bundle" || echo "FAIL $n.bundle"
  git -C $r ls-files -m -o --exclude-standard -z | (cd $r && tar --null -T - --exclude-vcs -cf $D/$n.dirty.tar 2>/dev/null); [ -s $D/$n.dirty.tar ] || rm -f $D/$n.dirty.tar
done
for r in ~/git/*.git ~/git/mirrors/*.git ~/repos/*.git; do
  [ -d $r ] || continue; n=bare_$(safe $r)
  git -C $r bundle create $D/$n.bundle --all >/dev/null 2>&1 && git -C $r bundle verify $D/$n.bundle >/dev/null 2>&1 && echo "ok $n.bundle" || echo "FAIL $n.bundle"
done
G=$(docker ps --format '{{.Names}}' | grep -E 'gitea-1$' | head -1)
docker exec -u git $G sh -c 'cd /tmp && rm -f gitea-dump.zip && gitea dump -c /data/gitea/conf/app.ini -f /tmp/gitea-dump.zip >/dev/null 2>&1' && docker cp $G:/tmp/gitea-dump.zip $D/gitea-dump.zip >/dev/null && docker exec $G rm -f /tmp/gitea-dump.zip && echo "ok gitea-dump.zip" || echo "FAIL gitea-dump.zip"
(cd $D && sha256sum * > SHA256SUMS)
du -sh $D | cut -f1
"""

PI_SCRIPT = r"""
set -u
D=~/code-backup-staging; rm -rf $D; mkdir -p $D
for r in $(find ~ -maxdepth 3 -name .git -not -path "*/.local/*" 2>/dev/null | sed 's#/.git$##'); do
  n=$(echo $r | sed -E 's#^/home/user/##; s#[^A-Za-z0-9._-]+#_#g'); git -C $r rev-parse -q --verify HEAD >/dev/null 2>&1 || continue
  git -C $r bundle create $D/$n.bundle --all >/dev/null 2>&1 && git -C $r bundle verify $D/$n.bundle >/dev/null 2>&1 && echo "ok $n.bundle" || echo "FAIL $n.bundle"
  git -C $r ls-files -m -o --exclude-standard -z | (cd $r && tar --null -T - -cf $D/$n.dirty.tar 2>/dev/null); [ -s $D/$n.dirty.tar ] || rm -f $D/$n.dirty.tar
done
(cd $D && sha256sum * > SHA256SUMS 2>/dev/null); ls $D | wc -l
"""


def remote_part(name: str, ssh_args: list[str], script: str, remote_dir: str) -> list[str]:
    out = STAGE / name
    out.mkdir(parents=True, exist_ok=True)
    # bytes, not text=True: on Windows text mode turns "\n" into "\r\n" and bash then fails on every line ($'\r').
    # OUTCOME 2026-11-23: the first full run lost the NUC and Pi parts this way.
    r = subprocess.run(["ssh", *ssh_args, "bash -s"], input=script.replace("__DATE__", DATE).encode(),
                       capture_output=True, timeout=7200)
    lines = r.stdout.decode("utf-8", "replace").splitlines()
    port = ["-P", ssh_args[1]] if ssh_args[0] == "-p" else []
    host = ssh_args[-1]
    cp = subprocess.run(["scp", "-q", "-r", *port, f"{host}:{remote_dir}/.", str(out)], capture_output=True, text=True, timeout=7200)
    if cp.returncode != 0:
        lines.append(f"FAIL copy from {name}: {cp.stderr.strip()[-200:]}")
    subprocess.run(["ssh", *ssh_args, "rm -rf ~/code-backup-staging"], capture_output=True)
    for f in sorted(out.iterdir()):
        results.append((str(f.relative_to(STAGE)), f.stat().st_size, sha(f), "remote"))
    return lines


def main() -> int:
    if STAGE.exists():
        shutil.rmtree(STAGE)
    (STAGE / "pc").mkdir(parents=True)
    repos = find_pc_repos()
    done: set = set()
    for repo in repos:
        bundle_pc(repo, STAGE / "pc", done)
    log = [f"PC: {len(repos)} repo folders, {len(done)} repositories bundled"]
    if "--pc-only" not in sys.argv:
        log += ["NUC:"] + remote_part("nuc", [NUC], NUC_SCRIPT, f"code-backup-staging/{DATE}")
        log += ["Pi:"] + remote_part("pi", PI, PI_SCRIPT, "code-backup-staging")
    fails = [r for r in results if "FAIL" in r[3]] + [x for x in log if x.startswith("FAIL")]
    total = sum(r[1] for r in results)
    (STAGE / "MANIFEST.tsv").write_text("path\tbytes\tsha256\tcheck\n" + "".join(f"{a}\t{b}\t{c}\t{d}\n" for a, b, c, d in results), encoding="utf-8")
    (STAGE / "SUMMARY.txt").write_text("\n".join(log + [f"files: {len(results)}, {total / 1e9:.2f} GB", f"failures: {len(fails)}"] + [str(f) for f in fails]) + "\n", encoding="utf-8")
    if "--pc-only" not in sys.argv:
        dest = NAS / DATE
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(STAGE, dest)
        copied = sum(1 for _ in dest.rglob("*") if _.is_file())
        old = sorted(p for p in NAS.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name))[:-KEEP]
        for p in old:
            shutil.rmtree(p)
        log.append(f"copied {copied} files to {dest}; kept the last {KEEP} dated folders")
    print("\n".join(log))
    print(f"files: {len(results)}, {total / 1e9:.2f} GB, failures: {len(fails)}")
    for f in fails:
        print("  ", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
