#!/usr/bin/env python3
"""T0889 step 2: POST /share with a YouTube link goes to MeTube when METUBE_URL is set, else to the PDF path.

No network: _public_url, link_route.send_to_metube and _to_pdf are replaced with fakes that record calls.
Run inside the intake image: docker run --rm --network none -v $PWD:/t -w /t -e GOTENBERG_URL=http://x \
    --entrypoint python3 nuc-homelab-intake test_t0889_share.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import link_route  # noqa: E402
import share  # noqa: E402

FAILS, calls = [], []


def check(name, got, want):
    """Print PASS/FAIL for one comparison and remember failures."""
    ok = got == want
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"got {got!r} want {want!r}")
    if not ok:
        FAILS.append(name)


async def fake_metube(url, fmt, client=None):
    calls.append(("metube", url, fmt))
    return {"name": url, "status": "queued", "why": "fake"}


async def fake_pdf(url, text, title):
    calls.append(("pdf", url))
    raise share.httpx.ConnectError("fake printer")  # ends in the existing "could not be printed" row


share._public_url = lambda u: u.strip() or None
share._to_pdf = fake_pdf
link_route.send_to_metube = fake_metube
app = FastAPI()
app.include_router(share.router)
c = TestClient(app)
H = {"X-Auth-Email": "test@example.invalid", "Accept": "application/json"}
YT = "https://youtu.be/dQw4w9WgXcQ"

link_route.METUBE = ""  # off: today's behaviour
calls.clear()
r = c.post("/share", data={"url": YT}, headers=H)
check("off -> pdf path", calls, [("pdf", YT)])

link_route.METUBE = "http://metube.test"
calls.clear()
r = c.post("/share", data={"url": YT}, headers=H)
check("on -> metube mp4", calls, [("metube", YT, "mp4")])
check("on -> queued row", r.json()[0]["status"], "queued")

calls.clear()
c.post("/share", data={"text": f"song {YT}"}, headers=H)
check("link in text + 'song' -> mp3", calls, [("metube", YT, "mp3")])

calls.clear()
c.post("/share", data={"url": "https://music.youtube.com/watch?v=dQw4w9WgXcQ"}, headers=H)
check("music host -> mp3", calls[0][2] if calls else None, "mp3")

calls.clear()
c.post("/share", data={"url": "https://example.com/article"}, headers=H)
check("non-YouTube still pdf", calls, [("pdf", "https://example.com/article")])

print(f"{len(FAILS)} failed" if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
