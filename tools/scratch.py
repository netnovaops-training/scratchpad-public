#!/usr/bin/env python3
"""scratch.py: the scratchpad is the master copy of every script Jason runs his site with.

Jason, 2026-11-12: "make it so that all the scripts are in the scratchpad ... a unified method to
find and have it documented how they work ... every time something is added removed or changed it
has to be documented ... backed up to a separate folder ... a working version is backed up,
exported, then worked on, tested, then if it improves it is documented and merged in place of the
older version". He chose: the scratchpad is the MASTER copy; scripts keep running where they run,
and a deploy step copies the approved version there. NUC scripts are listed, never moved.

    python -I scratch.py import            copy every script from where it runs into the scratchpad
                                           (first time, or to adopt new scripts); writes MANIFEST.csv
    python -I scratch.py check             what is out of step: DRIFT (changed where it runs, not here),
                                           NEW (a script nobody imported), NODOC (no docs/<path>.md),
                                           STALEDOC (script newer than its doc). Exit 1 if any.
    python -I scratch.py start NAME        begin a change: git branch work/NAME (the working version
                                           stays on main, untouched, until the change is merged)
    python -I scratch.py finish            the change is tested: refuses unless every changed script's
                                           doc was updated too; then merges into main and deploys
    python -I scratch.py deploy PATH...    copy scratchpad version(s) to where they run; the version it
                                           replaces is kept in D:\\scratchpad-backup\\replaced\\<date>\\
    python -I scratch.py backup            dated snapshot to D:\\scratchpad-backup, then Google Drive
                                           (rclone remote jason-gdrive:scratchpad-backup); the NAS gets it
                                           through the Synology Drive task on D:\\scratchpad-backup
    python -I scratch.py nuc               refresh nuc/NUC-SCRIPTS.md: every script the NUC's version of
                                           the site runs (cron, compose, ~/learning-src, ~/nuc-homelab/bin),
                                           read over ssh, with whether it is documented

Layout:  D:\\scratchpad\\
           pc\\<area>\\<same path as where it runs>      the master copies
           docs\\pc\\<area>\\<path>.md                      one doc per script (docs/code format)
           nuc\\NUC-SCRIPTS.md                            the NUC list (read-only)
           MANIFEST.csv                                   scratchpad path -> where it runs, kind, sha256
           tools\\scratch.py                              this file
Never imported: Garden for All / Robin's Garden material, secret-looking files, outdated code
(already in D:\\ai-stack-archive), data. Every command logs OK/ERROR through joblog when available.
"""
from __future__ import annotations

import csv
import datetime
import hashlib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path(r"D:\scratchpad")
BACKUP = Path(r"D:\scratchpad-backup")
MANIFEST = HOME / "MANIFEST.csv"
AI = Path(r"C:\Jasons New Site\www\ai-stack")
NUC = "server@100.64.0.10"  # Tailscale (2026-11-17); LAN 192.168.1.86 only if Tailscale is down
GDRIVE = "jason-gdrive:scratchpad-backup"

# (area, root, recurse, kind). Order matters only for the report.
AREAS = [
    ("ai-stack", AI, True, "site code: compose services, Docker apps, launchers, scheduled tasks"),
    ("www-root", AI.parent, False, "scripts at the top of www"),
    ("pc-cleanup", AI.parent / "pc-cleanup", True, "PC clean-up scripts"),
    ("secrets-tools", Path(r"D:\secrets-tools"), True, "Proton Pass / pass-cli tools"),
    ("doc-gather", Path(r"D:\doc-gather"), True, "document gathering and sorting tools"),
    ("archive-tools", Path(r"D:\ai-stack-archive\tools"), True, "archive and tidy tools"),
    ("checks", Path.home() / ".local" / "share" / "checks", True, "shared git hooks and checks"),
]
CODE_EXT = {".py", ".ps1", ".psm1", ".cmd", ".bat", ".sh", ".bash", ".rs", ".js", ".mjs", ".ts",
            ".html", ".css", ".yml", ".yaml", ".toml", ".vbs", ".sql", ".conf", ".nginx"}
