#!/usr/bin/env python3
"""github_fixes_in.py - bring Dependabot fixes from the PUBLIC copy back into the PRIVATE Scratchpad (T0870).

Why: GitHub's free scanners and Dependabot only run on the public repo netnovaops-training/scratchpad-public,
but that repo is REBUILT from D:/scratchpad by public_copy.py. A PR merged on the public repo would be
overwritten by the next copy, so the fix has to land in D:/scratchpad (the real source) instead.

    python tools/github_fixes_in.py            dry run: say what would happen, change nothing
    python tools/github_fixes_in.py --write    apply, commit, close / merge PRs
    python tools/github_fixes_in.py --write --force   ignore the once-per-11-hours stamp

Rules (Jason 11-18, recommended defaults):
- Only Dependabot PRs. Patch, minor and digest bumps go in when the PR's GitHub checks are not failing.
  Major bumps, and 0.x minor bumps (breaking under semver), wait for Jason: listed under "ask".
- PR touching only .github/ (the public repo's own workflows, never copied from private): merged on the
  public repo itself (squash), because there is no private copy of those files.
- Any other PR: its diff is applied to the SAME paths in D:/scratchpad (paths are identical in both repos).
  `git apply --check` first; a file with uncommitted local changes is never touched. One commit per PR
  (the post-commit hook pushes to the NUC/Gitea remote), then the public PR is closed with a comment;
  the next public copy (09:35/21:35) ships the change and Dependabot sees it as done.
- Anything that does not apply cleanly is listed under "ask" with the reason. Nothing is deleted.
Report: audit/github-fixes-in/YYYY-MM-DD_HHMM.json (the autopilot reads the newest one and files Ask lines).
Schedule: Windows task "GitHub fixes in" at 00:00 and 12:00, after github_pull.py (11:30/23:30 ET), before
the autopilot's 12:05 run, so new work starts on fixed code.
OUTCOME: (2026-11-18) first version; see the T0870 Review lines for the first runs.
"""
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = "netnovaops-training/scratchpad-public"
SRC = Path("D:/scratchpad")
OUT = SRC / "audit" / "github-fixes-in"
STAMP = SRC / ".github-fixes-in.stamp"     # last --write run; listed in .git/info/exclude, never committed
MIN_GAP_H = 11
TRAILER = "\n\nCo-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
# Full path, not bare "gh": the 2026-11-19 00:00 scheduled run applied PR #6 and #12, then
# `gh pr close 12` died with "FileNotFoundError: [WinError 2]" although the same bare "gh" had just
# worked for pr list/checks/diff/close #6 in that run (gh.exe untouched since 11-17 23:55, no installer
# ran at 00:00). Cause not found; the fix is a resolved path plus one retry in run(), so a passing
# hiccup cannot leave a committed fix with its public PR still open (PR #12 was left that way).
GH = shutil.which("gh") or r"C:\Program Files\GitHub CLI\gh.exe"


def run(cmd, cwd=None, inp=None, check=True):
    # Bytes, not text mode: on Windows text mode turns the patch's \n into \r\n and `git apply` then
    # rejects every hunk (found 11-18 on PR #5: same patch applied fine from bash, failed from Python).
    data = inp.encode("utf-8") if inp is not None else None
    try:
        r = subprocess.run(cmd, cwd=cwd, input=data, capture_output=True, check=False)
    except FileNotFoundError:
        time.sleep(5)        # one retry, see GH above; a second failure raises and main() saves the report
        r = subprocess.run(cmd, cwd=cwd, input=data, capture_output=True, check=False)
    r.stdout, r.stderr = r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace")
    if check and r.returncode != 0:
        raise RuntimeError("{} -> {}".format(" ".join(cmd[:4]), (r.stderr or r.stdout).strip()[:300]))
    return r


def gh(*args, **kw):
    return run([GH, *args], **kw)


def level(title):
    """'patch' | 'minor' | 'major' | 'digest' | 'group' | 'unknown' from a Dependabot PR title."""
    if title.startswith("Bump the ") and "group" in title:
        # groups are defined in .github/dependabot.yml; only the *-minor-and-patch groups count as safe
        return "group" if "minor-and-patch" in title else "unknown"
    m = re.search(r"from `?([^\s`]+)`? to `?([^\s`]+)`?", title)
    if not m:
        return "unknown"
    a, b = m.group(1), m.group(2)
    va, vb = re.findall(r"\d+", a), re.findall(r"\d+", b)
    hexsha = re.compile(r"^[0-9a-f]{7,64}$")
    if (hexsha.match(a) and re.search(r"[a-f]", a)) or not re.match(r"v?\d", a) or not va or not vb:
        return "digest"      # e.g. python `533b0b2` -> `65a65fc`: same tag, new image digest
    va, vb = [int(x) for x in va] + [0, 0], [int(x) for x in vb] + [0, 0]
    if va[0] != vb[0]:
        return "major"
    if va[0] == 0 and va[1] != vb[1]:
        return "major"       # 0.x: a minor bump may break (semver rule for 0.y.z)
    return "minor" if va[1] != vb[1] else "patch"


