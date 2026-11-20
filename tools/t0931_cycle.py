"""t0931_cycle.py - one-day push/review/pull cycle for Atomic Drop (to-do T0931), 2026-11-20.

Jason, 2026-11-20 morning: push the Atomic Drop code now and again at 11:30 AM (his edits until then), let the
cloud review its T0931 items on GitHub, merge that review, and have a current version waiting at 11 PM: on a
Gitea branch review/t0931, in his folder (only if he left nothing uncommitted), and live (only if every test passes).

    py -3.14 -I -X utf8 D:/scratchpad/tools/t0931_cycle.py push    (now, and the 11:30 AM task)
    py -3.14 -I -X utf8 D:/scratchpad/tools/t0931_cycle.py pull    (the 10:30 PM task)

push: 1) NUC: commit anything uncommitted in ~/nuc-homelab/intake (as "Jason WIP <time>") and push it to Gitea.
      2) Copy NUC intake/ (tracked files) into D:/scratchpad/nuc/intake, commit, push the private Scratchpad.
      3) Public copy: public_backport.py first (keeps merged public fixes), then public_copy.py --write
         (nuc/intake/ is now included, cleaned), commit on branch sync/t0931-<stamp>, push, ONE PR, wait for
         its checks (up to 25 min) and merge it when they are green, so the noon cloud review sees the code.
pull: 1) Merge the cloud review PR (head cloud/t0931 on scratchpad-public) when its checks are green.
      2) public_backport.py: the merged fixes go into D:/scratchpad (exit 2 = a hand port is needed: stop).
      3) NUC: branch review/t0931 from master with D:/scratchpad/nuc/intake copied in, pushed to Gitea.
      4) Sandbox tests (~/sandbox/intake_regress2.sh on a copy of that branch). All pass -> if intake/ in his
         folder is clean, fast-forward master to the branch and rebuild the live Drop (rollback tag
         nuc-homelab-intake:before-t0931-<date>); otherwise the branch is all he gets.
Log: D:/scratchpad/audit/t0931-cycle.log. Never prints secret values.
OUTCOME: see the T0931 Review lines.
"""
import datetime
import json
import subprocess
import sys
import time
from pathlib import Path

NUC = "server@100.64.0.10"
SP = Path("D:/scratchpad")
PUB = Path("D:/scratchpad-public")
REPO = "netnovaops-training/scratchpad-public"
LOG = SP / "audit" / "t0931-cycle.log"
PY = [sys.executable, "-I", "-X", "utf8"]


def say(msg):
    line = f"{datetime.datetime.now():%Y-%m-%d %H:%M} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd, check=True, inp=None, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       input=inp, cwd=cwd, check=False)
    if check and r.returncode != 0:
        say(f"FAILED {' '.join(map(str, cmd[:4]))}: {(r.stderr or r.stdout).strip()[:300]}")
        sys.exit(1)
    return r


class _R:
    def __init__(self, returncode, stdout, stderr):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def ssh(script, check=True, remote=("bash", "-s")):
    """Run a script on the NUC. Bytes on stdin: OUTCOME 2026-11-20, text-mode input on Windows turned
    every \\n into \\r\\n and bash failed with "cd: $'/home/user/nuc-homelab\\r'"."""
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=15", NUC, *remote], input=script.encode("utf-8"),
                       capture_output=True, check=False)
    out = _R(r.returncode, r.stdout.decode("utf-8", "replace"), r.stderr.decode("utf-8", "replace"))
    if check and r.returncode:
        say(f"FAILED on the NUC: {(out.stderr or out.stdout).strip()[:300]}")
        sys.exit(1)
    return out


def copy_nuc_to_scratchpad():
    """Tracked files of NUC ~/nuc-homelab/intake -> D:/scratchpad/nuc/intake (adds/overwrites, deletes nothing)."""
    r = subprocess.run(["ssh", NUC, "cd ~/nuc-homelab && git ls-files -z intake | tar cf - --null -T -"],
                       capture_output=True, check=True)
    tmp = SP / "audit" / "t0931-intake.tar"
    tmp.write_bytes(r.stdout)
    dest = SP / "nuc"
    run(["tar", "xf", str(tmp).replace("\\", "/"), "-C", str(dest).replace("\\", "/")])
    tmp.unlink()


