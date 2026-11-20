"""projects_public_copy.py - copy the private Projects repo's CODE into scratchpad-public/projects/, cleaned.

Why: Jason 2026-11-20 wants Projects public. Flipping the private repo would publish his name, home IPs and
family names (scan 11-20: Owner 139 files, home LAN 87, casey 147, robin 45) plus every old version in its history.
So, like scratchpad-public, a cleaned copy with fresh history is published instead, and Projects stays private.
Jason, later on 11-20: "I wanted it in the same repo as scratchpad, the public one" -> it now lives in
D:/scratchpad-public/projects/ (one public repo); the separate projects-public repo was archived.

How: reuses public_copy.py's rules (RULES, clean(), BANNED check, change record) by importing it and pointing
its SRC/DST/CHANGES/EXCLUDE settings at Projects. Source is a clean clone of main, D:/projects-src, fast-forwarded
from GitHub first (the everyday clone C:/Users/USER/Projects/Projects-repo sits on feature branches).

    py -3.14 -I D:/scratchpad/tools/projects_public_copy.py            dry run: counts only, writes nothing
    py -3.14 -I D:/scratchpad/tools/projects_public_copy.py --write    copy, then STOP if a banned word is left

Left out on purpose:
- jasons-site/ai-stack/: the same apps are already public, cleaned, in scratchpad-public (pc/ai-stack).
- jasons-site/Casey/ (whole app: built around a family member's real record) and the Mom/Ability knowledge/
  folders (profiles of real people).
- Admin/kb-media (screenshots), Mom/_archive, peewee narration files, handover/CLAUDE/HANDOFF notes.
- Booking (retired 11-19).
The change record (every real value and its placeholder) goes to D:/projects-public-changes.private.txt and a
copy in D:/private-records; it must never be pushed.
OUTCOME 2026-11-20: written; see the T0930 Review lines for the first run.
"""
import importlib.util
import subprocess
import sys
from pathlib import Path

SRC = Path("D:/projects-src")
PUB = Path("D:/scratchpad-public")
DST = PUB / "projects"
MARK = "projects_public_copy"   # every commit that writes projects/ says this, so the guard can find them

spec = importlib.util.spec_from_file_location("public_copy", "D:/scratchpad/tools/public_copy.py")
pc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc)

pc.SRC = SRC
pc.DST = DST
pc.CHANGES = Path("D:/projects-public-changes.private.txt")
pc.INCLUDE_PREFIX = ()   # public_copy keeps nuc/intake/ for the Scratchpad; nothing like that here
pc.PORT_FILES = ("jasons-site/pc-firewall.ps1", "jasons-site/pc-firewall-undo.ps1")
pc.EXCLUDE_PREFIX = (
    "jasons-site/ai-stack/",
    # 11-20: the whole Casey app is out, not just knowledge/: patches/, tools/ and app/milestones.py hold her
    # real record (school, scholarships, commissioning, awards). Family data never goes public.
    "jasons-site/Casey/",
    # .github is copied once, then the public repo keeps its own (as scratchpad-public does): 11-20 the
    # private dependabot.yml listed /jasons-site/Casey, which the public copy does not have.
    ".github/",
    "jasons-site/Mom/knowledge/",
    "jasons-site/Ability/knowledge/",
    "jasons-site/Mom/_archive/",
    "jasons-site/Admin/kb-media/",
    "jasons-site/Booking/",
    "jasons-site/peewee/narration",
)
pc.EXCLUDE_FILE = {
    "jasons-site/CLAUDE.md",
    "jasons-site/HANDOFF.md",
    "jasons-site/messagejay-handover.md",
    "jasons-site/Shared/WEDNESDAY_SETUP.md",
}


def refresh_source():
    """Clone Projects main into SRC once, then fast-forward it; never touches the everyday clone."""
    if not (SRC / ".git").exists():
        subprocess.run(["git", "clone", "-q", "--branch", "main",
                        "https://github.com/netnovaops-training/Projects.git", str(SRC)], check=True)
    else:
        subprocess.run(["git", "-C", str(SRC), "pull", "-q", "--ff-only", "origin", "main"], check=True)
    # OUTCOME 2026-11-20: Projects holds a file whose name has non-ASCII bytes; git ls-files printed it quoted
    # ("jasons-site/...") so the exclude prefixes missed it and the open() failed. Raw names fix both.
    subprocess.run(["git", "-C", str(SRC), "config", "core.quotePath", "false"], check=True)


def carry_gitleaks_ignore():
    """Put Projects' reviewed .gitleaksignore entries into scratchpad-public's root .gitleaksignore.

    Projects' entries are file:rule:line (reviewed 2026-10-25 as test or example values, not real secrets).
    gitleaks only reads the root file, so the paths get the projects/ prefix and are kept between two marker
    lines that are rewritten on every run.
    OUTCOME 2026-11-20: without it the commit hook stopped on Mom/tools/test_mom.py line 195 (a test login).
    """
    from collections import Counter
    src = SRC / ".gitleaksignore"
    if not src.exists():
        return
    begin, end = "# >>> projects/ (projects_public_copy.py, reviewed test/example values)", "# <<< projects/"
    block = [begin]
    for line in src.read_text(encoding="utf-8").splitlines():
        parts = line.split(":")
        if line.startswith("#") or len(parts) not in (3, 4):
            continue
        path, rule, num = parts[-3:]
        block.append(f"projects/{pc.clean(path, Counter(), '(path)')}:{rule}:{num}")
    block.append(end)
    root = PUB / ".gitleaksignore"
    old = root.read_text(encoding="utf-8").splitlines() if root.exists() else []
    if begin in old and end in old:
        old = old[:old.index(begin)] + old[old.index(end) + 1:]
    root.write_text("\n".join(old + block) + "\n", encoding="utf-8")
    print("gitleaks ignore entries carried:", len(block) - 2)


def guard():
    """Stop if projects/ on GitHub changed after our last copy (a merged cloud fix not yet carried to Projects).

    Fixes merged under projects/ must be ported by hand into private Projects first; public_backport.py skips
    projects/ for that reason. A refresh before that would silently undo them.
    """
    subprocess.run(["git", "-C", str(PUB), "fetch", "-q", "github"], check=False)
    log = subprocess.run(["git", "-C", str(PUB), "log", "--format=%H %s", "github/main", "--", "projects"],
                         capture_output=True, text=True, check=False).stdout.splitlines()
    later = []
    for line in log:                     # newest first; stop at our own last copy
        if MARK in line:
            break
        later.append(line[:60])
    if later:
        sys.exit(f"STOP: {len(later)} commit(s) changed projects/ on GitHub after the last copy: {later[:3]}. "
                 "Port them into private Projects first.")


if __name__ == "__main__":
    refresh_source()
    if "--write" in sys.argv:
        guard()
        DST.mkdir(exist_ok=True)
    pc.main()
    if "--write" in sys.argv:
        carry_gitleaks_ignore()
        print(f"next: commit in {PUB} with '{MARK}' in the message")