CODE_NAMES = {"Dockerfile", "Caddyfile", "Makefile", "Cargo.toml"}
SKIP_DIRS = {".git", ".claude", "node_modules", "target", "__pycache__", ".pytest_cache", "venv", ".venv",
             "All-Documents", "Audio-Files", "documents", "site-data", "secrets", "transcripts", "workspace",
             "voices", "logs", "data", "media", "vendor", "dist", "build", "duplicates", "secrets-review",
             "proton-pass-cli-docs", "public", "garden-for-all", "garden-for-robin", "nuc-files"}
GARDEN = re.compile(r"(?i)garden|garden|for-robin|(^|[^a-z])juns?([^a-z]|$)|letter")
SECRET_NAME = re.compile(r"(?i)(^|[^a-z])(\.env|token|secret|password|passwd|credential|id_rsa|id_ed25519|\.pem|\.key)([^a-z]|$)")
SECRET_TEXT = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY|ghp_[A-Za-z0-9]{30,}|sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}")
GARDEN_TEXT = re.compile(r"(?i)garden|garden-for-all|garden for all|robin's garden|letter-to-robin")
LETTER_TEXT = re.compile(r"(?i)letter-to-robin|garden-letter|letter-preview")  # private: never copied
ONE_OFF = re.compile(r"(?i)(^|/)scratchpad/files/")  # session scratch files, mostly Garden work
MAX = 512 * 1024

sys.path.insert(0, str(AI.parent / "ai-stack" / "scripts"))
try:
    import joblog  # noqa: E402  (NUC-style OK/ERROR log; optional on the PC)
except ImportError:
    joblog = None


def log(step: str, ok: bool, detail: str = "") -> None:
    print(("OK " if ok else "ERROR ") + step + (": " + detail if detail else ""))
    if joblog:
        joblog.event("scratch", step, ok, detail)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def is_code(p: Path) -> bool:
    return p.suffix.lower() in CODE_EXT or p.name in CODE_NAMES or p.name.startswith("Dockerfile")


def wanted(p: Path, rel: str) -> bool:
    if not is_code(p) or GARDEN.search(rel) or SECRET_NAME.search(p.name) or ".bak" in p.name:
        return False
    try:
        if p.stat().st_size > MAX:
            return False
        text = p.read_text("utf-8", "replace")
        if SECRET_TEXT.search(text) or LETTER_TEXT.search(text):
            return False
        # Garden one-offs (e.g. scratchpad/files/diag*.sh: cd ~/garden-for-robin) stay out.
        # 2026-11-13: this used to apply everywhere, which also dropped 148 site files that only
        # NAME Garden in a skip list or a route (rust/auth main.rs, web/Caddyfile, doc_backfill.py,
        # Update-Stack.ps1). Now only throwaway session folders are filtered by their text.
        return not (GARDEN_TEXT.search(text) and ONE_OFF.search(rel))
    except OSError:
        return False


def sources():
    """(area, rel, run_path, kind) for every script where it runs today."""
    for area, root, recurse, kind in AREAS:
        if not root.is_dir():
            continue
        for dp, dns, fns in os.walk(root):
            dns[:] = sorted(d for d in dns if d not in SKIP_DIRS and not d.startswith(("bak-", ".")) and ";" not in d)
            if not recurse:
                dns[:] = []
            for f in sorted(fns):
                p = Path(dp, f)
                rel = p.relative_to(root).as_posix()
                # colibri is an upstream project: only Jason's own top-level additions belong here.
                # doc-gather/nuc-email holds working copies of NUC scripts: those live on the NUC.
                if (rel.startswith("colibri/") and rel.count("/") > 1) or rel.startswith("nuc-email/"):
                    continue
                if wanted(p, rel):
                    yield area, rel, p, kind
    yield from desktop_targets()


