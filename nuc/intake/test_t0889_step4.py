#!/usr/bin/env python3
"""T0889 step 4: direct file links (pdf / docx / images) are downloaded and filed; pages still go to PDF.

No network. Three parts:
1. link_route.file_kind / file_name on probe-shaped dicts.
2. link_probe.fetch against an httpx.MockTransport, DNS faked to a public IP (and to a private one).
3. POST /share with probe / fetch / _file / _to_pdf replaced by fakes that record calls.
Run inside the intake image: docker run --rm --network none -v $PWD:/t -w /t -e GOTENBERG_URL=http://x \
    --entrypoint python3 nuc-homelab-intake test_t0889_step4.py
"""
import asyncio
import os
import pathlib
import socket
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import link_probe  # noqa: E402
import link_route  # noqa: E402
import share  # noqa: E402

FAILS = []


def check(name, got, want):
    """Print PASS/FAIL for one comparison and remember failures."""
    ok = got == want
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"got {got!r} want {want!r}")
    if not ok:
        FAILS.append(name)


# ---- 1. file_kind / file_name -------------------------------------------------------------------------
fk = link_route.file_kind
check("pdf type", fk({"content_type": "application/pdf", "url": "https://x.test/a"}), ".pdf")
check("docx type", fk({"content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}),
      ".docx")
check("jpeg type", fk({"content_type": "image/jpeg"}), ".jpg")
check("html is not a file", fk({"content_type": "text/html", "url": "https://x.test/a.pdf"}), None)
check("svg is not taken", fk({"content_type": "image/svg+xml"}), None)
check("octet + .pdf name", fk({"content_type": "application/octet-stream", "filename": "r.PDF"}), ".pdf")
check("octet + url .png", fk({"content_type": "application/octet-stream", "url": "https://x.test/p/i.png?x=1"}),
      ".png")
check("octet + .exe refused", fk({"content_type": "application/octet-stream", "filename": "setup.exe"}), None)
check("empty type + .svg refused", fk({"content_type": "", "url": "https://x.test/a.svg"}), None)
fn = link_route.file_name
check("name from disposition", fn({"filename": "Report 2026.pdf", "url": "https://x.test/d"}, ".pdf"),
      "Report 2026.pdf")
check("name from url", fn({"url": "https://x.test/files/My%20Doc.pdf"}, ".pdf"), "My Doc.pdf")
check("php gets .pdf", fn({"url": "https://x.test/get.php"}, ".pdf"), "get.php.pdf")
check("jpeg keeps .jpeg", fn({"url": "https://x.test/a.jpeg"}, ".jpg"), "a.jpeg")
check("no name -> shared", fn({"url": "https://x.test/"}, ".png"), "shared.png")
check("odd chars cleaned", fn({"filename": "../../etc/pa;ss.pdf"}, ".pdf"), "etc_pa_ss.pdf")

# ---- 2. fetch ---------------------------------------------------------------------------------------
DNS = {"ip": "93.184.215.14"}
_real_gai = socket.getaddrinfo
socket.getaddrinfo = lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (DNS["ip"], port))]
seen = []
BODY = b"%PDF-1.4 fake" * 100


def handler(req):
    seen.append((str(req.url), req.headers.get("host")))
    if req.url.path == "/redir":
        return httpx.Response(302, headers={"location": "/elsewhere"})
    if req.url.path == "/missing":
        return httpx.Response(404)
    return httpx.Response(200, content=BODY, headers={"content-type": "application/pdf"})


async def run_fetch(url, max_bytes):
    """Call fetch with the mock transport into a temp file; return (result or exception name, file exists)."""
    d = pathlib.Path(tempfile.mkdtemp())
    dest = d / "f.pdf"
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        try:
            got = await link_probe.fetch(url, dest, max_bytes, client=c)
        except (link_probe.TooBig, link_probe.Refused) as e:
            got = type(e).__name__
    return got, dest.exists() and dest.stat().st_size


got, size = asyncio.run(run_fetch("https://files.test/a.pdf", 1 << 20))
check("fetch returns byte count", got, len(BODY))
check("fetch wrote the file", size, len(BODY))
check("fetch connected to the checked IP with real Host", seen[-1], ("https://93.184.215.14/a.pdf", "files.test"))
check("fetch over the cap -> TooBig, no part file", asyncio.run(run_fetch("https://files.test/a.pdf", 100)),
      ("TooBig", False))
check("fetch new redirect refused", asyncio.run(run_fetch("https://files.test/redir", 1 << 20))[0], "Refused")
check("fetch 404 refused", asyncio.run(run_fetch("https://files.test/missing", 1 << 20))[0], "Refused")
DNS["ip"] = "192.168.1.86"
n = len(seen)
check("fetch private DNS refused", asyncio.run(run_fetch("https://files.test/a.pdf", 1 << 20))[0], "Refused")
check("fetch private DNS made no request", len(seen), n)
socket.getaddrinfo = _real_gai

# ---- 3. POST /share ---------------------------------------------------------------------------------
calls = []
PROBE = {}


async def fake_probe(url, client=None):
    calls.append(("probe", url))
    r = PROBE.get(url)
    if isinstance(r, Exception):
        raise r
    return r


async def fake_fetch(url, dest, max_bytes, client=None):
    calls.append(("fetch", url, pathlib.Path(dest).name, max_bytes))
    if "big" in url:
        raise link_probe.TooBig("the file is bigger than 200 MB")
    if "broken" in url:
        raise link_probe.Refused("the download broke off (ReadError)")
    pathlib.Path(dest).write_bytes(b"data")
    return 4


