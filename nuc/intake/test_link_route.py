#!/usr/bin/env python3
"""Tests for link_route.py (T0889 step 2): YouTube link detection, mp3/mp4 choice, MeTube body, send.

Run: python3 test_link_route.py   (no network; MeTube is faked with httpx.MockTransport)
"""
import asyncio
import importlib
import json
import os
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.pop("METUBE_URL", None)
import link_route as lr  # noqa: E402

FAILS = []


def check(name, got, want):
    """Record one comparison; print PASS/FAIL so the run log shows every case."""
    ok = got == want
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"got {got!r} want {want!r}")
    if not ok:
        FAILS.append(name)


V = "dQw4w9WgXcQ"  # an 11-character id
KIND = [
    (f"https://www.youtube.com/watch?v={V}", "video"),
    (f"https://m.youtube.com/watch?v={V}&t=30", "video"),
    (f"https://youtu.be/{V}?si=abc", "video"),
    (f"https://www.youtube.com/shorts/{V}", "video"),
    (f"https://youtube.com/live/{V}", "video"),
    ("https://www.youtube.com/playlist?list=PL123", "video"),
    (f"https://music.youtube.com/watch?v={V}&list=RD1", "music"),
    ("https://music.youtube.com/playlist?list=OLAK5", "music"),
    ("https://music.youtube.com/", None),
    ("https://www.youtube.com/about", None),
    ("https://www.youtube.com/watch?v=short", None),
    (f"https://youtube.com.evil.example/watch?v={V}", None),
    (f"https://evil.example/?u=https://youtube.com/watch?v={V}", None),
    (f"ftp://youtube.com/watch?v={V}", None),
    ("https://example.com/", None),
    ("", None),
    (None, None),
    ("http://[::1", None),  # bad URL must not raise
]
for url, want in KIND:
    check(f"kind {url!r}", lr.youtube_kind(url), want)

check("fmt music default", lr.pick_format(f"https://music.youtube.com/watch?v={V}"), "mp3")
check("fmt video default", lr.pick_format(f"https://youtu.be/{V}"), "mp4")
check("fmt word mp3 on video", lr.pick_format(f"https://youtu.be/{V}", "mp3 please"), "mp3")
check("fmt word video on music", lr.pick_format(f"https://music.youtube.com/watch?v={V}", "the video"), "mp4")
check("fmt both words -> host", lr.pick_format(f"https://youtu.be/{V}", "audio or video"), "mp4")
check("fmt word inside other word", lr.pick_format(f"https://youtu.be/{V}", "videogame"), "mp4")

a = lr.metube_payload("U", "mp3")
check("payload mp3", (a["download_type"], a["format"], a["quality"], a["folder"], a["url"]),
      ("audio", "mp3", "best", "music", "U"))
b = lr.metube_payload("U", "mp4")
check("payload mp4", (b["download_type"], b["format"], b["quality"], b["codec"], b["folder"]),
      ("video", "mp4", "best", "auto", "video"))
try:
    lr.metube_payload("U", "wav")
    check("payload bad fmt raises", False, True)
except ValueError:
    check("payload bad fmt raises", True, True)

check("off when METUBE_URL unset", lr.enabled(), False)
off = asyncio.run(lr.send_to_metube("U", "mp4"))
check("send when off", off["status"], "error")

os.environ["METUBE_URL"] = "http://metube.test:8081/"
lr = importlib.reload(lr)
check("on when set (slash stripped)", (lr.enabled(), lr.METUBE), (True, "http://metube.test:8081"))
seen = {}


def fake(answer, code=200):
    """MockTransport handler that records the request and answers like MeTube."""
    def handler(req):
        seen["url"], seen["body"] = str(req.url), json.loads(req.content)
        return httpx.Response(code, json=answer)
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


r = asyncio.run(lr.send_to_metube("U", "mp3", fake({"status": "ok"})))
check("send ok", r["status"], "queued")
check("send posts /add", seen["url"], "http://metube.test:8081/add")
check("send body", seen["body"]["download_type"], "audio")
r = asyncio.run(lr.send_to_metube("U", "mp4", fake({"status": "error", "msg": "Unsupported URL"})))
check("send refused (200 + error body)", (r["status"], r["detail"]), ("error", "Unsupported URL"))
r = asyncio.run(lr.send_to_metube("U", "mp4", fake({"x": 1}, 400)))
check("send http 400", r["status"], "error")


def boom(req):
    raise httpx.ConnectError("no route")


r = asyncio.run(lr.send_to_metube("U", "mp4", httpx.AsyncClient(transport=httpx.MockTransport(boom))))
check("send unreachable", r["status"], "error")

print(f"{len(FAILS)} failed" if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
