#!/usr/bin/env python3
"""test_t0932_s5.py - T0932 step 5 (2026-11-22, autopilot): 2 GB upload through the SANDBOX intake whose whole
/srv/unsorted sits on the USB SSD (stand-in folder on /mnt/vault). Never talks to the live intake.

    INTAKE_KEY=... python3 test_t0932_s5.py BASE SSD_DIR SRC_FILE

BASE = sandbox URL, SSD_DIR = host folder mounted as /srv (so /srv/unsorted = SSD_DIR/unsorted),
SRC_FILE = the 2 GB random file on the NUC disk (read piece by piece, never whole into memory).

Checks: (1) the staging cap (INTAKE_STAGE_MAX_MB, sandbox set to 3000) lets one 2 GB start in and refuses a
second with 507; (2) a piece cut off about 1 GB in (signal drop) keeps its bytes and GET gives the resume point;
(3) the rest goes in and finish files it; (4) the stored file's sha256 equals the source; (5) no .part and no
staging leftovers for this upload. Prints ALL PASS or FAIL lines.
"""
import hashlib
import json
import os
import pathlib
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE, SSD, SRC = sys.argv[1], pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])
HOST, PORT = urllib.parse.urlsplit(BASE).hostname, urllib.parse.urlsplit(BASE).port
HDR = {"X-Drop-Key": os.environ["INTAKE_KEY"], "X-Auth-Level": "full", "X-Auth-Email": "user1@example.com"}
bad = 0


def check(cond, what):
    global bad
    print(("PASS " if cond else "FAIL ") + what, flush=True)
    bad += not cond


def call(method, path, body=None, raw=False, timeout=900):
    """One JSON request to the sandbox; returns (status, parsed body). 900 s timeout: finish hashes 2 GB."""
    data = body if raw else (json.dumps(body).encode() if body is not None else None)
    rq = urllib.request.Request(BASE + path, data=data, method=method,
                                headers={**HDR, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def piece(f, off, n):
    f.seek(off)
    return f.read(n)


def cut_piece(uid, offset, data, send_bytes):
    """Start a PUT claiming len(data) bytes, send only send_bytes, drop the connection (a lost signal)."""
    s = socket.create_connection((HOST, PORT))
    head = (f"PUT /up/{uid}?offset={offset} HTTP/1.1\r\nHost: x\r\n"
            + "".join(f"{k}: {v}\r\n" for k, v in HDR.items())
            + f"Content-Length: {len(data)}\r\n\r\n").encode()
    s.sendall(head + data[:send_bytes])
    s.close()


def main():
    size = SRC.stat().st_size
    h = hashlib.sha256()
    with SRC.open("rb") as f:
        for blk in iter(lambda: f.read(8 << 20), b""):
            h.update(blk)
    want = h.hexdigest()
    print(f"source {size} bytes sha256 {want[:16]}...", flush=True)

    st, j = call("POST", "/up/start", {"name": "t0932-2gb.bin", "size": size})
    check(st == 200 and j["offset"] == 0, f"2 GB start accepted under the 3000 MB cap ({st})")
    uid, chunk = j["id"], j["chunk"]
    st2, j2 = call("POST", "/up/start", {"name": "t0932-second.bin", "size": size})
    check(st2 == 507, f"second 2 GB start refused by the staging cap ({st2}: {str(j2)[:80]})")

    t0 = time.time()
    off = 0
    cut_done = False
    with SRC.open("rb") as f:
        while off < size:
            if not cut_done and off >= 1 << 30:          # about 1 GB in: drop the signal mid-piece
                cut_piece(uid, off, piece(f, off, chunk), chunk // 3)
                cut_done = True
                time.sleep(1)
                _, g = call("GET", f"/up/{uid}")
                check(off <= g["offset"] <= off + chunk, f"resume point after drop {g['offset']} (from {off})")
                off = g["offset"]
                continue
            st, j = call("PUT", f"/up/{uid}?offset={off}", piece(f, off, chunk), raw=True)
            if st != 200:
                check(False, f"piece at {off} -> {st} {j}")
                break
            off = j["offset"]
    secs = time.time() - t0
    check(off == size, f"all bytes in ({off}/{size}) in {secs:.0f} s = {size / 1048576 / max(secs, 1):.0f} MB/s")

    t1 = time.time()
    st, res = call("POST", f"/up/{uid}/finish")
    check(st == 200 and isinstance(res, list), f"finish 200 in {time.time() - t1:.0f} s")
    print("  verdict:", json.dumps(res)[:300], flush=True)
    path = res[0].get("path", "") if isinstance(res, list) and res else ""
    host = SSD / path.replace("/srv/", "", 1) if path.startswith("/srv/") else pathlib.Path(path)
    got = "missing"
    if host.exists():
        g = hashlib.sha256()
        with host.open("rb") as f:
            for blk in iter(lambda: f.read(8 << 20), b""):
                g.update(blk)
        got = g.hexdigest()
    check(got == want, f"stored file sha256 == source ({host})")
    # Run 1 (2026-11-22) counted <uid>.json + <uid>.result.json as leftovers: wrong, they are KEPT on purpose
    # (resumable.py: a re-sent finish gets the same answer; _sweep clears them after STALE_HOURS = 48). Only
    # byte-holding files (.part) or a .work folder for this upload would be real leftovers.
    stage = SSD / "unsorted" / ".atomic-staging"
    left = [p for p in stage.glob(f"{uid}*") if p.suffix == ".part" or p.name.endswith(".work")]
    left += list((SSD / "unsorted").rglob("*.part"))
    check(not left, f"no .part or .work left for this upload ({[str(p) for p in left][:3]})")
    st3, _ = call("POST", "/up/start", {"name": "t0932-after.bin", "size": size})
    check(st3 == 200, f"after finish the cap is free again: new 2 GB start {st3} (finished uploads do not count)")
    print("ALL PASS" if not bad else f"SOME FAILED ({bad})")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
