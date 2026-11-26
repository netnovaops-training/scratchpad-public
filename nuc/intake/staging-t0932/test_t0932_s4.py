#!/usr/bin/env python3
"""test_t0932_s4.py - T0932 step 4 test (2026-11-22). Runs INSIDE a sandbox intake image started with
INTAKE_DEST_RESERVE_MB set far above any disk's free space, with /srv/unsorted and /srv/other on different
disks (same mounts as test_t0932_s3.py). Never touches the live intake.

Checks: a cross-disk filing is refused with ENOSPC, nothing (no file, no .part) lands in the library, and the
staging copy is kept intact; a same-disk filing is NOT refused (a rename needs no room). ALL PASS or FAIL lines.
"""
import errno
import hashlib
import os
import pathlib
import sys

import app

bad = 0


def check(ok, what):
    global bad
    print(("PASS " if ok else "FAIL ") + what)
    bad += not ok


check(app.DEST_RESERVE > 10**15, f"DEST_RESERVE from env is huge ({app.DEST_RESERVE // 1048576} MB)")
tmp = pathlib.Path("/srv/unsorted/.tmp/full.bin")
tmp.parent.mkdir(parents=True, exist_ok=True)
data = os.urandom(65536)
tmp.write_bytes(data)
dg = hashlib.sha256(data).hexdigest()
lib = pathlib.Path("/srv/other/lib")
try:
    app.place(tmp, lib, "full.bin", dg, {})
    check(False, "full library disk refused")
except OSError as e:
    check(e.errno == errno.ENOSPC, f"full library disk refused with ENOSPC ({e})")
check(not list(lib.glob("full.bin*")), "nothing written to the library")
check(tmp.exists() and app.sha256(tmp) == dg, "staging copy kept intact")
same = pathlib.Path("/srv/unsorted/samelib")
res = app.place(tmp, same, "full.bin", dg, {})
check(res["status"] == "filed" and not tmp.exists(), "same disk still filed (rename needs no room)")
print("ALL PASS" if not bad else f"SOME FAILED ({bad})")
sys.exit(1 if bad else 0)
