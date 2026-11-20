#!/usr/bin/env python3
"""py_versions.py: which Python every script and container runs on, and what blocks moving it up.

Jason, 2026-11-13: check the 3.12.10 and 3.14.10 installs, move whatever can move to 3.14 (and to
the free-threaded "no GIL" build where it helps), document what has to stay, and re-check monthly
so the code base climbs versions as its libraries catch up.

    py -3.14 -P py_versions.py              write D:\\scratchpad\\PYTHON-VERSIONS.md, exit 1 if a
                                            script would break on the target version
    py -3.14 -P py_versions.py --nuc        also list the Pythons on the NUC (ssh, read-only)
    py -3.14 -P py_versions.py --repair-pip restore 3.14's pip from its bundled wheel (needs admin)

Run with -P, not -I: -I also hides the per-user site-packages, which is where 3.14's libraries live
(%APPDATA%\\Python\\Python314), so every import would look missing.

How a script is judged (nothing is executed, imports are only looked up):
  1. parse it with the target Python's grammar (catches syntax that changed between versions)
  2. every top-level import is either stdlib, a sibling file, or must be findable on the target
  3. python-pins.json next to the report can pin a script to an older version with a reason;
     pinned scripts are reported, not failed. Re-check the reason each month.
Tried on 2026-11-13: `py -3.14 -m pip` was broken (pip._vendor.rich._emoji_codes missing), so pip
lists fall back to running pip straight from the bundled wheel, which can read but not repair.
"""
from __future__ import annotations

import ast
import datetime
import json
import re
import subprocess
import sys
import zipfile
from pathlib import Path

TARGET = "3.14"
HOME = Path(r"D:\scratchpad")
REPORT = HOME / "PYTHON-VERSIONS.md"
PINS = HOME / "python-pins.json"
AI = Path(r"C:\Jasons New Site\www\ai-stack")
SKIP_DIRS = {"garden-for-all", ".claude", "node_modules", "target", ".venv", "venv", "__pycache__"}
THREADS = re.compile(r"\b(threading|ThreadPoolExecutor|concurrent\.futures)\b")


def run(cmd: list[str], timeout: int = 60) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def interpreters() -> list[dict]:
    """Every Python the py launcher knows, plus whether it is a free-threaded (no GIL) build."""
    out = []
    for line in run(["py", "-0p"]).splitlines():
        m = re.search(r"-V?:?(\d+\.\d+t?)\S*\s+\*?\s*(\S.*\.exe)", line)
        if m:
            exe = m.group(2).strip()
            info = run([exe, "-c", ("import sys,sysconfig;print(sys.version.split()[0],"
                        "bool(sysconfig.get_config_var('Py_GIL_DISABLED')))")]).split()
            if info:
                out.append({"tag": m.group(1), "exe": exe, "version": info[0], "free_threaded": info[1] == "True"})
    return out


def pip_names(exe: str) -> set[str]:
    raw = run([exe, "-m", "pip", "list", "--format=json"])
    if not raw.strip():  # broken pip: run pip from the bundled wheel instead (read-only use)
        wheel = next((Path(exe).parent / "Lib" / "ensurepip" / "_bundled").glob("pip-*.whl"), None)
        raw = run([exe, f"{wheel}/pip", "list", "--format=json"]) if wheel else ""
    try:
        return {p["name"].lower().replace("_", "-") for p in json.loads(raw or "[]")}
    except ValueError:
        return set()


def docker_bases() -> list[tuple[str, str]]:
    rows = []
    for f in AI.rglob("Dockerfile*"):
        if SKIP_DIRS & set(f.relative_to(AI).parts):
            continue
        for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"\s*FROM\s+(\S*python:(\d+\.\d+)\S*)", line, re.IGNORECASE)
            if m:
                rows.append((str(f.relative_to(AI)), m.group(2)))
    for f in AI.glob("*.ps1"):  # one-off `docker run python:X` calls in launchers
        for m in re.finditer(r"python:(\d+\.\d+)-\w+", f.read_text(encoding="utf-8", errors="replace")):
            rows.append((f.name + " (docker run)", m.group(1)))
    return sorted(set(rows))


def check_scripts(target_exe: str) -> list[dict]:
    """Parse every scratchpad .py and look up its imports on the target interpreter."""
    files = [p for p in (HOME / "pc").rglob("*.py") if not SKIP_DIRS & set(p.parts)]
    # Project modules (imported through sys.path tweaks or packages like shared/, app/) are local,
    # wherever they sit in the tree. First run counted them as missing: 30 of 37 were these.
    project = {q.stem for q in files} | {q.name for q in (HOME / "pc").rglob("*") if q.is_dir()}
    found: dict[str, list[str]] = {}
    rows = []
    for p in files:
        rel = p.relative_to(HOME).as_posix()
        src = p.read_text(encoding="utf-8", errors="replace")
        row = {"path": rel, "syntax": "ok", "missing": [], "threads": bool(THREADS.search(src)),
               "docker": in_docker(p)}
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            row["syntax"] = f"line {e.lineno}: {e.msg}"
            rows.append(row)
            continue
        mods = set()
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                mods |= {a.name.split(".")[0] for a in n.names}
            elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                mods.add(n.module.split(".")[0])
        row["third_party"] = sorted(m for m in mods if m not in sys.stdlib_module_names and m not in project)
        if not row["docker"]:  # container code gets its libraries from its image, not this PC
            for m in row["third_party"]:
                found.setdefault(m, [])
        rows.append(row)
    # one lookup subprocess for all modules, on the target, with the user site enabled (-P, not -I)
    probe = "import importlib.util,sys;print('\\n'.join(m for m in sys.argv[1:] if importlib.util.find_spec(m) is None))"
    missing = set(run([target_exe, "-P", "-c", probe, *found], timeout=120).split())
    for r in rows:
        r["missing"] = [] if r["docker"] else [m for m in r.get("third_party", []) if m in missing]
    return rows


