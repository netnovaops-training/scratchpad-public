"""test_harden.py - step 7 (2026-11-24): the whole drop site under one strict CSP, and the audit fixes. Self-contained:
run INSIDE the patched image (SWAP-PATCH.sh does exactly this):

    docker run --rm --entrypoint python IMAGE test_harden.py

Checks:
  1 every route of the app (walked from app.routes, path parameters filled in) answers with a Content-Security-Policy
    that has script-src 'self', no 'unsafe-inline'/'unsafe-eval', object-src 'none', base-uri 'none' and
    frame-ancestors 'none'; plus nosniff, CORP same-origin and a COOP (same-origin, except the keyring: unsafe-none,
    and the share picker: same-origin-allow-popups, both documented). Also the gatekeeper's own 403.
  2 every HTML page (/, /sending, /routes, /devices, /relay/send, /relay/inbox, /relay/share, /keyring and share.py's
    result page) has no inline <script>, no <style>, no style= attribute, no on...= handler and no javascript: URL;
    every <script> and stylesheet it loads is same-origin with an integrity hash that matches the served bytes.
  3 the theme pictures lost their <style>/<script> to /assets/sending.css and sending.js (horizon theme).
  4 audit 8a: the E2E routes refuse a service peer (even if listed as edge) and any peer that is not the edge.
  5 audit 24: a piece request for a transfer that is not yours never enters relay.using().
Last line: "ALL PASS" or "SOME FAILED".
"""
import base64
import contextlib
import hashlib
import os
import pathlib
import re
import sys
import tempfile

T = pathlib.Path(tempfile.mkdtemp(prefix="hardtest-"))
(T / "drop.toml").write_text("""[brand]
name = "Novashare"
theme = "horizon"
[[person]]
id = "jason"
name = "Jason"
emails = ["j@example.invalid"]
[[person]]
id = "casey"
name = "Casey"
emails = ["z@example.invalid"]
""")
os.environ.update(DROP_CONFIG=str(T / "drop.toml"), INTAKE_KEY="k-test", INTAKE_TOKEN="t-test",
                  INTAKE_ALLOWED_PEERS="127.0.0.1,10.9.9.9,192.168.1.100", INTAKE_SERVICE_PEERS="192.168.1.100",
                  INTAKE_EDGE_PEERS="127.0.0.1,10.9.9.9,192.168.1.100",
                  INTAKE_STAGE=str(T / "stage"), INTAKE_UNSORTED=str(T / "unsorted"), INTAKE_LOG=str(T / "intake.log"),
                  INTAKE_DEVICES_DB=str(T / "devices.db"), INTAKE_RELAY_DIR=str(T / "relay"),
                  INTAKE_ABYSS_ORIGIN="https://abyss.example.invalid")
for k in ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL"):
    os.environ.setdefault(k, "http://10.255.255.1")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    fails += not ok


import app  # noqa: E402
import relay  # noqa: E402
import share  # noqa: E402
import site_assets  # noqa: E402
import themes  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

C = TestClient(app.app, client=("127.0.0.1", 50000), follow_redirects=False)
J = {"X-Auth-Email": "j@example.invalid", "X-Auth-Level": "full", "Sec-Fetch-Site": "same-origin", "X-Novashare": "1"}


