#!/usr/bin/env python3
"""test_part2.py - sandbox test for T0925 part 2 (A3 no freeze, shared key, A12 robin shelf, A14 health), 2026-11-19.

    python3 test_part2.py BASE KEY DATA_DIR RO_BASE

BASE: a sandbox started with INTAKE_KEY=KEY, LLM_BASE pointing at an address that never answers (so a text
file's model call blocks for INTAKE_LLM_TIMEOUT seconds) and DATA_DIR mounted as /srv.
RO_BASE: a sandbox whose /srv/unsorted is mounted read-only (healthz must say 503).
Talks only to sandboxes on 127.0.0.1 with scratch folders, never the live intake.

OUTCOME: see the T0925 Review line for the run on 2026-11-19.
"""
import json
import pathlib
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

ok_all = True
ME = "user@example.com"
ROBIN = "user@example.com"


def call(base, path, method="GET", headers=None, body=None, timeout=120):
    rq = urllib.request.Request(base + path, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(rq, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def check(name, cond):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)


def multipart(field, filename, data):
    b = uuid.uuid4().hex
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
    return body, f"multipart/form-data; boundary={b}"


def main():
    base, key, data_dir, ro_base = sys.argv[1], sys.argv[2], pathlib.Path(sys.argv[3]), sys.argv[4]
    good = {"X-Drop-Key": key}

    st, _ = call(base, "/healthz")
    check(f"no key -> 403 ({st})", st == 403)
    st, _ = call(base, "/healthz", headers={"X-Drop-Key": key + "x"})
    check(f"wrong key -> 403 ({st})", st == 403)
    st, body = call(base, "/healthz", headers=good)
    check(f"right key -> 200 and ok ({st})", st == 200 and json.loads(body)["ok"] is True)

    # A3: a text file makes the classifier call the (dead) model and wait. Meanwhile /healthz must answer fast.
    body, ctype = multipart("file", "slow-note.txt", b"A note long enough to need the model. " * 20)
    hdr = {**good, "X-Auth-Email": ME, "X-Auth-Level": "full", "Content-Type": ctype}
    done = {}
    t = threading.Thread(target=lambda: done.update(r=call(base, "/upload", "POST", hdr, body)))
    t0 = time.time()
    t.start()
    time.sleep(1.5)
    worst = 0.0
    while t.is_alive() and time.time() - t0 < 60:
        s = time.time()
        call(base, "/healthz", headers=good, timeout=30)
        worst = max(worst, time.time() - s)
        time.sleep(0.5)
    t.join()
    slow = time.time() - t0
    check(f"upload waited on the model ({slow:.1f}s) and was filed ({done['r'][0]})",
          slow > 5 and done["r"][0] == 200)
    check(f"other requests did not freeze meanwhile (worst {worst:.2f}s < 1s)", 0 < worst < 1.0)

    # A12: robin's book is his, not unsorted. The sandbox has no SERVICE_KEY, so the "read" grant check
    # answers "no access" and the book is HELD for robin (pending/robin/book); live, with his read:full
    # grant, the same verdict files it to /srv/books/robin. The shelf itself is checked by
    # `docker exec <sandbox> python -c "import classify; print(classify.SHELF['robin'])"`.
    body, ctype = multipart("file", "robin-test-book.epub", b"PK\x03\x04 not really an epub")
    st, res = call(base, "/upload", "POST", {**good, "X-Auth-Email": ROBIN, "X-Auth-Level": "full", "Content-Type": ctype}, body)
    got = json.loads(res) if st == 200 else {}
    path = got.get("path", "")
    check(f"robin's .epub is his book, not unsorted ({st} {path})",
          st == 200 and got.get("person") == "robin" and got.get("kind") == "book"
          and (path.startswith("/srv/books/robin/") or "/.pending/robin/book/" in path))

    # A10 on the share door: a file named '..' is filed, not a 500.
    body, ctype = multipart("files", "..", b"dotdot share")
    st, _ = call(base, "/share", "POST", {**good, "X-Auth-Email": ME, "X-Auth-Level": "full", "Content-Type": ctype,
                                          "Accept": "application/json"}, body)
    check(f"share of a file named '..' -> 200 ({st})", st == 200)

    # A8: nothing left behind in the temp folder on the /srv disk.
    tmp_left = [p for p in (data_dir / "unsorted" / ".tmp").rglob("*") if p.is_file()]
    check(f"no temp files left on /srv ({len(tmp_left)})", not tmp_left)

    # A14: a staging folder that cannot be written is unhealthy.
    st, body = call(ro_base, "/healthz", headers=good)
    check(f"read-only staging -> healthz 503 ({st})", st == 503)

    print("ALL PASS" if ok_all else "SOME FAILED")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
