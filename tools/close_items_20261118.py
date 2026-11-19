#!/usr/bin/env python3
"""Close finished to-do items: tick, add Done/Review lines, move the block to TODO-DONE.md (2026-11-18).

Copy of close_items_20261117.py with today's items. Uses the shared ~/.todo-all.lock like todo_note.py /
todo_merge.py. Backup: TODO-ALL.md.bak-close-<time>.
OUTCOME: (2026-11-18) closes T0001 (backlog page) and notes T0015 (n-grams restored).
"""
import fcntl
import re
import shutil
import time
from pathlib import Path

HOME = Path.home()
TODO = HOME / "nuc-homelab/TODO-ALL.md"
DONE = HOME / "nuc-homelab/TODO-DONE.md"
LOCK = HOME / ".todo-all.lock"
ITEM = re.compile(r"^- \[( |x|X)\] \*\*(T\d{4,})\*\*")

CLOSE = {
    "T0001": [
        "Review: (2026-11-18 03:10) finish stage: skill 12 re-run on the NUC (Trivy 0.58.1 in a container, the plugin script fails on Windows). Before: image from 11-13 = 1 HIGH (zlib CVE-2026-85091, fix available), 44 MEDIUM, 12 LOW, 5 UNKNOWN. The distroless base was pinned by digest, so `build --pull` changed nothing. Fix: Dockerfile line 21 digest bumped to sha256:809d04af... (current cc-debian13) in ~/backlog-page and in the PC source dash-rust; old Dockerfiles in ~/archive/2026-11-18/ and D:/archive/2026-11-18/; old image kept as backlog-page:pre-2026-11-18 for rollback.",
        "Review: (2026-11-18 03:10) before/after: plants in feed 1,036 (memory only, mounts gone) -> 7,019 | feed folder missing -> 17,134 files, pushed every 30 min by Windows task 'Backlog feed push' | HIGH CVEs 1 -> 0 (MEDIUM 44 -> 43) | /api/meta no key 403, with key 200, public /backlog/ 303 to sign-in (same as before). Code review 0 findings. Not fixed: feedback saved before 11-15 is gone (no copy anywhere); backlog_sync.py has not found new plants since 11-13 (its own item if wanted).",
    ],
}
NOTE = {
    "T0015": ["Review: (2026-11-18 01:32) n-grams done: ngrams-en.zip (9.0 GB, languagetool.org) tested, unpacked to ~/languagetool-ngrams (15 GB, en), languagetool restarted with languageModel=/ngrams, healthy; /v2/check answers. Zip moved to trash 03:00. Left: manga, media, intake-unsorted wait on Jason's files in NAS _T1018-drop."],
}


def main():
    today = time.strftime("%Y-%m-%d")
    with open(LOCK, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        lines = TODO.read_text(encoding="utf-8").split("\n")
        shutil.copy2(TODO, str(TODO) + ".bak-close-" + time.strftime("%Y%m%d-%H%M%S"))
        moved = []

        def find(tid):
            for i, l in enumerate(lines):
                m = ITEM.match(l)
                if m and m.group(2) == tid:
                    j = i + 1
                    while j < len(lines) and lines[j].startswith("  ") and lines[j].strip():
                        j += 1
                    return i, j
            raise SystemExit("not found: " + tid)

        for tid, new in NOTE.items():
            i, j = find(tid)
            lines[j:j] = ["  " + t for t in new]
        for tid, new in CLOSE.items():
            i, j = find(tid)
            block = lines[i:j] + ["  " + t for t in new] + ["  Done: " + today]
            block[0] = block[0].replace("- [ ]", "- [x]", 1)
            del lines[i:j]
            moved.append("\n".join(block))
        if not DONE.exists():
            DONE.write_text("# Finished to-do items\n\nMoved here from TODO-ALL.md when ticked (HANDOFF section 2).\n", encoding="utf-8")
        with open(DONE, "a", encoding="utf-8") as d:
            d.write("\n## " + today + "\n" + "\n".join(moved) + "\n")
        tmp = TODO.with_suffix(".md.tmp")
        tmp.write_text("\n".join(lines), encoding="utf-8")
        tmp.replace(TODO)
    print("closed", list(CLOSE), "noted", list(NOTE))


if __name__ == "__main__":
    main()