def csp_ok(h) -> str:
    """'' if the response headers are strict, else the reason."""
    c = h.get("content-security-policy") or ""
    need = ["script-src 'self'", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'"]
    if not c:
        return "no CSP"
    for n in need:
        if n not in c and not (n == "object-src 'none'" and "default-src 'none'" in c and "object-src" not in c):
            return "missing " + n
    if "unsafe-inline" in c or "unsafe-eval" in c or "*" in c.replace("'*'", ""):
        return "unsafe source"
    if h.get("x-content-type-options") != "nosniff":
        return "no nosniff"
    if h.get("cross-origin-resource-policy") != "same-origin":
        return "no CORP"
    if not h.get("cross-origin-opener-policy"):
        return "no COOP"
    return ""


# --- 1. every route
FILL = {"name": "devices.js", "device_id": "A" * 22, "other": "casey", "tid": "B" * 22, "n": "0", "uid": "C" * 22}
seen, bad = 0, []
for r in app.app.routes:
    if not isinstance(r, APIRoute):
        continue
    path = re.sub(r"\{(\w+)(:[^}]*)?\}", lambda m: FILL.get(m.group(1), "x"), r.path)
    for method in sorted(r.methods - {"HEAD"}):
        if path in ("/intake", "/upload", "/share") and method == "POST":
            resp = C.request(method, path, headers=J, data={})      # empty form: refused fast, headers still there
        else:
            resp = C.request(method, path, headers=J)
        seen += 1
        why = csp_ok(resp.headers)
        if why:
            bad.append(f"{method} {path} ({resp.status_code}): {why}")
check(f"every route ({seen} method+path pairs) answers with the strict header set", not bad)
for b in bad[:10]:
    print("    " + b)
g = TestClient(app.app, client=("10.66.66.66", 5)).get("/devices")
check("the gatekeeper's own 403 (unknown peer) carries the strict CSP too", g.status_code == 403 and not csp_ok(g.headers))
coop = {p: C.get(p, headers=J).headers.get("cross-origin-opener-policy") for p in ("/", "/devices", "/relay/share", "/keyring")}
check("COOP: same-origin everywhere, keyring unsafe-none (Abyss keeps its popup handle), picker allow-popups",
      coop == {"/": "same-origin", "/devices": "same-origin", "/relay/share": "same-origin-allow-popups", "/keyring": "unsafe-none"})
check("site CSP has object-src 'none', base-uri 'none', frame-ancestors 'none', no unsafe-inline",
      all(x in site_assets.SITE_CSP for x in ("object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'"))
      and "unsafe" not in site_assets.SITE_CSP)

# --- 2. no inline script/style on any page; every subresource same-origin with a matching SRI hash
PAGES = {p: C.get(p, headers=J) for p in ("/", "/sending", "/routes", "/devices", "/relay/send", "/relay/inbox",
                                          "/relay/share", "/keyring")}
PAGES["share result page"] = None
share_html = share._page([{"name": "a.pdf", "status": "filed", "kind": "document"}])


def sri_of(b: bytes, alg: str) -> str:
    return alg + "-" + base64.b64encode(hashlib.new(alg, b).digest()).decode()


for name, resp in PAGES.items():
    html = share_html if resp is None else resp.text
    ok_status = resp is None or resp.status_code == 200
    problems = []
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, flags=re.S | re.I):
        if "src=" not in m.group(1) or m.group(2).strip():
            problems.append("inline script")
    if re.search(r"<style\b", html, flags=re.I):
        problems.append("<style>")
    if re.search(r"\sstyle\s*=", html, flags=re.I):
        problems.append("style= attribute")
    if re.search(r"<[a-z][^>]*\son[a-z]+\s*=", html, flags=re.I):
        problems.append("on...= handler")
    if "javascript:" in html.lower():
        problems.append("javascript: URL")
    for m in re.finditer(r'<(?:script|link)\b[^>]*?(?:src|href)="?([^" >]+)"?[^>]*?integrity="([^"]+)"', html):
        url, want = m.group(1), m.group(2)
        if url.startswith("http") or url.startswith("//"):
            problems.append("third-party " + url)
            continue
        body = C.get(url if url.startswith("/") else "/" + url, headers=J).content
        if sri_of(body, want.split("-")[0]) != want:
            problems.append("SRI mismatch " + url)
    for m in re.finditer(r'<(script)\b[^>]*\bsrc="?([^" >]+)', html):
        if "integrity=" not in m.group(0) and not m.group(0).endswith(">"):
            pass
    no_sri = [m.group(1) for m in re.finditer(r'<script\b[^>]*\bsrc="?([^" >]+)"?(?![^>]*integrity)[^>]*>', html)]
    if no_sri:
        problems.append("script without integrity " + ",".join(no_sri))
    check(f"page {name}: 200, no inline script/style/handler, same-origin scripts with matching SRI", ok_status and not problems)
    for p in problems[:5]:
        print("    " + p)

# --- 3. the theme picture's style and script moved to files
svg, css, js = themes.svg_parts(themes.current()["svg"])
send_css = C.get("/assets/sending.css", headers=J)
send_js = C.get("/assets/sending.js", headers=J)
check("horizon picture: its <style> and <script> are split off (both non-empty)", css and js and "<script" not in svg)
check("/assets/sending.css carries the picture's style; /assets/sending.js its script",
      css.splitlines()[0] in send_css.text and js.splitlines()[0] in send_js.text and send_css.headers["content-type"].startswith("text/css"))
check("/assets/nothing.js -> 404", C.get("/assets/nothing.js", headers=J).status_code == 404)

# --- 4. audit 8a: identity headers are only believed from the edge on the E2E routes
K = {**J, "X-Drop-Key": "k-test"}
svc = TestClient(app.app, client=("192.168.1.100", 5)).get("/devices/api/mine", headers=K)
check("a service peer (listed as edge too) with the drop key + identity headers -> 403 not_edge",
      svc.status_code == 403 and svc.json().get("code") == "not_edge")
edge = TestClient(app.app, client=("10.9.9.9", 5)).get("/devices/api/mine", headers=K)
check("an edge peer with the drop key -> 200", edge.status_code == 200)
os.environ["INTAKE_EDGE_PEERS"] = ""
import devices  # noqa: E402

devices.EDGE_PEERS.discard("10.9.9.9")
off = TestClient(app.app, client=("10.9.9.9", 5)).get("/relay/inbox", headers=K)
check("an allowed peer that is not the edge -> 403 on an E2E page", off.status_code == 403)
devices.EDGE_PEERS.add("10.9.9.9")
lib = TestClient(app.app, client=("192.168.1.100", 5)).get("/healthz", headers=K)
check("the service peer still reaches non-E2E routes (healthz)", lib.status_code in (200, 503))

# --- 5. audit 24: using() only after the access check
entered = []
orig = relay.using


@contextlib.contextmanager
def spy(tid):
    entered.append(tid)
    with orig(tid):
        yield


relay.using = spy
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402


def mlkem_pub():
    import random
    co = [random.randrange(3329) for _ in range(768)]
    out = bytearray()
    for i in range(0, 768, 2):
        a, b = co[i], co[i + 1]
        out += bytes([a & 0xFF, (a >> 8) | ((b & 0x0F) << 4), b >> 4])
    return bytes(out) + os.urandom(32)


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


xp = X25519PrivateKey.generate().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
zd = C.post("/devices/api/register", headers={**J, "X-Auth-Email": "z@example.invalid"},
            json={"label": "Z", "x25519_pub": b64u(xp), "mlkem_pub": b64u(mlkem_pub())}).json()["device_id"]
r = C.get("/relay/api/transfers/" + "D" * 22 + "/pieces/0", headers={**J, "X-Auth-Email": "z@example.invalid", "X-Device-Id": zd})
check("piece of a transfer that is not yours -> 404, and relay.using() was never entered",
      r.status_code == 404 and entered == [])
relay.using = orig

print("ALL PASS" if not fails else "SOME FAILED")
