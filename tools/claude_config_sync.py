#!/usr/bin/env python3
"""claude_config_sync.py - copy Claude's memory and skills into D:\\claude-config and commit, so they get a history.

Why (T0076, Jason 2026-11-21 answer a): memory and skills were only in Kopia snapshots (whole files, no diff, no
"why did this rule change"). Now a private Gitea repo (Jason/claude-config) keeps every change.

    py -3.14 -I D:\\scratchpad\\tools\\claude_config_sync.py          copy, commit if anything changed (push via hook)
    py -3.14 -I D:\\scratchpad\\tools\\claude_config_sync.py --dry    say what would change, write nothing

Route (same as the Scratchpad, no Gitea password on the PC): commit in D:\\claude-config -> its post-commit hook
pushes to the NUC bare repo ~/git/claude-config.git over SSH -> the NUC's post-receive hook pushes to Gitea
(private, created on first push). The shared pre-commit hook (core.hooksPath, gitleaks) runs on every commit;
a secret found there stops the commit, and this script reports FAIL.
Copied: the memory folder (*.md) and the skills folder, minus caches. NOT copied: anything else under ~/.claude
(credentials, settings, history, transcripts). A file removed at the source is removed from the copy too
(mirror); git keeps its history.

Variables: SRC (source folder -> folder in the repo), REPO, SKIP_DIRS / SKIP_EXT (never copied), LOG.
Exit: 0 ok or nothing to do, 1 commit refused (secret or git error; see LOG).
OUTCOME 2026-11-21: first run committed the full copy and Gitea received it (see T0076 Review).
"""
from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
import time
from pathlib import Path

CLAUDE = Path.home() / ".claude"
SRC = {CLAUDE / "projects" / "C--Users-USER-Projects-Projects-repo" / "memory": "memory",
       CLAUDE / "skills": "skills"}
REPO = Path(r"D:\claude-config")
SKIP_DIRS = {"__pycache__", ".git", "node_modules", ".venv"}
SKIP_EXT = {".pyc", ".pyo", ".log"}
LOG = REPO / ".git" / "sync.log"


def wanted(p: Path) -> bool:
    """True for files that belong in the copy (no caches, no logs)."""
    return p.is_file() and not (set(p.parts) & SKIP_DIRS) and p.suffix.lower() not in SKIP_EXT


def mirror(src: Path, dst: Path, dry: bool) -> list[str]:
    """Make dst equal to src (wanted files only); return 'add/upd/del path' lines."""
    changes = []
    want = {p.relative_to(src) for p in src.rglob("*") if wanted(p)}
    have = {p.relative_to(dst) for p in dst.rglob("*") if p.is_file()} if dst.exists() else set()
    for rel in sorted(want):
        s, d = src / rel, dst / rel
        if not d.exists() or not filecmp.cmp(s, d, shallow=False):
            changes.append(("upd " if d.exists() else "add ") + str(dst.name / rel))
            if not dry:
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
    for rel in sorted(have - want):
        changes.append("del " + str(dst.name / rel))
        if not dry:
            (dst / rel).unlink()   # the copy only; the file stays in git history and at its (moved) source
    return changes


def git(*a: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True, check=False)


def main(argv: list[str]) -> int:
    dry = "--dry" in argv
    changes = []
    for src, name in SRC.items():
        changes += mirror(src, REPO / name, dry)
    stamp = time.strftime("%Y-%m-%d %H:%M")
    # Also commit what an earlier run copied but could not commit. OUTCOME 2026-11-21: the first run failed
    # (no git identity), the second found the files already equal and said "nothing changed", so the copy
    # would never have been committed.
    pending = git("status", "--porcelain", "--", "memory", "skills").stdout.strip()
    if not changes and not pending:
        print("PASS nothing changed")
        return 0
    if not changes:
        changes = [ln[3:] for ln in pending.splitlines()]
    print(f"{'DRY ' if dry else ''}{len(changes)} change(s): " + "; ".join(changes[:8]) + (" ..." if len(changes) > 8 else ""))
    if dry:
        return 0
    git("add", "-A", "memory", "skills")
    r = git("commit", "-q", "-m", f"sync {stamp}: {len(changes)} change(s)")
    ok = r.returncode == 0
    with LOG.open("a", encoding="utf-8") as f:
        f.write(f"{stamp} {'PASS' if ok else 'FAIL'} {len(changes)} change(s)"
                + ("" if ok else " | " + (r.stdout + r.stderr).strip().replace("\n", " ")[-300:]) + "\n")
    print(("PASS committed" if ok else "FAIL commit refused (see .git/sync.log)"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
