#!/usr/bin/env python3
"""t1014_spurts.py - T1014 phase 2: run nas_move.py on the NAS in 15-minute spurts until the list is done, and
after each spurt add a 3-line Review summary (done / counts / errors) to T1014 on the NUC.

Run (PC, detached):  python D:\\scratchpad\\tools\\t1014_spurts.py ops-2-hold.jsonl journal-2-hold.jsonl
Each spurt: ssh jay@100.64.0.12 runs nas_move.py --minutes 15 in the foreground and prints its last line.
Review lines go through ~/learning-src/todo_note.py on the NUC from a JSON file (scp, never a heredoc).
Stops when a spurt makes no progress (only refused ops left) or after 12 spurts.
OUTCOME 2026-11-17: written for T1014 phase 2.
"""
import json
import re
import subprocess
import sys
import tempfile
import time

NAS, NUC = "jay@100.64.0.12", "server@100.64.0.10"
SSH = ["ssh", "-o", "BatchMode=yes"]


def spurt(ops, journal):
    """Run one spurt; return its 'spurt end' line."""
    cmd = (f"cd ~/t1014 && python3 nas_move.py {ops} {journal} --minutes 15 >> move-spurts.log 2>&1; "
           f"grep 'spurt end' move-spurts.log | tail -1; "
           f"python3 -c \"import json,collections as C; c=C.Counter(json.loads(l).get('err','ok')[:40] "
           f"for l in open('{journal}')); print(dict(c))\"")
    out = subprocess.run(SSH + [NAS, cmd], capture_output=True, text=True, timeout=3600, check=False).stdout.strip()
    return out


def review(n, text):
    """Add one Review line to T1014."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"T1014": [text]}, f)
    subprocess.run(["scp", "-q", f.name, f"{NUC}:/home/user/t1014_spurt.json"], check=True)
    subprocess.run(SSH + [NUC, "cd ~/learning-src && python3 todo_note.py ~/t1014_spurt.json"], check=True)


def main(ops, journal):
    for n in range(1, 13):
        out = spurt(ops, journal)
        lines = out.splitlines()
        end = next((l for l in lines if "spurt end" in l), "no spurt line")
        totals = lines[-1] if lines else ""
        m = re.search(r"ok (\d+), errors (\d+), ([\d.]+) GB, left (\d+)", end)
        ok, bad, gb, left = (int(m[1]), int(m[2]), m[3], int(m[4])) if m else (0, 0, "0", -1)
        stamp = time.strftime("%Y-%m-%d %H:%M")
        review(n, f"Review: ({stamp}, hold spurt {n}) Done: {ok} files to the holding folder this spurt "
                  f"({gb} GB); {left} left. Journal so far: {totals}. Errors this spurt: {bad} (refused ops "
                  f"change nothing; 'kept copy missing' = the copy that stays was not found, so the file stayed).")
        print(stamp, end, totals, flush=True)
        if left == 0 or ok == 0:
            break


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
