#!/usr/bin/env python3
"""config_export_pull.py: pull the NUC's secret-free config export into the Scratchpad and commit it.

T0004 step 3 (2026-11-18 autopilot). The NUC writes ~/config-export at 01:25 (nuc/bin/config_export.py,
which redacts secret values). This copies it to D:\\scratchpad\\nuc\\config-export and commits only that
folder, so git shows day by day what changed in the crontabs, systemd units and compose files.

    py -3.14 -I D:\\scratchpad\\tools\\config_export_pull.py      # Windows task "Config export pull" 12:10

Gate: every pulled file is scanned again with secret_scan.py's patterns BEFORE it touches the repo; one
hit = nothing copied, FAIL in audit/config-export/run.log. nuc/ is excluded from the public copy
(public_copy.py EXCLUDE_PREFIX), so this folder reaches only Gitea and the private GitHub repo.

OUTCOME 2026-11-18: the first export had 3 files that secret_scan.scan() flagged, all false: compose
values like "postgres://user:${DB_PASSWORD}@db" (a variable, not a password) and *_PASSWORD_FILE:
/run/secrets/... (a path). So this gate skips values that start with "${" or "/"; real values still fail.
"""
from __future__ import annotations

import datetime as dt
import math
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import secret_scan

REPO = Path(r"D:\scratchpad")
DEST = REPO / "nuc" / "config-export"
LOG = REPO / "audit" / "config-export" / "run.log"
NUC = "server@100.64.0.11"


def entropy(s: str) -> float:
    """Shannon entropy in bits per character."""
    c = Counter(s)
    return -sum(n / len(s) * math.log2(n / len(s)) for n in c.values())


def leaks(p: Path) -> list[str]:
    """Kinds of plain-text secrets in one file (never the values). Empty list = clean."""
    try:
        t = p.read_text("utf-8", "strict")
    except (OSError, UnicodeDecodeError):
        return ["unreadable"]
    found = []
    for kind, rx in secret_scan.SHAPES.items():
        for m in rx.finditer(t):
            if kind == "url with password" and "${" in m.group(0):
                continue
            found.append(kind)
    for _name, val in secret_scan.ASSIGN.findall(t):
        if val.startswith(("${", "/")) or secret_scan.PLACEHOLDER.match(val) or val == "<redacted>":
            continue
        if entropy(val) > 3.3:
            found.append("KEY=value")
    return found


def log(msg: str) -> None:
    """Append one stamped line to the run log and print it."""
    LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M} {msg}"
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


PC_DEST = REPO / "pc" / "config-export"
# Jason's own Windows tasks (Microsoft's under \Microsoft\ are skipped) and the NAMES of user env vars.
# Env values are never read: PROTON_PASS_KEY_PROVIDER etc. are harmless, but other values could be keys.
PS_TASKS = r"""
Get-ScheduledTask | Where-Object { $_.TaskPath -notlike '\Microsoft\*' } | Sort-Object TaskPath, TaskName | ForEach-Object {
  $acts = ($_.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments)".Trim() }) -join ' && '
  $trig = ($_.Triggers | ForEach-Object { "$($_.CimClass.CimClassName -replace 'MSFT_Task','' -replace 'Trigger','') $($_.StartBoundary)" }) -join '; '
  "$($_.TaskPath)$($_.TaskName)`t$($_.State)`t$trig`t$acts"
}
"""
PS_ENV = "[Environment]::GetEnvironmentVariables('User').Keys | Sort-Object"


def export_pc() -> dict[str, bytes]:
    """PC side of step 3: scheduled tasks (name, state, triggers, actions) and user env var names."""
    out = {}
    for name, script, head in (("scheduled-tasks.tsv", PS_TASKS, "task\tstate\ttriggers\tactions"),
                               ("user-env-names.txt", PS_ENV, "# user environment variable NAMES (values never exported)")):
        r = subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, text=True, check=False)
        if r.returncode:
            raise RuntimeError(f"{name}: powershell exit {r.returncode}")
        out[name] = (head + "\n" + r.stdout.strip() + "\n").encode("utf-8")
    return out


def sync(dest: Path, new: dict[str, bytes]) -> list[Path]:
    """Write changed files into dest; return files in dest that are no longer exported."""
    dest.mkdir(parents=True, exist_ok=True)
    gone = [p for p in dest.rglob("*") if p.is_file() and p.relative_to(dest).as_posix() not in new]
    for rel, data in new.items():
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if not out.exists() or out.read_bytes() != data:
            out.write_bytes(data)
    return gone


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="cfgx-pc-") as td:
        pc = export_pc()
        for name, data in pc.items():
            (Path(td) / name).write_bytes(data)
        bad = [(n, k) for n in pc if (k := leaks(Path(td) / n))]
        if bad:
            log("FAIL secret gate (PC export), nothing copied: " + "; ".join(f"{n} {sorted(set(k))}" for n, k in bad))
            return 1
    with tempfile.TemporaryDirectory(prefix="cfgx-") as td:
        r = subprocess.run(["scp", "-q", "-r", f"{NUC}:config-export", td], capture_output=True, text=True, check=False)
        src = Path(td) / "config-export"
        if r.returncode or not src.is_dir():
            log(f"FAIL scp exit {r.returncode}: {r.stderr.strip()[:200]}")
            return 1
        files = sorted(p for p in src.rglob("*") if p.is_file())
        bad = [(p.relative_to(src), k) for p in files if (k := leaks(p))]
        if bad:
            log("FAIL secret gate, nothing copied: " + "; ".join(f"{p} {sorted(set(k))}" for p, k in bad))
            return 1
        new = {p.relative_to(src).as_posix(): p.read_bytes() for p in files}
    gone = sync(DEST, new) + sync(PC_DEST, pc)
    new.update(pc)
    if gone:  # git history keeps them; git rm, not a plain delete
        subprocess.run(["git", "-C", str(REPO), "rm", "-q", "--", *map(str, gone)], check=True)
    rels = [d.relative_to(REPO).as_posix() for d in (DEST, PC_DEST)]
    subprocess.run(["git", "-C", str(REPO), "add", "--", *rels], check=True)
    if subprocess.run(["git", "-C", str(REPO), "diff", "--cached", "--quiet", "--", *rels], check=False).returncode == 0:
        log(f"OK {len(new)} files, no change")
        return 0
    c = subprocess.run(["git", "-C", str(REPO), "commit", "-q", "-m",
                        "config-export: daily NUC/Pi crontab, units, compose + PC task list (redacted; T0004 step 3)",
                        "--", *rels], capture_output=True, text=True, check=False)
    if c.returncode:
        log(f"FAIL commit exit {c.returncode}: {(c.stdout + c.stderr).strip()[:300]}")
        return 1
    log(f"OK {len(new)} files committed, {len(gone)} removed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
