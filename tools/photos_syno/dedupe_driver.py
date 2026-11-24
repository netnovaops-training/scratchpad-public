#!/usr/bin/env python3
"""dedupe_driver.py - step 4 driver on the PC: delete in batches of 5,000 with sync + storage check + pause between.

Run:  python D:\\scratchpad\\tools\\photos_syno\\dedupe_driver.py TOTAL [pause_seconds]   (log: OUT\\step4-delete.log)
TOTAL = line count of dedupe-plan.jsonl. Stops at once (exit 2) on any storage warning (coordinator 11-24).
OUTCOME 2026-11-24: written for plan step 4.
"""
import sys
import time

from nas import run
from storage_check import check

W = "/var/services/homes/jay/photos_syno/"
LOG = r"D:\backups\photo-verify\2026-11-24\step4-delete.log"
SINCE = "2026-11-24T04:47:38"
BATCH = 5000


def say(m):
    line = time.strftime("%F %T ") + m
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main(total, pause):
    rc, out, _ = run("df -B1 /volume1 | tail -1")
    say(f"start: {total} planned deletes; df before: {out.strip()}")
    for start in range(1, total + 1, BATCH):
        ok, why = check(SINCE)
        if not ok:
            say(f"STOP before batch {start}: {why}")
            sys.exit(2)
        rc, out, err = run(f"cd {W} && python3 nas_dedupe.py run dedupe-plan.jsonl dedupe-log.jsonl "
                           f"--from {start} --count {BATCH}", timeout=7200)
        say(f"batch {start}: rc {rc} {out.strip()} {err.strip()[-200:]}")
        if rc != 0:
            say("STOP: batch failed")
            sys.exit(1)
        time.sleep(pause)
    ok, why = check(SINCE)
    rc, out, _ = run("df -B1 /volume1 | tail -1")
    say(f"end storage check: {why}; df after: {out.strip()}")


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]) if len(sys.argv) > 2 else 20)
