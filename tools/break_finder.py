#!/usr/bin/env python3
"""Break-finder (T1012): list every file or folder this machine's jobs depend on, and say which are missing.

Read-only. It never changes, restarts or deletes anything. Runs the same on the NUC and the Pi (Python 3.9+).

What it reads (the "sources"):
  cron    - `crontab -l` of the user running it. Commented lines (OFF / RETIRED) are skipped. Paths after a
            redirect (`>> x.log`, `2> err`) are skipped: those files are made on first write.
  systemd - ~/.config/systemd/user/*.service|*.timer: ExecStart/ExecStartPre/WorkingDirectory/EnvironmentFile.
  docker  - every container (running or stopped): bind-mount sources, and the compose file + working dir
            from the compose labels. Taken from `docker inspect` JSON, so folder names with spaces stay whole.
  script  - the scripts cron/systemd start (.sh/.py, under $HOME only): absolute $HOME paths written in them.
            Lower confidence: a script may create the path itself, so these are marked "maybe".

Status per path: ok / MISSING / no-access (exists check refused, e.g. /var/lib/docker/volumes as a normal user).
The git column says which repo tracks the path, or "none" (input for T1013: untracked = lost in the next wipe).

Output: one CSV (default ~/nuc-homelab/audit/break-finder/<host>-<date>.csv, or --out) and a short summary of
MISSING rows on stdout. Exit code 0 always, so cron can run it daily (T1013 step 5).

Usage:  python3 break_finder.py [--out FILE] [--no-scripts]
OUTCOME: (2026-11-17) written for T1012 slice 1; results go in the T1012 Review line.
"""
import csv
import glob
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
# Paths inside scripts: absolute paths under $HOME, or ~/..., or $HOME/... (stop at quotes, spaces, ; ) etc.)
SCRIPT_PATH = re.compile(r"""(?:(?<![\w/])~|\$HOME|\$\{HOME\}|%s)(/[\w.@+-]+(?:/[\w.@+-]+)*)""" % re.escape(HOME))
REDIRECT = re.compile(r"^\d*>>?&?$")  # >, >>, 2>, 2>>, &> forms as their own token
SKIP_SUFFIX = (".log", ".err", ".out", ".lock", ".pid", ".tmp")  # made on first write


def run(cmd):
    """Run a read-only command; return stdout ('' if the tool is missing or fails)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def expand(p):
    """Turn ~, $HOME and ${HOME} into the real home folder; strip trailing slashes and quotes."""
    p = p.strip("'\"")
    p = re.sub(r"^(~|\$HOME|\$\{HOME\})(?=/|$)", HOME, p)
    return p.rstrip("/") or "/"


def status(p):
    """ok / MISSING / no-access for one path. A parent we cannot read means we cannot tell: no-access."""
    try:
        os.stat(p)
        return "ok"
    except PermissionError:
        return "no-access"
    except FileNotFoundError:
        parent = os.path.dirname(p)
        while parent and parent != "/":
            if os.path.exists(parent):
                return "MISSING" if os.access(parent, os.R_OK | os.X_OK) else "no-access"
            parent = os.path.dirname(parent)
        return "MISSING"
    except OSError:
        return "no-access"


_git_cache = {}


def git_repo(p):
    """Top folder of the git repo that tracks path p, 'untracked' if in a repo but not added, or 'none'."""
    d = p if os.path.isdir(p) else os.path.dirname(p)
    while d and not os.path.isdir(d):
        d = os.path.dirname(d)
    if d not in _git_cache:
        _git_cache[d] = run(["git", "-C", d, "rev-parse", "--show-toplevel"]).strip() or "none"
    top = _git_cache[d]
    if top == "none" or not os.path.exists(p):
        return top
    if os.path.isfile(p) and run(["git", "-C", top, "ls-files", "--error-unmatch", p]).strip() == "":
        return "untracked in " + top
    return top


def paths_in_command(cmd):
    """Absolute/home paths named in one shell command line, minus redirect targets and env assignments."""
    try:
        toks = shlex.split(cmd, comments=False, posix=True)
    except ValueError:
        toks = cmd.split()
    out, skip_next = [], False
    for t in toks:
        if skip_next:
            skip_next = False
            continue
        if REDIRECT.match(t):
            skip_next = True
            continue
        if re.match(r"^\d*>>?", t):  # glued form: >>/x.log or 2>/dev/null
            continue
        if "=" in t:
            k, _, v = t.partition("=")
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", k):  # VAR=value: env, often PATH lists; skip
                continue
            if k.startswith("-"):
                t = v  # --config=/x
        for part in re.split(r"[;&|()]", t):
            if re.match(r"^(/|~/|\$HOME/|\$\{HOME\}/)", part) and "*" not in part and "$(" not in part:
                p = expand(part)
                if p.startswith("/dev/") or p.startswith("/proc/") or p.endswith(SKIP_SUFFIX):
                    continue
                if p in ("/usr/bin/env", "/bin/bash", "/bin/sh", "/usr/bin/python3", "/usr/bin/flock"):
                    continue
                out.append(p)
    return out


def from_cron():
    """(source, from, path) rows from the active crontab lines."""
    rows = []
    for n, line in enumerate(run(["crontab", "-l"]).splitlines(), 1):
        s = line.strip()
        if not s or s.startswith("#") or re.match(r"^[A-Za-z_]+=", s):
            continue
        cmd = s.split(None, 1)[1] if s.startswith("@") else " ".join(s.split()[5:])
        for p in paths_in_command(cmd):
            rows.append(("cron", "crontab line %d" % n, p))
    return rows


def from_systemd():
    """(source, from, path) rows from the user's systemd unit files."""
    rows = []
    for f in sorted(glob.glob(os.path.join(HOME, ".config/systemd/user/*.service")) +
                    glob.glob(os.path.join(HOME, ".config/systemd/user/*.timer"))):
        rows.append(("systemd", "unit file", f))
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                k, _, v = line.partition("=")
                if k.strip() in ("ExecStart", "ExecStartPre", "ExecStop", "WorkingDirectory", "EnvironmentFile"):
                    for p in paths_in_command(v.lstrip("-@+!")):
                        rows.append(("systemd", os.path.basename(f) + " " + k.strip(), p))
    return rows


