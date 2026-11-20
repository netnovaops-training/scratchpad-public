#!/usr/bin/env python3
"""test_gatekeeper.py - sandbox test for T0924 (A1 peer allowlist, A7 view-only, A9 video type), 2026-11-19.

    python3 test_gatekeeper.py BASE_ALLOWED BASE_BLOCKED

BASE_ALLOWED: a sandbox whose INTAKE_ALLOWED_PEERS includes this host's address as the container sees it.
BASE_BLOCKED: a sandbox with INTAKE_ALLOWED_PEERS=127.0.0.1 (the default), i.e. this host is a stranger.
Talks only to sandboxes on 127.0.0.1 with scratch folders, never the live intake.
"""
import os
import sys
import urllib.error
import urllib.request
import uuid

ok_all = True
# T0931 item 23 (2026-11-20): X-Drop-Key from env INTAKE_KEY when the sandbox has one (it must, item 7).
KEY = {"X-Drop-Key": os.environ["INTAKE_KEY"]} if os.environ.get("INTAKE_KEY") else {}


def call(base, path, method="GET", headers=None, body=None):
    rq = urllib.request.Request(base + path, data=body, method=method, headers={**KEY, **(headers or {})})
    try:
        with urllib.request.urlopen(rq, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code


def check(name, cond):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name)


def multipart(filename, data, fields):
    b = uuid.uuid4().hex
    parts = [f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode() for k, v in fields.items()]
    parts.append(f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                 f'Content-Type: application/octet-stream\r\n\r\n'.encode() + data + b"\r\n")
    return b"".join(parts) + f"--{b}--\r\n".encode(), {"Content-Type": f"multipart/form-data; boundary={b}"}


def main():
    allowed, blocked = sys.argv[1], sys.argv[2]
    me = {"X-Auth-Email": "user@example.com"}
    full = {**me, "X-Auth-Level": "full"}
    check("allowed peer: /healthz 200", call(allowed, "/healthz") == 200)
    check("stranger peer: /healthz 403", call(blocked, "/healthz") == 403)
    body, h = multipart("x.pdf", b"%PDF-1.4 test", {})
    check("stranger peer with a forged email: /upload 403",
          call(blocked, "/upload", "POST", {**h, **me}, body) == 403)
    check("no level on a write (item 8): /upload 403", call(allowed, "/upload", "POST", {**h, **me}, body) == 403)
    check("full level: /upload 200", call(allowed, "/upload", "POST", {**h, **full}, body) == 200)
    check("view-only level: /upload 403",
          call(allowed, "/upload", "POST", {**h, **me, "X-Auth-Level": "view"}, body) == 403)
    check("view-only level: GET / still 200", call(allowed, "/", headers={**me, "X-Auth-Level": "view"}) == 200)
    body, h = multipart("page.html", b"<script>alert(1)</script>", {"hint": "video", "shelf": "general"})
    check("hint=video with .html: 415", call(allowed, "/upload", "POST", {**h, **me, "X-Auth-Level": "full"}, body) == 415)
    print("ALL PASS" if ok_all else "SOME FAILED")
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