def desktop_targets():
    """Scripts that desktop shortcuts start (e.g. 'Restart Docker', 'update all apps')."""
    ps = ("$s=New-Object -ComObject WScript.Shell; foreach($d in @([Environment]::GetFolderPath('Desktop'),"
          "\"$env:PUBLIC\\Desktop\",\"$env:USERPROFILE\\Desktop\")){ if(Test-Path $d){ Get-ChildItem $d -Filter *.lnk |"
          " ForEach-Object { $l=$s.CreateShortcut($_.FullName); $_.BaseName + '|' + $l.TargetPath + '|' + $l.Arguments } } }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                             timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return
    seen = set()
    for line in out.splitlines():
        name, target, args = (line.split("|") + ["", ""])[:3]
        for cand in [target] + re.findall(r'"([^"]+\.(?:ps1|py|cmd|bat|vbs|sh))"|(\S+\.(?:ps1|py|cmd|bat|vbs|sh))', args):
            c = cand if isinstance(cand, str) else (cand[0] or cand[1])
            p = Path(c) if c else None
            if p and p.is_file() and is_code(p) and str(p).lower() not in seen:
                seen.add(str(p).lower())
                yield "desktop", f"{name}/{p.name}", p, f"started by the desktop shortcut '{name}'"


def doc_for(area: str, rel: str) -> Path:
    return HOME / "docs" / "pc" / area / (rel + ".md")


def cmd_import() -> int:
    rows, n_doc = [], 0
    for area, rel, run, kind in sources():
        dst = HOME / "pc" / area / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(run, dst)
        doc = doc_for(area, rel)
        src_doc = AI / "docs" / "code" / (rel + ".md") if area == "ai-stack" else None
        if src_doc and src_doc.is_file() and not doc.exists():
            doc.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_doc, doc)
        n_doc += doc.exists()
        rows.append([f"pc/{area}/{rel}", str(run), kind, sha(dst), "yes" if doc.exists() else "no"])
    with MANIFEST.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["scratchpad_path", "runs_from", "kind", "sha256", "documented"])
        w.writerows(rows)
    log("import", True, f"{len(rows)} scripts, {n_doc} documented, {len(rows) - n_doc} need a doc")
    return 0


def manifest():
    if not MANIFEST.exists():
        return []
    return list(csv.DictReader(MANIFEST.open(encoding="utf-8")))


def cmd_check() -> int:
    rows, problems = manifest(), []
    known = {r["runs_from"].lower() for r in rows}
    for r in rows:
        run, mine = Path(r["runs_from"]), HOME / r["scratchpad_path"]
        doc = doc_for(*r["scratchpad_path"].split("/", 2)[1:])
        if not run.exists():
            problems.append(("GONE", r["scratchpad_path"], "no longer where it ran"))
            continue
        if mine.exists() and sha(run) != sha(mine):
            problems.append(("DRIFT", r["scratchpad_path"], "changed where it runs, not in the scratchpad"))
        if not doc.exists():
            problems.append(("NODOC", r["scratchpad_path"], "no doc"))
        elif mine.exists() and mine.stat().st_mtime > doc.stat().st_mtime + 60:
            problems.append(("STALEDOC", r["scratchpad_path"], "script changed after its doc"))
    for area, rel, run, _kind in sources():
        if str(run).lower() not in known:
            problems.append(("NEW", f"pc/{area}/{rel}", "not in the scratchpad yet (run import)"))
    from collections import Counter
    c = Counter(k for k, _p, _d in problems)
    report = HOME / "CHECK.md"
    report.write_text("# Scratchpad check " + datetime.datetime.now().strftime("%Y-%m-%d %H:%M") + "\n\n"
                      + "".join(f"- **{k}** `{p}`: {d}\n" for k, p, d in sorted(problems)), encoding="utf-8")
    # NODOC alone is the known backlog: warn in the report, but only real drift counts as an error.
    bad = {k: v for k, v in c.items() if k in ("DRIFT", "GONE", "STALEDOC")}
    log("check", not bad, ", ".join(f"{k} {v}" for k, v in sorted(c.items())) or "all in step")
    return 1 if bad else 0


def git(*a, check=True):
    return subprocess.run(["git", "-C", str(HOME), *a], capture_output=True, text=True, check=check)


def cmd_start(name: str) -> int:
    git("checkout", "-b", f"work/{name}")
    log("start", True, f"branch work/{name}; main keeps the working version")
    return 0