def from_docker():
    """(source, from, path) rows from container bind mounts and compose labels (spaces in names kept)."""
    ids = run(["docker", "ps", "-aq"]).split()
    if not ids:
        return []
    try:
        data = json.loads(run(["docker", "inspect"] + ids) or "[]")
    except ValueError:
        return []
    rows = []
    for c in data:
        name = c.get("Name", "?").lstrip("/")
        for m in c.get("Mounts", []):
            if m.get("Type") == "bind":
                rows.append(("docker", name + " mount -> " + m.get("Destination", "?"), m["Source"].rstrip("/") or "/"))
        labels = (c.get("Config") or {}).get("Labels") or {}
        for f in filter(None, labels.get("com.docker.compose.project.config_files", "").split(",")):
            rows.append(("docker", name + " compose file", f))
        wd = labels.get("com.docker.compose.project.working_dir")
        if wd:
            rows.append(("docker", name + " compose dir", wd))
    return rows


def from_scripts(rows):
    """'maybe' rows: $HOME paths written inside the .sh/.py scripts that cron/systemd start."""
    out, seen = [], set()
    for src, _, p in rows:
        if src in ("cron", "systemd") and p.startswith(HOME) and p.endswith((".sh", ".py")) and os.path.isfile(p):
            if p in seen:
                continue
            seen.add(p)
            try:
                text = open(p, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            # A path glued to a variable or glob (learning_${x}, learning_*) is only half a name: skip it, it would be a fake miss.
            found = {m.group(1) for m in SCRIPT_PATH.finditer(text) if text[m.end():m.end() + 1] not in ("$", "{", "*")}
            for m in found:
                q = HOME + m.rstrip("._-")
                if not q.endswith(SKIP_SUFFIX):
                    out.append(("script(maybe)", os.path.basename(p), q))
    return out


def main(argv):
    """Collect rows from every source, check each path once per (source, from), write the CSV, print MISSING."""
    host = socket.gethostname()
    base = "nuc-homelab/audit/break-finder" if os.path.isdir(os.path.join(HOME, "nuc-homelab")) else "audit/break-finder"
    out = os.path.join(HOME, base, "%s-%s.csv" % (host, time.strftime("%Y-%m-%d")))
    if "--out" in argv:
        out = argv[argv.index("--out") + 1]
    rows = from_cron() + from_systemd() + from_docker()
    if "--no-scripts" not in argv:
        rows += from_scripts(rows)
    rows = sorted(set(rows))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    missing = []
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["source", "named_in", "path", "status", "git"])
        for src, frm, p in rows:
            st = status(p)
            w.writerow([src, frm, p, st, git_repo(p) if p.startswith(HOME) else "-"])
            if st == "MISSING":
                missing.append((src, frm, p))
    print("%s: %d paths checked, %d MISSING -> %s" % (host, len(rows), len(missing), out))
    for src, frm, p in missing:
        print("  MISSING  %-14s %-40s %s" % (src, frm[:40], p))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
