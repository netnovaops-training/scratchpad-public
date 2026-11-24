"""test_onion.py - proof for onion.py with a REAL Tor proxy. Run INSIDE the patched image on the compose network:

    docker run --rm --network nuc-homelab_default -e TOR_PROXY=socks5h://gluetun-tor:9050 --entrypoint python IMAGE test_onion.py

Checks: is_onion; a real onion page (the Tor Project's own onion) is fetched over Tor; a redirect to a clear-web
host is refused; TOR_PROXY empty refuses; a non-admin gets a refusal; the admin path files a readable PDF
(share._file and Gotenberg are faked here so the test never files anything live).
"""
import asyncio
import os
import pathlib
import sys
import tempfile

for k, v in {"INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test", "LLM_BASE": "http://x", "AUTH_LEVELS_URL": "http://x",
             "GOTENBERG_URL": "http://x"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402
import onion  # noqa: E402
import share  # noqa: E402

TPO = "http://2gzyxa5ihm7nsggfxnu52rck2vv4rvmdlkiu3zzui5du4xyclen53wid.onion/"   # torproject.org's onion
fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label)
    fails += not ok


check("is_onion", onion.is_onion(TPO) and not onion.is_onion("https://example.com/") and not onion.is_onion("ftp://x.onion/"))
tmp = pathlib.Path(tempfile.mkdtemp())
info = asyncio.run(onion.fetch(TPO, tmp / "p.html", 5 * 1048576))
check(f"real onion page over Tor ({info['content_type']}, {(tmp / 'p.html').stat().st_size} bytes)",
      info["status"] == 200 and "html" in info["content_type"] and (tmp / "p.html").stat().st_size > 1000)

real = onion.TOR_PROXY
onion.TOR_PROXY = ""
try:
    asyncio.run(onion.fetch(TPO, tmp / "x", 1000))
    check("no TOR_PROXY refuses", False)
except onion.Refused:
    check("no TOR_PROXY refuses", True)
onion.TOR_PROXY = real

app._person = lambda e: "casey"
row = asyncio.run(onion.handle(TPO, tmp, "z@example.invalid", []))
check("non-admin refused", row["status"] == "refused")

app._person = lambda e: "jason"
seen = {}


async def fake_file(p, name, email, details=None):
    seen["name"], seen["bytes"] = name, p.read_bytes()[:4]
    return {"name": name, "status": "filed"}


class FakeResp:
    content = b"%PDF-fake"

    def raise_for_status(self):
        pass


class FakeClient:
    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, files=None):
        seen["html_sent"] = files["index.html"][1][:200]
        return FakeResp()


share._file = fake_file
real_client = onion.httpx.AsyncClient
import httpx  # noqa: E402
onion_client = httpx.AsyncClient


def client_factory(*a, **k):
    return onion_client(*a, **k) if k.get("proxy") else FakeClient()


onion.httpx.AsyncClient = client_factory
tmp2 = pathlib.Path(tempfile.mkdtemp())
row = asyncio.run(onion.handle(TPO, tmp2, "j@example.invalid", []))
check(f"admin: page saved as a PDF of its readable text ({row})", row["status"] == "filed" and seen.get("bytes") == b"%PDF"
      and "onion site" in row["name"] and b"<script" not in seen.get("html_sent", b"<script"))
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
