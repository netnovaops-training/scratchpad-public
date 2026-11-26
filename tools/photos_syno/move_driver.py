#!/usr/bin/env python3
"""move_driver.py - step 3 driver on the PC: one year folder per batch, storage check + pause between batches.

Run:  python D:\\scratchpad\\tools\\photos_syno\\move_driver.py [pause_seconds]   (log: OUT\\step3-move.log)
Stops at once (exit 2) if storage_check finds an mdstat change or a new storage warning (coordinator 11-24).
OUTCOME 2026-11-24: written for plan step 3.
"""
import json
import sys
import time

from nas import run
from storage_check import check

W = "/var/services/homes/jay/photos_syno/"
LOG = r"D:\backups\photo-verify\2026-11-24\step3-move.log"
SINCE = time.strftime("%Y-%m-%dT%H:%M:%S")


def say(m):
    line = time.strftime("%F %T ") + m
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main(pause):
    rc, out, _ = run(f"cd {W} && python3 -c \"import json;print(json.dumps(sorted({{json.loads(l)['batch'] for l in open('move-ops.jsonl')}})))\"")
    batches = json.loads(out)
    batches = [b for b in batches if b != "From-M-Pictures"] + ["From-M-Pictures"]
    say(f"start: {len(batches)} batches; storage warnings since {SINCE} stop the run")
    for b in batches:
        ok, why = check(SINCE)
        if not ok:
            say("STOP before batch " + b + ": " + why)
            sys.exit(2)
        rc, out, err = run(f"cd {W} && python3 nas_move.py run move-ops.jsonl move-journal.jsonl --batch '{b}'",
                           timeout=7200)
        say(f"batch {b} rc {rc}: {out.strip().splitlines()[-1] if out.strip() else ''} {err.strip()[-200:]}")
        if rc != 0:
            say("STOP: batch failed")
            sys.exit(1)
        time.sleep(pause)
    ok, why = check(SINCE)
    say("end storage check: " + why)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
