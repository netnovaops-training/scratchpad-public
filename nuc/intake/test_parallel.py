#!/usr/bin/env python3
"""test_parallel.py - sandbox test for T0928 (numbered pieces in any order, several at once), 2026-11-19.

    python3 test_parallel.py BASE KEY DATA_DIR

BASE: a sandbox intake (INTAKE_KEY=KEY, DATA_DIR mounted as /srv). Never the live intake.
Sends a 40 MB file as 4 MB pieces in REVERSED order, 4 at a time, with one piece cut short and one with a
wrong checksum, then checks the server lists exactly those two as missing, re-sends them, finishes, and
the filed file's sha256 equals the original. Also: a 300 MB declared file gets 16 MB pieces.
OUTCOME: see the T0928 Review line.
"""
import concurrent.futures as cf
import hashlib
import http.client
import json
import os
import pathlib
import sys
import urllib.error
import urllib.request

ok_all = True


def check(name, cond):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, flush=True)


def main():
    base, key, data = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3])
    hdr = {"X-Auth-Email": "user1@example.com", "X-Drop-Key": key, "X-Auth-Level": "full", "Content-Type": "application/json"}

    def call(method, path, body=None, extra=None):
        rq = urllib.request.Request(base + path, data=body if isinstance(body, (bytes, type(None))) else
                                    json.dumps(body).encode(), method=method, headers={**hdr, **(extra or {})})
        try:
            with urllib.request.urlopen(rq, timeout=120) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    blob = os.urandom(40 * 1048576 + 12345)
    st, s = call("POST", "/up/start", {"name": "parallel-test.bin", "size": len(blob), "parallel": True})
    check(f"start parallel 200, chunk 4 MB ({st} {s.get('chunk')})", st == 200 and s["parallel"] and s["chunk"] == 4194304)
    uid, c = s["id"], s["chunk"]
    n = -(-len(blob) // c)
    cut, bad = n - 2, 3

    def put(i):
        p = blob[i * c:(i + 1) * c]
        h = hashlib.sha256(p).hexdigest()
        if i == bad:
            h = "0" * 64
        if i == cut:                                     # announce the full length, send half, hang up
            host, port = base.split("//")[1].split(":")
            conn = http.client.HTTPConnection(host, int(port), timeout=10)
            conn.putrequest("PUT", f"/up/{uid}/piece/{i}")
            for k, v in {**hdr, "Content-Length": str(len(p)), "Content-Type": "application/octet-stream"}.items():
                conn.putheader(k, v)
            conn.endheaders()
            conn.send(p[:len(p) // 2])
            conn.close()
            return i, "cut"
        return i, call("PUT", f"/up/{uid}/piece/{i}", p, {"X-Piece-Sha256": h,
                                                          "Content-Type": "application/octet-stream"})[0]

    with cf.ThreadPoolExecutor(4) as ex:
        res = dict(ex.map(put, reversed(range(n))))
    check(f"bad checksum piece -> 422 ({res[bad]})", res[bad] == 422)
    import time
    time.sleep(1)
    st, info = call("GET", f"/up/{uid}")
    check(f"missing = exactly the cut + the damaged piece ({info.get('missing')})", sorted(info["missing"]) == sorted([cut, bad]))
    st, fin = call("POST", f"/up/{uid}/finish")
    check(f"finish refused while pieces are missing ({st})", st == 409)
    for i in (cut, bad):
        p = blob[i * c:(i + 1) * c]
        call("PUT", f"/up/{uid}/piece/{i}", p, {"X-Piece-Sha256": hashlib.sha256(p).hexdigest(),
                                                "Content-Type": "application/octet-stream"})
    st, fin = call("POST", f"/up/{uid}/finish")
    path = fin[0].get("path", "") if st == 200 else ""
    host_path = data / path.replace("/srv/", "", 1) if path else None
    same = host_path and host_path.exists() and hashlib.sha256(host_path.read_bytes()).hexdigest() == hashlib.sha256(blob).hexdigest()
    check(f"finished and rebuilt in order: sha256 equal ({st} {path})", st == 200 and same)
    st, s2 = call("POST", "/up/start", {"name": "big.bin", "size": 300 * 1048576, "parallel": True})
    check(f"300 MB file gets 16 MB pieces ({s2.get('chunk')})", st == 200 and s2["chunk"] == 16 * 1048576)
    call("POST", f"/up/{s2['id']}/cancel")
    print("ALL PASS" if ok_all else "SOME FAILED")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
