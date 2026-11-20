#!/usr/bin/env python3
"""test_t0931.py - sandbox test for the 2026-11-20 re-audit fixes (T0931 must items), never the live intake.

    python3 test_t0931.py BASE KEY DATA_DIR CONTAINER

BASE: a sandbox intake with INTAKE_KEY=KEY and DATA_DIR mounted as /srv/unsorted.
CONTAINER: the sandbox's docker name (restarted once for the stale-lock check).
Checks:
  1. a damaged re-send (bad checksum) of a piece that already arrived does not change the stored file;
  2. a cut-short re-send of an arrived piece does not change it either;
  3. a clean duplicate is acknowledged as "already" without a second write;
  4. a parallel start reserves real disk blocks (fallocate), not a sparse file;
  5. a finish lock left by a restart is cleared at start-up, so finish works at once (no 1 h "working");
  6. the finished file's sha256 equals the original.
OUTCOME: see the T0931 Review line.
"""
import hashlib
import http.client
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

ok_all = True


def check(name, cond):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, flush=True)


def main():
    base, key, data, container = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3]), sys.argv[4]
    hdr = {"X-Auth-Email": "user1@example.com", "X-Drop-Key": key, "X-Auth-Level": "full", "Content-Type": "application/json"}

    def call(method, path, body=None, extra=None):
        rq = urllib.request.Request(base + path, data=body if isinstance(body, (bytes, type(None))) else
                                    json.dumps(body).encode(), method=method, headers={**hdr, **(extra or {})})
        try:
            with urllib.request.urlopen(rq, timeout=120) as r:
                return r.status, json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"null")

    blob = os.urandom(12 * 1048576 + 777)
    st, s = call("POST", "/up/start", {"name": "t0931.bin", "size": len(blob), "parallel": True})
    check(f"start 200 ({st})", st == 200)
    uid, c = s["id"], s["chunk"]
    n = -(-len(blob) // c)
    stage = data / ".atomic-staging"
    part = stage / f"{uid}.part"
    st_blocks = part.stat().st_blocks * 512 if part.exists() else 0
    check(f"disk blocks reserved at start ({st_blocks} >= {len(blob)})", st_blocks >= len(blob))

    def piece(i, body=None, sha=None):
        p = blob[i * c:(i + 1) * c] if body is None else body
        return call("PUT", f"/up/{uid}/piece/{i}", p, {"X-Piece-Sha256": sha or hashlib.sha256(p).hexdigest(),
                                                      "Content-Type": "application/octet-stream"})

    for i in range(n):
        piece(i)
    good0 = blob[:c]
    st, _ = piece(0, body=os.urandom(c), sha="0" * 64)                       # damaged re-send
    check(f"damaged re-send refused 422 ({st})", st == 422)
    host, port = base.split("//")[1].split(":")                               # cut-short re-send
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.putrequest("PUT", f"/up/{uid}/piece/0")
    for k, v in {**hdr, "Content-Length": str(c), "Content-Type": "application/octet-stream"}.items():
        conn.putheader(k, v)
    conn.endheaders()
    conn.send(os.urandom(c // 2))
    conn.close()
    time.sleep(0.5)
    with part.open("rb") as f:
        check("piece 0 on disk unchanged after both bad re-sends", f.read(c) == good0)
    st, r = piece(0)
    check(f"clean duplicate acknowledged as already in ({st} {r})", st == 200 and r.get("already"))

    (stage / f"{uid}.finishing").touch()                                    # pretend a restart mid-finish
    subprocess.run(["docker", "restart", container], check=True, capture_output=True)
    for _ in range(40):
        try:
            with urllib.request.urlopen(urllib.request.Request(base + "/healthz", headers=hdr), timeout=3):
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    check("stale finish lock cleared at start-up", not (stage / f"{uid}.finishing").exists())
    st, res = call("POST", f"/up/{uid}/finish")
    check(f"finish works at once after restart ({st})", st == 200 and isinstance(res, list))
    path = res[0].get("path", "") if isinstance(res, list) and res else ""
    host_path = pathlib.Path(path.replace("/srv/unsorted", str(data), 1))
    got = hashlib.sha256(host_path.read_bytes()).hexdigest() if host_path.is_file() else "missing"
    check("filed file sha256 == original", got == hashlib.sha256(blob).hexdigest())
    print("ALL PASS" if ok_all else "SOME FAILED")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
