#!/usr/bin/env python3
"""docs_status.py: where is the documentation backfill? (2026-10-31)

    python scripts/docs_status.py            # writes docs/STATUS.md and prints one line

Counts, for every file doc_backfill.py would document: documented and current (the doc's `source`
hash equals the file's hash now), stale (the file changed since its doc was written), missing, and
too long for one pass. Also counts docs a person has reviewed (`reviewed: yes`) and docs the script
flagged (`needs-review: yes`). Reads the backfill log for the last lines. Changes nothing but
docs/STATUS.md. A new session, or a morning check, reads that file to see exactly where the work
stopped, instead of working it out again.
"""
import datetime
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path

import doc_backfill as B


def front(docpath):
    try:
        with open(docpath, encoding="utf-8") as fh:
            head = fh.read(700)
    except OSError:
        return None
    get = lambda k: (re.search(rf"^{k}:\s*(.*)$", head, re.MULTILINE) or [None, ""])[1].strip()
    return {"source": get("source"), "reviewed": get("reviewed"), "needs": get("needs-review")}


def main():
    files = [l for l in B.git("ls-files").splitlines() if B.eligible(l)]
    # one git call for every hash, instead of one process per file
    hashes = subprocess.run(["git", "-C", B.REPO, "hash-object", "--stdin-paths"], input="\n".join(files),
                            capture_output=True, text=True, encoding="utf-8", check=False).stdout.split()
    cur = stale = missing = too_long = reviewed = flagged = 0
    flagged_files, stale_files = [], []
    for rel, h in zip(files, hashes):
        fm = front(os.path.join(B.OUT, rel.replace("/", os.sep) + ".md"))
        if fm is None:
            missing += 1
            try:
                if len(Path(os.path.join(B.REPO, rel)).read_text(encoding="utf-8", errors="replace")) > B.MAX_CHARS:
                    too_long += 1
            except OSError:
                pass
            continue
        if fm["source"] == h[:12]:
            cur += 1
        else:
            stale += 1
            stale_files.append(rel)
        if fm["reviewed"].lower().startswith("yes"):
            reviewed += 1
        if fm["needs"].lower().startswith("yes"):
            flagged += 1
            flagged_files.append(rel)
    try:
        tail = Path(B.LOG).read_text(encoding="utf-8").splitlines()[-5:]
    except OSError:
        tail = ["(no backfill log yet)"]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Documentation status",
        "",
        f"Generated {now} by `scripts/docs_status.py`. Do not edit by hand.",
        "",
        f"- Files the backfill covers: **{len(files)}**",
        f"- Documented and current: **{cur}**",
        f"- Documented, but the file changed since (stale): **{stale}**",
        f"- Not documented yet: **{missing}** (of these, too long for one pass: {too_long})",
        f"- Reviewed by a person (`reviewed: yes`): **{reviewed}**",
        f"- Flagged for names not in the file (`needs-review: yes`): **{flagged}**",
        "",
        "## To resume",
        "`python scripts/doc_backfill.py --all` skips every current doc, so it is safe to stop and start again.",
        f"Files over {B.MAX_CHARS} characters are skipped and need a chunked pass (not built yet).",
        "",
        f"## Last lines of the backfill log (`{B.LOG}`)",
        "```",
    ] + tail + ["```", ""]
    if flagged_files:
        lines += ["## Flagged, read these first"] + ["- " + f for f in flagged_files[:40]] + [""]
    if stale_files:
        lines += ["## Stale, will be redone on the next run"] + ["- " + f for f in stale_files[:40]] + [""]
    os.makedirs(os.path.join(B.REPO, "docs"), exist_ok=True)
    with open(os.path.join(B.REPO, "docs", "STATUS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"docs: {cur} of {len(files)} current, {stale} stale, {missing} missing ({too_long} too long), {flagged} flagged, {reviewed} reviewed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
