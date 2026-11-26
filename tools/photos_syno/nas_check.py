#!/usr/bin/env python3
"""nas_check.py - re-hash files ON the NAS and compare with expected SHA-256 values. Read-only.

Run (NAS):  python3 nas_check.py LIST.jsonl      LIST lines: {"p": "/volume1/...", "h": "<sha256>"}
Prints: checked, ok, mismatches, missing (+ the first 30 problem paths).
OUTCOME 2026-11-24: written to verify the step 5 PC copies on the NAS side.
"""
import hashlib
import json
import sys


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(path):
    ok, bad = 0, []
    items = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    for it in items:
        try:
            if sha(it["p"]) == it["h"]:
                ok += 1
            else:
                bad.append(("MISMATCH", it["p"]))
        except OSError as e:
            bad.append(("MISSING " + type(e).__name__, it["p"]))
    print(f"checked {len(items)}: ok {ok}, problems {len(bad)}")
    for b in bad[:30]:
        print("  ", *b)


if __name__ == "__main__":
    main(sys.argv[1])
