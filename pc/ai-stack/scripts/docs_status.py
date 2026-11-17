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
import doc_backfill as B  # noqa: E402  (same eligibility rules, same paths, one source of truth)


def front(docpath):
    try:
        head = open(docpath, encoding="utf-8").read(700)
    except OSError:
        return None
    get = lambda k: (re.search(r"^%s:\s*(.*)$" % k, head, re.M) or [None, ""])[1].strip()
    return {"source": get("source"), "reviewed": get("reviewed"), "needs": get("needs-review")}


def main():
    files = [l for l in B.git("ls-files").splitlines() if B.eligible(l)]
    # one git call for every hash, instead of one process per file
    hashes = subprocess.run(["git", "-C", B.REPO, "hash-object", "--stdin-paths"], input="\n".join(files),
                            capture_output=True, text=True, encoding="utf-8").stdout.split()
    cur = stale = missing = too_long = reviewed = flagged = 0
    flagged_files, stale_files = [], []
    for rel, h in zip(files, hashes):
        fm = front(os.path.join(B.OUT, rel.replace("/", os.sep) + ".md"))
        if fm is None:
            missing += 1
            try:
                if len(open(os.path.join(B.REPO, rel), encoding="utf-8", errors="replace").read()) > B.MAX_CHARS:
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
        tail = open(B.LOG, encoding="utf-8").read().splitlines()[-5:]
    except OSError:
        tail = ["(no backfill log yet)"]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = [
        "# Documentation status",
        "",
        "Generated %s by `scripts/docs_status.py`. Do not edit by hand." % now,
        "",
        "- Files the backfill covers: **%d**" % len(files),
        "- Documented and current: **%d**" % cur,
        "- Documented, but the file changed since (stale): **%d**" % stale,
        "- Not documented yet: **%d** (of these, too long for one pass: %d)" % (missing, too_long),
        "- Reviewed by a person (`reviewed: yes`): **%d**" % reviewed,
        "- Flagged for names not in the file (`needs-review: yes`): **%d**" % flagged,
        "",
        "## To resume",
        "`python scripts/doc_backfill.py --all` skips every current doc, so it is safe to stop and start again.",
        "Files over %d characters are skipped and need a chunked pass (not built yet)." % B.MAX_CHARS,
        "",
        "## Last lines of the backfill log (`%s`)" % B.LOG,
        "```",
    ] + tail + ["```", ""]
    if flagged_files:
        lines += ["## Flagged, read these first"] + ["- " + f for f in flagged_files[:40]] + [""]
    if stale_files:
        lines += ["## Stale, will be redone on the next run"] + ["- " + f for f in stale_files[:40]] + [""]
    os.makedirs(os.path.join(B.REPO, "docs"), exist_ok=True)
    with open(os.path.join(B.REPO, "docs", "STATUS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("docs: %d of %d current, %d stale, %d missing (%d too long), %d flagged, %d reviewed" %
          (cur, len(files), stale, missing, too_long, flagged, reviewed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
