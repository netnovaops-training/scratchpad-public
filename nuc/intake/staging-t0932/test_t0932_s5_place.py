#!/usr/bin/env python3
"""test_t0932_s5_place.py - T0932 step 5 (2026-11-22, autopilot): app.place() with a 2 GB file across disks.
Runs INSIDE the sandbox intake container: /srv/unsorted = USB SSD stand-in, /srv/other = NUC disk.

    docker exec <sandbox> python test_t0932_s5_place.py /srv/unsorted/<the 2 GB file from the upload test>

Checks: (1) wrong digest on a 2 GB copy: OSError, no .part in the library, staging copy kept and intact
(the failure path at real size, not 1 MB); (2) right digest: filed on the other disk, sha256 matches, staging
copy gone, no .part; (3) the container's memory peak stays far below the file size (place() streams, it never
holds the file). Prints ALL PASS or FAIL lines.
"""
import hashlib
import os
import pathlib
import shutil
import sys
import time

import app

bad = 0


def check(ok, what):
    global bad
    print(("PASS " if ok else "FAIL ") + what, flush=True)
    bad += not ok


src = pathlib.Path(sys.argv[1])
lib = pathlib.Path("/srv/other/lib")
lib.mkdir(parents=True, exist_ok=True)
check(os.stat("/srv/unsorted").st_dev != os.stat("/srv/other").st_dev, "the two mounts are on different disks")
dg = app.sha256(src)
size = src.stat().st_size

tmp = pathlib.Path("/srv/unsorted/.tmp/t0932-s5.bin")
tmp.parent.mkdir(parents=True, exist_ok=True)
shutil.copyfile(src, tmp)                     # same SSD: the staged copy place() will work on
t0 = time.time()
try:
    app.place(tmp, lib, "t0932-s5.bin", "0" * 64, {})
    check(False, "2 GB wrong digest raised")
except OSError as e:
    check("copy check failed" in str(e), f"2 GB wrong digest raised OSError in {time.time() - t0:.0f} s")
check(not list(lib.glob("t0932-s5*")), "wrong digest: no .part and no file in the library")
check(tmp.exists() and tmp.stat().st_size == size, "wrong digest: staging copy kept, full size")

t1 = time.time()
res = app.place(tmp, lib, "t0932-s5.bin", dg, {})
secs = time.time() - t1
dst = pathlib.Path(res["path"])
check(res["status"] == "filed" and dst.exists() and app.sha256(dst) == dg,
      f"2 GB cross-disk filed, sha256 matches ({secs:.0f} s = {size / 1048576 / max(secs, 1):.0f} MB/s incl. check)")
check(not tmp.exists(), "staging copy removed after the check")
check(not list(lib.glob("*.part")), "no .part left in the library")

peak = None
for p in ("/sys/fs/cgroup/memory.peak", "/sys/fs/cgroup/memory/memory.max_usage_in_bytes"):
    try:
        peak = int(pathlib.Path(p).read_text().split()[0])
        break
    except (OSError, ValueError):
        pass
# memory.peak counts page cache too, so it can be large after 2 GB of file I/O; the process RSS high-water
# mark (VmHWM) is the real "did it hold the file" number.
hwm = next((int(l.split()[1]) * 1024 for l in pathlib.Path("/proc/self/status").read_text().splitlines()
            if l.startswith("VmHWM:")), 0)
print(f"  memory: this process VmHWM {hwm >> 20} MB; container cgroup peak {(peak or 0) >> 20} MB (incl. cache)")
check(hwm < size // 4, f"process memory high-water {hwm >> 20} MB is far below the {size >> 20} MB file")
print("ALL PASS" if not bad else f"SOME FAILED ({bad})")
sys.exit(1 if bad else 0)
