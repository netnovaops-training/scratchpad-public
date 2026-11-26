#!/usr/bin/env python3
"""test_t0932_s2.py - sandbox test for the T0932 step 2 staging cap (2026-11-22). Talks to a SANDBOX intake
started with INTAKE_STAGE_MAX_MB=10, never the live one.

    INTAKE_KEY=<sandbox key> python3 test_t0932_s2.py BASE

Checks: a 6 MB start is accepted; a second 6 MB start (12 MB > 10 MB cap) gets 507; the same person's
idempotent re-start of the FIRST upload (same lastModified) is still handed back (not refused by the cap);
a 3 MB start (6 + 3 = 9 MB) still fits. Prints ALL PASS or FAIL lines.
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = sys.argv[1]
KEY = os.environ.get("INTAKE_KEY", "")
HDR = {"X-Drop-Key": KEY, "X-Auth-Email": "tester@example.com", "X-Auth-Level": "full",
       "Content-Type": "application/json"}
bad = 0


def start(name, size, lm):
    """POST /up/start; returns (status, body dict)."""
    req = urllib.request.Request(BASE + "/up/start", data=json.dumps(
        {"name": name, "size": size, "lastModified": lm}).encode(), headers=HDR, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def check(ok, what):
    global bad
    print(("PASS " if ok else "FAIL ") + what)
    bad += not ok


MB = 1048576
c1, b1 = start("a.bin", 6 * MB, 1700000000001)
check(c1 == 200, f"6 MB start under the 10 MB cap -> {c1}")
c2, b2 = start("b.bin", 6 * MB, 1700000000002)
check(c2 == 507, f"second 6 MB start (12 MB > cap) -> {c2} {b2.get('detail', '')}")
c3, b3 = start("a.bin", 6 * MB, 1700000000001)
check(c3 == 200 and b3.get("resumed") and b3.get("id") == b1.get("id"), f"re-start of the first upload handed back -> {c3} resumed={b3.get('resumed')}")
c4, _ = start("c.bin", 3 * MB, 1700000000004)
check(c4 == 200, f"3 MB start (6 + 3 = 9 MB) fits -> {c4}")
print("ALL PASS" if not bad else f"SOME FAILED ({bad})")
sys.exit(1 if bad else 0)