def in_docker(p: Path) -> str:
    """The Python of the nearest Dockerfile above this script ('' if it runs on the host)."""
    for d in p.parents:
        if d == HOME / "pc":
            break
        for f in d.glob("Dockerfile*"):
            m = re.search(r"^\s*FROM\s+\S*python:(\d+\.\d+)", f.read_text(encoding="utf-8", errors="replace"), re.MULTILINE | re.IGNORECASE)
            return m.group(1) if m else "image"
    return ""


def nuc_pythons() -> str:
    cmd = "for p in /usr/bin/python3 /usr/bin/python3.* /usr/local/bin/python3*; do [ -x $p ] && echo $p $($p -V 2>&1); done"
    return run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "server@100.64.0.10", cmd], timeout=30)


def repair_pip(exe: str) -> int:
    """Re-extract pip/ from the wheel that shipped with this Python. Needs an admin shell for C:\\Python314."""
    sp = Path(exe).parent / "Lib" / "site-packages"
    wheel = next((Path(exe).parent / "Lib" / "ensurepip" / "_bundled").glob("pip-*.whl"))
    with zipfile.ZipFile(wheel) as z:
        z.extractall(sp, [n for n in z.namelist() if n.startswith("pip/")])
    print("pip restored from", wheel.name, "- check with: py -3.14 -m pip --version")
    return 0


def main() -> int:
    pys = interpreters()
    target = next((p for p in pys if p["tag"] == TARGET), None)
    if not target:
        print(f"FAIL no Python {TARGET} found by the py launcher")
        return 1
    if "--repair-pip" in sys.argv:
        return repair_pip(target["exe"])
    pins = json.loads(PINS.read_text(encoding="utf-8")) if PINS.exists() else {}
    rows = check_scripts(target["exe"])
    tgt_pkgs = pip_names(target["exe"])
    today = datetime.date.today().isoformat()
    L = [f"# Python versions ({today})", "",
         f"Target for PC scripts: **{target['version']}**. Written by `tools/py_versions.py`; re-run monthly.", "",
         "## Interpreters on this PC", "", "| Version | Free-threaded | Packages | Only in this one (vs target) | Path |",
         "|---|---|---|---|---|"]
    for p in pys:
        names = pip_names(p["exe"])
        extra = sorted(names - tgt_pkgs) if p is not target else []
        L.append(f"| {p['version']} | {'yes' if p['free_threaded'] else 'no'} | {len(names)} | "
                 f"{len(extra)}{': ' + ', '.join(extra[:12]) + (' ...' if len(extra) > 12 else '') if extra else ''} | `{p['exe']}` |")
    if not any(p["free_threaded"] for p in pys):
        L += ["", "No free-threaded build is installed (it is a separate `python3.14t.exe`, an installer option)."]
    L += ["", "## Docker base images", "", "| File | Python |", "|---|---|"]
    L += [f"| `{f}` | {v} |" for f, v in docker_bases()]
    bad = [r for r in rows if (r["syntax"] != "ok" or r["missing"]) and r["path"] not in pins]
    L += ["", f"## Scripts that would not run on {target['version']} ({len(bad)} of {len(rows)})", ""]
    if bad:
        L += ["| Script | Problem |", "|---|---|"]
        L += [f"| `{r['path']}` | {'syntax ' + r['syntax'] if r['syntax'] != 'ok' else 'missing: ' + ', '.join(r['missing'])} |" for r in bad]
    else:
        L.append("None.")
    L += ["", "## Pinned to an older version (python-pins.json)", ""]
    L += [f"- `{k}` stays on {v.get('stay_on')}: {v.get('why')}" for k, v in pins.items()] or ["None."]
    dk = {}
    for r in rows:
        if r["docker"]:
            dk.setdefault(r["docker"], []).append(r["path"])
    L += ["", "## Scripts that run inside Docker (judged by their image, not this PC)", ""]
    L += [f"- Python {v}: {len(ps)} scripts" for v, ps in sorted(dk.items())] or ["None."]
    ft = [r["path"] for r in rows if r["threads"] and not r["docker"] and r["syntax"] == "ok" and not r["missing"]]
    L += ["", "## Free-threaded candidates", "",
          ("Scripts that use threads. They only gain from the no-GIL build if the threads do CPU work "
          "(network or disk waits already overlap with the GIL). Test one on `python3.14t` before moving it."), ""]
    L += [f"- `{p}`" for p in ft] or ["None."]
    if "--nuc" in sys.argv:
        L += ["", "## NUC", "", "```", nuc_pythons().strip() or "(no answer over ssh)", "```"]
    REPORT.write_text("\n".join(L) + "\n", encoding="utf-8")
    ok = not bad
    try:
        with open(r"D:\joblog\events.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.datetime.now().isoformat(timespec="seconds"), "job": "py_versions",
                                "ok": ok, "detail": f"{len(bad)} of {len(rows)} scripts blocked"}) + "\n")
    except OSError:
        pass
    print(("PASS" if ok else "FAIL"), f"{len(rows)} scripts, {len(bad)} blocked on {target['version']} -> {REPORT}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
