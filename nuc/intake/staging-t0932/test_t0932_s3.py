#!/usr/bin/env python3
"""test_t0932_s3.py - T0932 step 3 test for app.place() (2026-11-22). Runs INSIDE a sandbox intake image with
two bind mounts on DIFFERENT disks: /srv/unsorted (staging, USB SSD stand-in) and /srv/other (NUC disk).
Never touches the live intake.

    docker run --rm ... --entrypoint python <image> test_t0932_s3.py

Checks: (1) cross-disk filing: the file arrives, sha256 matches, the staging copy is gone, no .part left;
(2) a wrong digest (stands in for a copy that came out different) raises, leaves NO .part in the library and
KEEPS the staging copy; (3) same disk: still a rename (inode kept), staging copy gone; (4) duplicate check
unchanged. Prints ALL PASS or FAIL lines.
"""
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


def staged(name, data):
    p = pathlib.Path("/srv/unsorted/.tmp") / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p, hashlib.sha256(data).hexdigest()


other = pathlib.Path("/srv/other/lib")
same = pathlib.Path("/srv/unsorted/samelib")
check(os.stat("/srv/unsorted").st_dev != os.stat("/srv/other").st_dev, "the two mounts are on different disks")

data = os.urandom(3 * 1048576 + 17)
tmp, dg = staged("a.bin", data)
res = app.place(tmp, other, "a.bin", dg, {})
dst = pathlib.Path(res["path"])
check(res["status"] == "filed" and dst.exists() and app.sha256(dst) == dg, "cross-disk: filed and sha256 matches")
check(not tmp.exists(), "cross-disk: staging copy removed after the check")
check(not list(other.glob("*.part")), "cross-disk: no .part left")

tmp2, dg2 = staged("b.bin", os.urandom(1048576))
try:
    app.place(tmp2, other, "b.bin", "0" * 64, {})
    check(False, "wrong digest raised")
except OSError as e:
    check("copy check failed" in str(e), f"wrong digest raised OSError ({e})")
check(not list(other.glob("b.bin*")), "wrong digest: no .part and no file in the library")
check(tmp2.exists() and app.sha256(tmp2) == dg2, "wrong digest: staging copy KEPT intact")

tmp3, dg3 = staged("c.bin", os.urandom(4096))
same.mkdir(parents=True, exist_ok=True)
ino = tmp3.stat().st_ino
res3 = app.place(tmp3, same, "c.bin", dg3, {})
check(res3["status"] == "filed" and pathlib.Path(res3["path"]).stat().st_ino == ino and not tmp3.exists(),
      "same disk: still a rename (same inode), staging copy gone")

tmp4, dg4 = staged("a-again.bin", data)
res4 = app.place(tmp4, other, "a-again.bin", dg4, {})
check(res4["status"] == "duplicate" and not tmp4.exists(), "duplicate on the other disk still skipped")
print("ALL PASS" if not bad else f"SOME FAILED ({bad})")
sys.exit(1 if bad else 0)
