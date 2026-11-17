"""tidy_ai_stack.py: move backup copies, archives and zips out of the ai-stack repo to D:.

Written 2026-11-12 (Jason: "save archived and .bak backed up versions to the D:/ folder so we can
clean the folder ... and update the code base"). Destination D:\\ai-stack-archive\\:
    env-backups\\      .env.bak-* / .env.backup-* (old values, PLAINTEXT secrets: keep private)
    backups\\<path>    every other *.bak* / *.backup* copy, same relative path as in the repo
    archive\\          the repo's archive/ folder (one-shot scripts that already did their job)
    docs-archive\\     docs/archive/
    zips\\<path>       the zip files (contents compared first; see zips-extracted\\)
Left alone: garden-for-robin.zip and everything Garden/Robin (Jason: leave untouched), .git,
.claude worktrees, node_modules, rust/target, All-Documents, documents/.

    python -I tidy_ai_stack.py plan | apply | undo     (log: D:\\ai-stack-archive\\moves.csv)
"""
import csv
import os
import re
import shutil
import sys

REPO = r"C:\Jasons New Site\www\ai-stack"
DEST = r"D:\ai-stack-archive"
LOG = os.path.join(DEST, "moves.csv")
PRUNE = {".git", ".claude", "node_modules", "garden-for-all", "garden-for-robin", "All-Documents",
         "documents", "target", "__pycache__"}
BAK = re.compile(r"(?i)(\.bak($|[-._])|\.bac$|\.backup($|[-._])|\.orig$|\.old$|~$)")
GARDEN = re.compile(r"(?i)garden|\bjuns?\b|for-robin")


def plan_rows():
    rows = []
    for d, sub in (("archive", "archive"), (os.path.join("docs", "archive"), "docs-archive")):
        if os.path.isdir(os.path.join(REPO, d)):
            rows.append(("dir", os.path.join(REPO, d), os.path.join(DEST, sub)))
    for dp, dns, fns in os.walk(REPO):
        rel_dp = os.path.relpath(dp, REPO)
        dns[:] = [x for x in dns if x not in PRUNE and not (rel_dp == "." and x == "archive")
                  and not (rel_dp == "docs" and x == "archive")]
        for f in fns:
            src = os.path.join(dp, f)
            rel = os.path.relpath(src, REPO)
            if GARDEN.search(rel):
                continue
            if f.lower().endswith(".zip"):
                rows.append(("file", src, os.path.join(DEST, "zips", rel)))
            elif BAK.search(f):
                if f.startswith(".env.") and rel_dp == ".":
                    rows.append(("file", src, os.path.join(DEST, "env-backups", f)))
                else:
                    rows.append(("file", src, os.path.join(DEST, "backups", rel)))
    return rows


def main():
    mode = sys.argv[1]
    if mode == "undo":
        for kind, src, dst in reversed(list(csv.reader(open(LOG, encoding="utf-8")))):
            if os.path.exists(dst) and not os.path.exists(src):
                os.makedirs(os.path.dirname(src), exist_ok=True)
                shutil.move(dst, src)
        os.rename(LOG, LOG + ".undone")
        print("UNDO done")
        return 0
    rows = plan_rows()
    for kind, src, dst in rows:
        print("%-4s %s" % (kind, os.path.relpath(src, REPO)))
    print("PLAN %d items" % len(rows))
    if mode != "apply":
        return 0
    ok = 0
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        for kind, src, dst in rows:
            if os.path.exists(dst):
                print("FAIL exists already:", dst)
                continue
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(src, dst)  # across drives: copy, then the original is removed
            w.writerow([kind, src, dst])
            ok += 1
    print("APPLY moved %d of %d" % (ok, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