def cmd_finish() -> int:
    br = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if not br.startswith("work/"):
        log("finish", False, "not on a work/ branch")
        return 1
    changed = [p for p in git("diff", "--name-only", "main...HEAD").stdout.split() + git("status", "--porcelain").stdout.split()
               if p.startswith("pc/")]
    missing = [p for p in changed if not any(d.startswith("docs/" + p) for d in
               git("diff", "--name-only", "main...HEAD").stdout.split() + git("status", "--porcelain").stdout.split())]
    if missing:
        log("finish", False, "update the doc for: " + ", ".join(missing[:10]))
        return 1
    git("add", "-A")
    git("commit", "-m", f"{br}: tested change", check=False)
    git("checkout", "main")
    git("merge", "--no-ff", br, "-m", f"merge {br}")
    log("finish", True, f"merged {br}")
    return cmd_deploy(changed)


def cmd_deploy(paths) -> int:
    rows = {r["scratchpad_path"]: r for r in manifest()}
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    bad = 0
    for p in paths:
        r = rows.get(p.replace("\\", "/"))
        if not r:
            log("deploy", False, f"{p}: not in MANIFEST.csv")
            bad += 1
            continue
        run, mine = Path(r["runs_from"]), HOME / r["scratchpad_path"]
        if run.exists():
            keep = BACKUP / "replaced" / stamp / r["scratchpad_path"]
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(run, keep)
        shutil.copy2(mine, run)
        log("deploy", True, f"{p} -> {run}")
    return 1 if bad else 0


def cmd_backup() -> int:
    stamp = datetime.datetime.now().strftime("%Y-%m-%d")
    dst = BACKUP / "snapshots" / stamp
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(HOME, dst, ignore=shutil.ignore_patterns(".git"))
    log("backup-d", True, str(dst))
    r = subprocess.run(["rclone", "copy", str(BACKUP), GDRIVE, "--exclude", "replaced/**"],
                       capture_output=True, text=True)
    log("backup-gdrive", r.returncode == 0, (r.stderr.strip().splitlines() or [""])[-1][:200])
    return r.returncode


def cmd_nuc() -> int:
    script = r'''
cd ~ || exit 1
echo '## Started by cron'
crontab -l 2>/dev/null | grep -vE '^\s*(#|$)' | sed 's/^/    /'
for d in learning-src nuc-homelab/bin bin money-autopilot secrets-page/tools; do
  [ -d "$d" ] || continue
  echo; echo "## ~/$d"
  find "$d" -maxdepth 1 -type f \( -name '*.py' -o -name '*.sh' -o -name '*.mjs' -o -name '*.js' \) ! -name '*.bak*' | sort | while read -r f; do
    first=$(grep -m1 -E '^("""|#|//) ?[A-Za-z]' "$f" | sed -E 's/^("""|#|\/\/) ?//' | cut -c1-110)
    echo "- \`$f\`: $first"
  done
done
echo; echo '## Docker services (compose in ~/nuc-homelab)'
docker ps --format '- {{.Names}} ({{.Image}})' | sort
'''
    r = subprocess.run(["ssh", NUC, script], capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = HOME / "nuc" / "NUC-SCRIPTS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    body = r.stdout
    body = "\n".join(l for l in body.splitlines() if not GARDEN.search(l))  # Garden/Robin: not listed
    out.write_text("# Scripts the NUC's version of the site runs (read-only list, "
                   + datetime.date.today().isoformat() + ")\n\nThese stay on the NUC; this list is how to find them.\n"
                   "The first line of each is its own description. Garden for All / Robin material is left out.\n\n"
                   + body + "\n", encoding="utf-8")
    log("nuc", r.returncode == 0, f"{body.count(chr(10))} lines")
    return r.returncode


def main() -> int:
    a = sys.argv[1:] or ["check"]
    cmds = {"import": cmd_import, "check": cmd_check, "backup": cmd_backup, "nuc": cmd_nuc, "finish": cmd_finish}
    if a[0] == "start" and len(a) > 1:
        return cmd_start(a[1])
    if a[0] == "deploy":
        return cmd_deploy(a[1:])
    if a[0] in cmds:
        return cmds[a[0]]()
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
