"""public_backport.py - bring changes merged on the PUBLIC GitHub repo back into the private Scratchpad.

Why: the nightly cloud routine merges Dependabot and fix PRs on github.com/netnovaops-training/scratchpad-public.
The public copy is rebuilt from D:/scratchpad by public_copy.py, so without this step the next refresh would
undo those merges (and the push would be refused, since main cannot be force-pushed).

What it does (run by daily_public_push.ps1 before the refresh; safe to run by hand):
  1. git fetch github in D:/scratchpad-public. Nothing new on GitHub -> exit 0.
  2. For every file GitHub changed outside .github/ (the workflows live only in the public repo), apply the
     same diff to the matching Scratchpad file. Cleaned paths are mapped back to real ones with
     public_copy.clean(). A diff whose context holds a placeholder will not apply: that file is listed in
     the log and the run stops (exit 2), so nothing gets overwritten. Fix it by hand, then rerun.
  3. Commit the back-ported files in the Scratchpad, then merge github/main into the public copy.
Exit codes: 0 = done or nothing to do, 2 = needs a hand port, 1 = git error.
Reminder: pc/ai-stack in the Scratchpad is itself a copy of the live code in www/ai-stack; the commit message
says so, and the live copy still needs the same change.
OUTCOME 2026-11-17: written with the nightly routine (T1010). First real use waits for the first merge.
"""
import importlib.util
import subprocess
import sys
from collections import Counter
from pathlib import Path

PUB = Path("D:/scratchpad-public")
SRC = Path("D:/scratchpad")


def git(repo, *args, check=True, inp=None):
    """Run git in repo and return stdout (text). Raises on failure when check is set."""
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, input=inp)
    if check and r.returncode != 0:
        print(f"git {' '.join(args[:3])}: {r.stderr.strip()[:300]}")
        sys.exit(1)
    return r.stdout


def path_map():
    """Public path -> Scratchpad path, using public_copy's own cleaning rules."""
    spec = importlib.util.spec_from_file_location("pc", SRC / "tools/public_copy.py")
    pc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pc)
    m = {}
    for rel in git(SRC, "ls-files").split("\n"):
        if rel:
            m[pc.clean(rel, Counter(), "(path)")] = rel
    return m


def main():
    git(PUB, "fetch", "-q", "github")
    local, remote = git(PUB, "rev-parse", "main").strip(), git(PUB, "rev-parse", "github/main").strip()
    already = subprocess.run(["git", "-C", str(PUB), "merge-base", "--is-ancestor", remote, local]).returncode == 0
    if local == remote or already:
        print("backport: nothing new on GitHub")
        return 0
    base = git(PUB, "merge-base", local, remote).strip()
    changed = [f for f in git(PUB, "diff", "--name-only", base, remote).split("\n") if f]
    code = [f for f in changed if not f.startswith(".github/")]
    pmap = path_map() if code else {}
    done, stuck = [], []
    for pub in code:
        real = pmap.get(pub, pub)
        patch = git(PUB, "diff", base, remote, "--", pub)
        if pub != real:
            patch = patch.replace(f"a/{pub}", f"a/{real}").replace(f"b/{pub}", f"b/{real}")
        if subprocess.run(["git", "-C", str(SRC), "apply", "--check", "-"], input=patch, text=True,
                          capture_output=True).returncode != 0:
            stuck.append(real)
            continue
        git(SRC, "apply", "-", inp=patch)
        done.append(real)
    if stuck:
        # Undo the partial work so the Scratchpad is never left half-patched.
        if done:
            git(SRC, "checkout", "--", *done)
        print("backport: STOPPED, needs a hand port:", ", ".join(stuck))
        return 2
    if done:
        git(SRC, "add", "--", *done)
        msg = ("Back-port from public GitHub (scratchpad-public " + remote[:7] + ")\n\n"
               + "\n".join(f"- {d}" for d in done)
               + "\n\nMerged on GitHub by the nightly routine. Live code in www/ai-stack still needs the same change.\n")
        git(SRC, "commit", "-q", "-F", "-", inp=msg)
    git(PUB, "merge", "-q", "--no-edit", "github/main")
    print(f"backport: {len(done)} files back-ported, {len(changed) - len(code)} workflow files merged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