async def fake_file(p, name, email, hint=None, shelf=None, details=None):
    calls.append(("file", name))
    p.unlink()
    return {"name": name, "status": "filed", "kind": "paperless"}


async def fake_pdf(url, text, title):
    calls.append(("pdf", url))
    raise share.httpx.ConnectError("fake printer")


kept = []
import resumable  # noqa: E402

resumable.keep_failed = lambda folder, email, name, results, dest=None: kept.append(
    sorted(p.name for p in folder.iterdir())) or True
share._public_url = lambda u: u.strip() or None
share._to_pdf = fake_pdf
share._pdf_text = lambda pdf: ""  # the fake PDF bytes are not a real PDF; bot-wall check sees no text
share._file = fake_file
link_probe.probe = fake_probe
link_probe.fetch = fake_fetch
link_route.METUBE = ""
app = FastAPI()
app.include_router(share.router)
c = TestClient(app)
H = {"X-Auth-Email": "test@example.invalid", "Accept": "application/json"}


def post(url, info):
    """Share one link with the given fake probe answer; return (calls, json rows)."""
    PROBE[url] = info
    calls.clear()
    kept.clear()
    return calls, c.post("/share", data={"url": url}, headers=H).json()


PDF = "https://files.test/a.pdf"
cl, rows = post(PDF, {"url": PDF, "status": 200, "content_type": "application/pdf", "filename": None})
check("pdf link -> probe, fetch, file", [x[0] for x in cl], ["probe", "fetch", "file"])
check("pdf link filed as a.pdf", cl[2], ("file", "a.pdf"))
check("pdf link cap is LINK_MAX_MB", cl[1][3], link_route.LINK_MAX_MB * 1048576)
check("pdf link row names the host", rows[0]["name"], "a.pdf (from files.test)")

IMG = "https://img.test/p/cat"
cl, rows = post(IMG, {"url": IMG + ".jpg", "status": 200, "content_type": "image/jpeg", "filename": None})
check("image link fetched from the final url", cl[1][1], IMG + ".jpg")
check("image filed as cat.jpg", cl[2], ("file", "cat.jpg"))

ART = "https://news.test/story"
cl, rows = post(ART, {"url": ART, "status": 200, "content_type": "text/html", "filename": None})
# step 5: the page is fetched for its readable text; the fake page ("data") is too short -> Chromium print
check("short page -> probe, fetch page, chromium pdf", [x[0] for x in cl], ["probe", "fetch", "pdf"])
check("page fetch cap is READ_MAX_MB", cl[1][3], share.READ_MAX_MB * 1048576)

# step 5: a long article -> our own plain HTML to Gotenberg convert/html, then filed as "<title>.pdf"
ARTICLE = ("<title>Big story</title><article><h1>Big story</h1><p>" + "Words of the article. " * 80
           + "</p><script>x()</script></article>").encode()
sent = []
_real_fetch = link_probe.fetch


async def fetch_article(url, dest, max_bytes, client=None):
    calls.append(("fetch", url, pathlib.Path(dest).name, max_bytes))
    pathlib.Path(dest).write_bytes(ARTICLE)
    return len(ARTICLE)


def gotenberg(req):
    sent.append((req.url.path, req.content))
    return httpx.Response(200, content=b"%PDF-1.4 readable")


_RealClient = httpx.AsyncClient
share.httpx.AsyncClient = lambda *a, **k: _RealClient(transport=httpx.MockTransport(gotenberg))
link_probe.fetch = fetch_article
LONG = "https://news.test/long"
cl, rows = post(LONG, {"url": LONG, "status": 200, "content_type": "text/html", "filename": None})
check("long article -> probe, fetch, file (no chromium url print)", [x[0] for x in cl], ["probe", "fetch", "file"])
check("long article filed under its title", cl[-1], ("file", "Big story.pdf"))
check("gotenberg got convert/html", sent[0][0] if sent else None, "/forms/chromium/convert/html")
check("no script reached gotenberg", b"x()" in (sent[0][1] if sent else b"x()"), False)
GEM = "https://gemini.google.com/share/abc123"
sent.clear()
cl, rows = post(GEM, {"url": GEM, "status": 200, "content_type": "text/html", "filename": None})
check("gemini link never read, chromium as before", ([x[0] for x in cl], sent), (["probe", "pdf"], []))
share.httpx.AsyncClient = _RealClient
link_probe.fetch = fake_fetch

DEAD = "https://dead.test/x"
cl, rows = post(DEAD, link_probe.Refused("the site did not answer (ConnectError)"))
check("no answer -> pdf path (never dropped)", [x[0] for x in cl], ["probe", "pdf"])

REB = "https://rebind.test/x"
cl, rows = post(REB, link_probe.Refused("only public http(s) links are fetched"))
check("rebinding -> refused, nothing fetched", ([x[0] for x in cl], rows[0]["status"]), (["probe"], "refused"))

BIG = "https://big.test/huge.pdf"
cl, rows = post(BIG, {"url": BIG, "status": 200, "content_type": "application/pdf", "filename": None})
check("too big -> refused row", rows[0]["status"], "refused")

BRK = "https://broken.test/a.pdf"
cl, rows = post(BRK, {"url": BRK, "status": 200, "content_type": "application/pdf", "filename": None})
check("broken download -> error row", rows[0]["status"], "error")
check("broken download keeps the link in .failed", kept, [["link-failed.url.txt"]])

NF = "https://files.test/gone.pdf"
cl, rows = post(NF, {"url": NF, "status": 404, "content_type": "application/pdf", "filename": None})
check("404 pdf -> pdf path, not fetch", [x[0] for x in cl], ["probe", "pdf"])

print(f"{len(FAILS)} failed" if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