def checks_failing(num):
    r = gh("pr", "checks", str(num), "-R", REPO, "--json", "name,state", check=False)
    try:
        states = [c["state"] for c in json.loads(r.stdout or "[]")]
    except json.JSONDecodeError:
        return False
    return any(s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT") for s in states)


def dirty(paths):
    r = run(["git", "status", "--porcelain", "--", *paths], cwd=SRC)
    return [ln[3:] for ln in r.stdout.splitlines() if ln.strip()]


def main(write, force):
    if STAMP.exists() and not force and time.time() - STAMP.stat().st_mtime < MIN_GAP_H * 3600:
        print(f"ran less than {MIN_GAP_H}h ago (stamp {STAMP}); use --force")
        return
    prs = json.loads(gh("pr", "list", "-R", REPO, "--state", "open", "--author", "app/dependabot",
                        "--json", "number,title,url,files", "--limit", "100").stdout)
    report = {"time": time.strftime("%Y-%m-%d %H:%M"), "write": write, "applied": [], "merged_public": [],
              "ask": [], "skipped": []}
    # Any crash still saves the report so far (with "error") and skips the stamp, so the next run
    # retries; before 2026-11-19 a crash left no report and the autopilot read a day-old one.
    try:
        for pr in sorted(prs, key=lambda p: p["number"]):
            n, title = pr["number"], pr["title"]
            files = [f["path"] for f in pr.get("files") or []]
            lv = level(title)
            tag = f"#{n} {title}"
            if lv in ("major", "unknown"):
                report["ask"].append({"pr": n, "title": title, "why": f"{lv} update: waits for Jason", "url": pr["url"]})
                continue
            if checks_failing(n):
                report["ask"].append({"pr": n, "title": title, "why": "GitHub checks failing on the PR", "url": pr["url"]})
                continue
            if files and all(f.startswith(".github/") for f in files):
                if write:
                    gh("pr", "merge", str(n), "-R", REPO, "--squash", "--delete-branch")
                report["merged_public"].append(tag)
                continue
            busy = dirty(files)
            if busy:
                report["skipped"].append({"pr": n, "why": "uncommitted local changes in " + ", ".join(busy)})
                continue
            diff = gh("pr", "diff", str(n), "-R", REPO).stdout
            chk = run(["git", "apply", "--check", "-"], cwd=SRC, inp=diff, check=False)
            if chk.returncode != 0 and run(["git", "apply", "--reverse", "--check", "-"], cwd=SRC, inp=diff,
                                           check=False).returncode == 0:
                # Already in the private source (a past run committed it but crashed before closing the PR,
                # as PR #12 on 2026-11-19 00:00). Close it the same way instead of filing it as "does not apply".
                if write:
                    gh("pr", "close", str(n), "-R", REPO, "--comment",
                       "Already applied in the private source; it ships with the next public copy.")
                report["applied"].append(tag + " (already in source%s)" % ("" if write else ", dry run"))
                continue
            if chk.returncode != 0:
                report["ask"].append({"pr": n, "title": title, "url": pr["url"],
                                      "why": "diff does not apply to D:/scratchpad: " + chk.stderr.strip()[:200]})
                continue
            if write:
                run(["git", "apply", "-"], cwd=SRC, inp=diff)
                run(["git", "add", "--", *files], cwd=SRC)
                msg = f"Dependabot via public PR #{n}: {title}\n\nApplied by tools/github_fixes_in.py (T0870)."
                run(["git", "commit", "-q", "-m", msg + TRAILER, "--", *files], cwd=SRC)
                sha = run(["git", "rev-parse", "--short", "HEAD"], cwd=SRC).stdout.strip()
                gh("pr", "close", str(n), "-R", REPO, "--comment",
                   f"Applied in the private source (commit {sha}); it ships with the next public copy.")
                report["applied"].append(tag + " -> " + sha)
            else:
                report["applied"].append(tag + " (dry run)")
    except Exception as exc:  # noqa: BLE001 - report it, then fail the task (exit 1) below
        report["error"] = f"{type(exc).__name__}: {exc}"
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / (time.strftime("%Y-%m-%d_%H%M") + ("" if write else "_dry") + ".json")
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    if write and "error" not in report:
        STAMP.write_text(report["time"], encoding="utf-8")
    print("applied {}, merged on public {}, ask {}, skipped {} -> {}".format(
        len(report["applied"]), len(report["merged_public"]), len(report["ask"]), len(report["skipped"]), path))
    for a in report["ask"]:
        print("  ask #{}: {}".format(a["pr"], a["why"]))
    if "error" in report:
        print("ERROR: " + report["error"])
        sys.exit(1)


if __name__ == "__main__":
    main("--write" in sys.argv, "--force" in sys.argv)
