#!/usr/bin/env python3
"""nas_restore.py - T1014: undo nas_move.py. Puts every journaled file back where it was. Run ON the NAS.

Run (NAS):  python3 ~/t1014/nas_restore.py JOURNAL.jsonl [JOURNAL2.jsonl ...] [--dry]
            Undo hold moves first, then place moves:
            python3 ~/t1014/nas_restore.py ~/t1014/journal-2-hold.jsonl ~/t1014/journal-1-place.jsonl
Reads each journal's ok lines newest first and moves dst back to src (rename, or reflink copy across shares).
Skips a line when dst is gone or src is taken again, and says so. Writes ~/t1014/restore-<time>.log.
JPGs made by raw_to_jpg.py are not removed by this script: they are listed in converted.jsonl on the PC.
Copy of this script sits next to the plan: D:\\backups\\photo-verify\\unify-restore.py.
OUTCOME 2026-11-17: written before the first phase-2 move, as Jason asked.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nas_move import move


def main(argv):
    dry = "--dry" in argv
    files = [a for a in argv if not a.startswith("--")]
    with open(os.path.expanduser(time.strftime("~/t1014/restore-%Y%m%d-%H%M%S.log")), "a", encoding="utf-8") as log:
        back = skip = 0
        for jf in files:
            with open(jf, encoding="utf-8") as fh:
                recs = [json.loads(l) for l in fh if l.strip()]
            for r in reversed([r for r in recs if r.get("ok")]):
                if not os.path.exists(r["dst"]) or os.path.lexists(r["src"]):
                    skip += 1
                    log.write(f"skip {r['n']}: dst gone or src taken: {r['src']}\n")
                    continue
                if not dry:
                    move(r["dst"], r["src"])
                back += 1
        msg = f"{'DRY: would restore' if dry else 'restored'} {back}, skipped {skip}"
        log.write(msg + "\n")
        print(msg)


if __name__ == "__main__":
    main(sys.argv[1:])