def wait_checks(pr, minutes=25):
    """True when every check on the PR finished green/neutral/skipped; False on a failure or timeout."""
    end = time.time() + minutes * 60
    while time.time() < end:
        r = run(["gh", "pr", "checks", str(pr), "-R", REPO, "--json", "state,name"], check=False)
        try:
            checks = json.loads(r.stdout or "[]")
        except ValueError:
            checks = []
        states = {c["state"] for c in checks}
        if checks and states <= {"SUCCESS", "NEUTRAL", "SKIPPED"}:
            return True
        if states & {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED"}:
            bad = [c["name"] for c in checks if c["state"] not in ("SUCCESS", "NEUTRAL", "SKIPPED", "PENDING",
                                                                   "QUEUED", "IN_PROGRESS")]
            say(f"PR #{pr}: checks failed: {', '.join(bad[:8])}")
            return False
        time.sleep(60)
    say(f"PR #{pr}: checks still running after {minutes} min")
    return False


def push():
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M")
    r = ssh(f"""set -e; cd ~/nuc-homelab
if [ -n "$(git status --porcelain intake)" ]; then git add intake && git commit -q -m "Jason WIP {stamp} (T0931)"; echo committed; fi
git push -q origin master; git log --oneline -1""")
    say(f"NUC: {r.stdout.strip()}")
    copy_nuc_to_scratchpad()
    run(["git", "-C", str(SP), "add", "nuc/intake"])
    if run(["git", "-C", str(SP), "diff", "--cached", "--quiet"], check=False).returncode:
        run(["git", "-C", str(SP), "commit", "-q", "-m", f"nuc/intake: Atomic Drop from the NUC, {stamp} (T0931)"])
    run(["git", "-C", str(SP), "push", "-q", "github", "main"])
    say("private Scratchpad pushed")
    b = run(PY + [str(SP / "tools/public_backport.py")], check=False)
    say(f"backport: {b.stdout.strip()[-200:]}")
    if b.returncode != 0:
        say("STOP: backport needs a hand port; public copy not refreshed")
        sys.exit(2)
    run(["git", "-C", str(PUB), "fetch", "-q", "github"])
    # Fewer, larger PRs: when a sync/t0931-* PR is still open, add to it instead of opening a second one.
    openr = run(["gh", "pr", "list", "-R", REPO, "--state", "open", "--json", "number,headRefName"], check=False)
    open_sync = [p for p in json.loads(openr.stdout or "[]") if p["headRefName"].startswith("sync/t0931-")]
    if open_sync:
        branch = open_sync[0]["headRefName"]
        run(["git", "-C", str(PUB), "checkout", "-q", "-B", branch, f"github/{branch}"])
    else:
        branch = f"sync/t0931-{stamp}"
        run(["git", "-C", str(PUB), "checkout", "-q", "-B", branch, "github/main"])
    c = run(PY + [str(SP / "tools/public_copy.py"), "--write"], check=False, cwd=str(PUB))
    say("public copy: " + " | ".join(c.stdout.strip().splitlines()[-2:]))
    if c.returncode != 0:
        say("STOP: public copy refused (banned word or error); nothing pushed")
        sys.exit(1)
    run(["git", "-C", str(PUB), "add", "-A", "--", ".", ":!audit"])
    if run(["git", "-C", str(PUB), "diff", "--cached", "--quiet"], check=False).returncode == 0:
        say("public copy: no changes")
        return
    run(["git", "-C", str(PUB), "commit", "-q", "-m", f"Sync from private Scratchpad {stamp} (Atomic Drop, T0931)"])
    run(["git", "-C", str(PUB), "push", "-q", "-u", "github", branch])
    if open_sync:
        num = str(open_sync[0]["number"])
        pr = f"https://github.com/{REPO}/pull/{num} (added to)"
    else:
        pr = run(["gh", "pr", "create", "-R", REPO, "--head", branch, "--base", "main",
                  "--title", f"Sync {stamp}: Atomic Drop code (T0931)",
                  "--body", "Cleaned copy from the private Scratchpad, now including nuc/intake (Atomic Drop) "
                            "so the cloud review can see it."]).stdout.strip()
        num = pr.rstrip("/").split("/")[-1]
    say(f"PR {pr}")
    if wait_checks(num):
        run(["gh", "pr", "merge", num, "-R", REPO, "--merge", "--delete-branch"])
        run(["git", "-C", str(PUB), "checkout", "-q", "main"])
        run(["git", "-C", str(PUB), "pull", "-q", "--ff-only", "github", "main"])
        say(f"PR #{num} merged; public main has the code")
    else:
        say(f"PR #{num} left open (see above); the noon review reads the branch instead")


def pull():
    stamp = datetime.date.today().strftime("%Y%m%d")
    r = run(["gh", "pr", "list", "-R", REPO, "--head", "cloud/t0931", "--state", "open", "--json", "number"],
            check=False)
    prs = json.loads(r.stdout or "[]")
    if prs:
        num = str(prs[0]["number"])
        if wait_checks(num, minutes=10):
            run(["gh", "pr", "merge", num, "-R", REPO, "--merge", "--delete-branch"])
            say(f"cloud review PR #{num} merged")
        else:
            say(f"cloud review PR #{num} NOT merged (checks); its changes are not in tonight's version")
    else:
        say("no open cloud/t0931 PR (review made no changes, or was already merged)")
    b = run(PY + [str(SP / "tools/public_backport.py")], check=False)
    say(f"backport: {b.stdout.strip()[-200:]}")
    if b.returncode != 0:
        say("STOP: backport needs a hand port; NUC not changed")
        sys.exit(2)
    copy_up = subprocess.run(["tar", "cf", "-", "-C", str(SP / "nuc").replace("\\", "/"), "intake"],
                             capture_output=True, check=True).stdout
    script = f"""set -e
cd ~/nuc-homelab
git fetch -q origin
dirty=$(git status --porcelain intake | wc -l)
W=$(mktemp -d /home/user/sandbox/t0931-pull-XXXX)
git worktree add -q -B review/t0931 "$W" master
tar xf - -C "$W" --exclude='__pycache__'
cd "$W"; git add intake
git diff --cached --quiet || git commit -q -m "Atomic Drop: tonight's version with the cloud review merged ({stamp}, T0931)"
git push -q -f origin review/t0931
echo "branch $(git rev-parse --short HEAD)"
if bash ~/sandbox/intake_regress2.sh "$W/intake" > ~/sandbox/t0931-pull-tests.txt 2>&1 && ! grep -q "FAIL\\|BUILD FAILED\\|SOME FAILED" ~/sandbox/t0931-pull-tests.txt; then
  echo "tests: ALL PASS"
  cd ~/nuc-homelab
  if [ "$dirty" = 0 ]; then
    git merge -q --ff-only review/t0931 && git push -q origin master && echo "folder: fast-forwarded"
    docker tag nuc-homelab-intake nuc-homelab-intake:before-t0931-{stamp}
    docker compose build -q intake && docker compose up -d intake >/dev/null 2>&1
    sleep 12; echo "live: $(docker inspect intake -f '{{{{.State.Health.Status}}}}') (rollback tag before-t0931-{stamp})"
  else
    echo "folder: has uncommitted intake changes, left alone; use: git checkout review/t0931"
  fi
else
  echo "tests: FAILED, folder and live left alone (see ~/sandbox/t0931-pull-tests.txt)"
fi
cd ~/nuc-homelab && git worktree remove --force "$W"
"""
    # Send the script as a file, then feed the tar on stdin.
    ssh(script, remote=("cat > /home/user/sandbox/t0931_pull_nuc.sh",))
    r = subprocess.run(["ssh", NUC, "bash /home/user/sandbox/t0931_pull_nuc.sh"], input=copy_up,
                       capture_output=True, check=False)
    for line in (r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")).strip().splitlines():
        say("NUC: " + line)


if __name__ == "__main__":
    {"push": push, "pull": pull}[sys.argv[1]]()
