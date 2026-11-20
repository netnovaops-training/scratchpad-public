#!/usr/bin/env python3
"""test_finish.py - sandbox test for T0925 (A4 finish retry, A10 '..' name, A11 disk reserve), 2026-11-19.

    python3 test_finish.py BASE RESERVE_BASE DATA_DIR

BASE: a normal sandbox. RESERVE_BASE: a sandbox started with INTAKE_RESERVE_MB huge (every start refused).
DATA_DIR: the host folder mounted as the sandbox's /srv (to check what was filed). Never the live intake.
"""
import concurrent.futures as cf
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ME = {"X-Auth-Email": "user@example.com", "Content-Type": "application/json"}
ok_all = True


def call(base, method, path, body=None, raw=False):
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    rq = urllib.request.Request(base + path, data=data, method=method, headers=ME)
    try:
        with urllib.request.urlopen(rq, timeout=120) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(name, cond):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)


def upload(base, name, data):
    st, s = call(base, "POST", "/up/start", {"name": name, "size": len(data)})
    if st != 200:
        return st, None
    call(base, "PUT", f"/up/{s['id']}?offset=0", data, raw=True)
    return st, s["id"]


def main():
    base, reserve_base, data_dir = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
    body = os.urandom(300_000)
    st, uid = upload(base, "retry-test.bin", body)
    with cf.ThreadPoolExecutor(2) as ex:   # two finishes at once: one files, the other waits or reuses
        a, b = ex.map(lambda _: call(base, "POST", f"/up/{uid}/finish"), range(2))
    codes = sorted([a[0], b[0]])
    check(f"two finishes at once: {codes} (200 + 409 working, or 200 + 200)",
          codes in ([200, 409], [200, 200]))
    st3, again = call(base, "POST", f"/up/{uid}/finish")
    first = a[1] if a[0] == 200 else b[1]
    check("a later retry gets the SAME stored answer", st3 == 200 and again == first)
    st4, info = call(base, "GET", f"/up/{uid}")
    check("GET after finish: offset == size (phone will not re-send)", st4 == 200 and info["offset"] == len(body))
    filed = list((data_dir / "unsorted").glob("retry-test*.bin"))
    check(f"filed exactly once ({len(filed)} copies)", len(filed) == 1)
    st, uid2 = upload(base, "..", b"dotdot")
    st5, res = call(base, "POST", f"/up/{uid2}/finish")
    check(f"name '..' is filed as 'shared', not a 500 ({st5})", st5 == 200 and (data_dir / "unsorted" / "shared").exists())
    st6, _ = call(reserve_base, "POST", "/up/start", {"name": "big.bin", "size": 1000})
    check(f"start refused when space is short: {st6} == 507", st6 == 507)
    print("ALL PASS" if ok_all else "SOME FAILED")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
