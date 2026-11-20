#!/usr/bin/env python3
"""test_resumable.py - sandbox test for resumable.py (T0884, 2026-11-18). Talks to the SANDBOX intake
(127.0.0.1:8199, scratch folders), never the live one. Sends X-Auth-Email itself because the sandbox has
no Caddy in front (that header is exactly what Caddy's forward_auth sets on the live path).

Checks: start; pieces; a piece cut off mid-way (signal drop) keeps its received bytes; GET gives the resume
point; a stale offset gets 409 + the real offset; another person gets 404; over-size refused; finish files it
and the stored file's sha256 equals the original; finish with missing bytes -> 409.
"""
import hashlib
import http.client
import json
import os
import socket
import sys
import urllib.request

BASE, HOST, PORT = "http://127.0.0.1:8199", "127.0.0.1", 8199
ME, OTHER = "user@example.com", "someone-else@example.com"
ok_all = True


def call(method, path, body=None, who=ME, raw=False):
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    rq = urllib.request.Request(BASE + path, data=data, method=method,
                                headers={"X-Auth-Email": who, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=300) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(name, cond):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)


def cut_piece(uid, offset, data, send_bytes):
    """Start a PUT that claims len(data) bytes, send only send_bytes, then drop the connection."""
    s = socket.create_connection((HOST, PORT))
    head = (f"PUT /up/{uid}?offset={offset} HTTP/1.1\r\nHost: x\r\nX-Auth-Email: {ME}\r\n"
            f"Content-Length: {len(data)}\r\n\r\n").encode()
    s.sendall(head + data[:send_bytes])
    s.close()


def main():
    blob = os.urandom(10 * 1048576 + 12345)         # 10 MB + a bit: uneven last piece
    want = hashlib.sha256(blob).hexdigest()
    st, j = call("POST", "/up/start", {"name": "atomic-test.bin", "size": len(blob)})
    check("start 200", st == 200 and j["offset"] == 0)
    uid, chunk = j["id"], j["chunk"]
    st, _ = call("GET", f"/up/{uid}", who=OTHER)
    check("other person gets 404", st == 404)
    st, j = call("PUT", f"/up/{uid}?offset=0", blob[:chunk], raw=True)
    check("piece 1 accepted", st == 200 and j["offset"] == chunk)
    cut_piece(uid, chunk, blob[chunk:2 * chunk], 1048576)   # signal drops 1 MB into piece 2
    import time; time.sleep(1)
    st, j = call("GET", f"/up/{uid}")
    off = j["offset"]
    check(f"resume point after drop = {off} (between {chunk} and {2*chunk})", chunk <= off <= 2 * chunk)
    st, j = call("PUT", f"/up/{uid}?offset=0", blob[:10], raw=True)
    check("stale offset -> 409 with real offset", st == 409 and j["offset"] == off)
    st, j = call("POST", f"/up/{uid}/finish")
    check("finish too early -> 409", st == 409)
    while off < len(blob):
        st, j = call("PUT", f"/up/{uid}?offset={off}", blob[off:off + chunk], raw=True)
        if st != 200:
            check(f"piece at {off}", False); break
        off = j["offset"]
    check("all bytes in", off == len(blob))
    st, j = call("PUT", f"/up/{uid}?offset={off}", b"x", raw=True)
    check("byte past declared size -> 413", st == 413)
    st, res = call("POST", f"/up/{uid}/finish")
    check("finish 200", st == 200 and isinstance(res, list))
    print("  verdict:", json.dumps(res)[:300])
    path = res[0].get("path", "") if res else ""
    host = path.replace("/srv/", os.path.expanduser("~/build/intake-sbx-data/"), 1)
    got = hashlib.sha256(open(host, "rb").read()).hexdigest() if os.path.exists(host) else "missing"
    check("stored file sha256 == original", got == want)
    st, _ = call("POST", "/up/start", {"name": "big.bin", "size": 10 ** 13})
    check("over INTAKE_MAX_MB refused (413)", st == 413)
    st, _ = call("GET", "/up/../../etc/passwd")
    check("path-like id refused", st in (404, 422))
    print("ALL PASS" if ok_all else "SOME FAILED")
    sys.exit(0 if ok_all else 1)


if __name__ == "__main__":
    main()
