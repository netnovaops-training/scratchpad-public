#!/usr/bin/env python3
"""Tests for link_probe.py (T0889 step 3). Offline: DNS and the web are faked.

Run: python3 test_link_probe.py          offline cases only
     python3 test_link_probe.py --live   also probes 3 real public links (needs network)
"""
import asyncio
import os
import socket
import sys

import httpx

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import link_probe as lp  # noqa: E402

FAILS = []


def check(name, got, want):
    """Print PASS/FAIL for one comparison and remember failures."""
    ok = got == want
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"got {got!r} want {want!r}")
    if not ok:
        FAILS.append(name)


DNS = {"pub.test": ["93.184.215.14"], "lan.test": ["192.168.1.86"], "ts.test": ["100.64.0.10"],
       "mixed.test": ["93.184.215.14", "127.0.0.1"], "v6.test": ["2606:2800:21f:cb07:6820:80da:af6b:8b2c"]}
real_gai = socket.getaddrinfo


def fake_gai(host, port, *a, **k):
    """Answer from DNS above; an IP literal answers itself (like the real resolver); unknown names fail."""
    if host.replace(".", "").isdigit() or ":" in host:
        DNS.setdefault(host, [host])
    if host not in DNS:
        raise socket.gaierror("no such name")
    return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
            for ip in DNS[host]]


def run(url, handler):
    """probe(url) against a fake web; returns the result dict or 'Refused: <why>'."""
    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        return asyncio.run(lp.probe(url, c))
    except lp.Refused as e:
        return f"Refused: {e}"


seen = []


def web(req):
    """Fake sites keyed by the Host header (the URL holds the pinned IP)."""
    seen.append((req.method, str(req.url), req.headers.get("host"), req.extensions.get("sni_hostname")))
    path = req.url.path
    if path == "/file.pdf":
        return httpx.Response(200, headers={"content-type": "application/pdf", "content-length": "5000",
                                            "content-disposition": "attachment; filename*=UTF-8''a%20b.pdf"})
    if path == "/nohead":
        if req.method == "HEAD":
            return httpx.Response(405)
        return httpx.Response(206, headers={"content-type": "video/mp4", "content-range": "bytes 0-0/999"})
    if path == "/to-lan":
        return httpx.Response(302, headers={"location": "http://lan.test/admin"})
    if path == "/to-pdf":
        return httpx.Response(301, headers={"location": "/file.pdf"})
    if path == "/loop":
        return httpx.Response(302, headers={"location": "/loop"})
    return httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"})


socket.getaddrinfo = fake_gai
r = run("https://pub.test/file.pdf", web)
check("pdf type", r["content_type"], "application/pdf")
check("pdf length", r["length"], 5000)
check("pdf filename*", r["filename"], "a b.pdf")
check("pinned to checked IP, Host + SNI = name", seen[-1],
      ("HEAD", "https://93.184.215.14/file.pdf", "pub.test", "pub.test"))
r = run("http://pub.test:8080/page", web)
check("http with port: no SNI, Host keeps port", seen[-1][1:], ("http://93.184.215.14:8080/page", "pub.test:8080", None))
check("html type", r["content_type"], "text/html")
r = run("https://pub.test/nohead", web)
check("405 -> ranged GET", (seen[-1][0], r["content_type"], r["length"]), ("GET", "video/mp4", 999))
r = run("https://pub.test/to-pdf", web)
check("redirect followed + rechecked", (r["content_type"], r["hops"], r["url"]),
      ("application/pdf", 1, "https://pub.test/file.pdf"))
check("redirect to LAN refused", run("https://pub.test/to-lan", web), "Refused: only public http(s) links are fetched")
check("too many redirects", run("https://pub.test/loop", web), f"Refused: more than {lp.MAX_HOPS} redirects")
check("LAN refused", run("http://lan.test/", web), "Refused: only public http(s) links are fetched")
check("Tailscale refused", run("http://ts.test/", web), "Refused: only public http(s) links are fetched")
check("one private record refuses all", run("http://mixed.test/", web), "Refused: only public http(s) links are fetched")
check("literal 127.0.0.1 refused", run("http://127.0.0.1/", web), "Refused: only public http(s) links are fetched")
check("ftp refused", run("ftp://pub.test/x", web), "Refused: only public http(s) links are fetched")
check("unknown name", run("http://nope.test/", web), "Refused: the site name 'nope.test' does not resolve")
run("https://v6.test/x", web)
check("IPv6 pinned in brackets", seen[-1][1], "https://[2606:2800:21f:cb07:6820:80da:af6b:8b2c]/x")


def down(req):
    raise httpx.ConnectTimeout("slow")


check("no answer", run("https://pub.test/", down), "Refused: the site did not answer (ConnectTimeout)")
socket.getaddrinfo = real_gai

if "--live" in sys.argv:
    for url in ("https://example.com/",
                "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf",
                "https://youtu.be/dQw4w9WgXcQ"):
        try:
            r = asyncio.run(lp.probe(url))
            print("LIVE", url, "->", r["status"], r["content_type"], r["length"], "hops", r["hops"])
        except lp.Refused as e:
            print("LIVE", url, "-> Refused:", e)
            FAILS.append("live " + url)

print(f"{len(FAILS)} failed" if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
