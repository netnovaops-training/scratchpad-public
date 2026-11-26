"""test_devices.py - API proof for devices.py (Novashare E2E Step 1, 2026-11-24). Self-contained: run INSIDE the
patched image (SWAP-PATCH.sh does exactly this):

    docker run --rm --entrypoint python IMAGE test_devices.py

It writes its own drop.toml (fake people, example.invalid addresses) and uses a temp folder for the DB and log,
so it needs no mount. Checks: the request guard (Sec-Fetch-Site, X-Novashare, level, unknown sign-ins), register /
retry / re-key, someone else's device can be neither re-keyed nor revoked, revoked keys leave the directory, the
safety number is symmetric, stable and changes on a key change, key checks (small-order X25519, FIPS 203 ML-KEM
modulus), schema triggers, device limit, rate limit, the vendored bundle's hash, the page's CSP and SRI, no emails
in the log, and that importing app opens no DB. Last line: "ALL PASS" or "SOME FAILED".
Variables: fails, check(), H(), kp() (a random valid key pair encoding), T (temp dir).
"""
import base64
import hashlib
import os
import pathlib
import random
import re
import sqlite3
import subprocess
import sys
import tempfile

T = pathlib.Path(tempfile.mkdtemp(prefix="devtest-"))
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
[[person]]
id = "mom"
name = "Mom"
emails = ["m@example.invalid"]
[[person]]
id = "kid"
name = "Kid"
""")
os.environ.update(DROP_CONFIG=str(T / "drop.toml"), INTAKE_KEY="k-test", INTAKE_TOKEN="t-test",
                  INTAKE_ALLOWED_PEERS="127.0.0.1", INTAKE_STAGE=str(T / "stage"), INTAKE_UNSORTED=str(T / "unsorted"),
                  INTAKE_LOG=str(T / "intake.log"), INTAKE_DEVICES_DB=str(T / "devices.db"))
for k in ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL"):
    os.environ.setdefault(k, "http://10.255.255.1")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    fails += not ok


# --- 0. importing app must not open or create the devices DB (rescan_held imports app every hour)
probe = T / "probe.db"
r = subprocess.run([sys.executable, "-c", "import app"], env={**os.environ, "INTAKE_DEVICES_DB": str(probe)},
                   capture_output=True, text=True, cwd=str(pathlib.Path(__file__).resolve().parent))
check("importing app (a second process) creates no devices DB", r.returncode == 0 and not probe.exists())

import app  # noqa: E402
import devices  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

C = TestClient(app.app, client=("127.0.0.1", 50000))
EMAIL = {"jason": "j@example.invalid", "casey": "z@example.invalid", "mom": "m@example.invalid"}


def H(who="jason", **over):
    h = {"X-Auth-Email": EMAIL.get(who, who), "X-Auth-Level": "full", "Sec-Fetch-Site": "same-origin", "X-Novashare": "1"}
    for k, v in over.items():
        k = k.replace("_", "-")
        if v is None:
            h.pop(k, None)
        else:
            h[k] = v
    return h


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def mlkem_pub(bad=False):
    """A random ML-KEM-768 encapsulation key that passes the FIPS 203 modulus check (or fails it, bad=True)."""
    co = [random.randrange(3329) for _ in range(768)]
    if bad:
        co[17] = 4095
    out = bytearray()
    for i in range(0, 768, 2):
        a, b = co[i], co[i + 1]
        out += bytes([a & 0xFF, (a >> 8) | ((b & 0x0F) << 4), b >> 4])
    return bytes(out) + os.urandom(32)


def kp():
    return {"x25519_pub": b64u(os.urandom(32)), "mlkem_pub": b64u(mlkem_pub())}


def reg(who="jason", label="Phone", **extra):
    return C.post("/devices/api/register", headers=H(who), json={"label": label, "platform": "Android", **kp(), **extra})


def people(who="jason"):
    return {p["id"]: p for p in C.get("/devices/api/people", headers=H(who)).json()["people"]}


# --- 1. the guard
cases = {"no Sec-Fetch-Site": H(Sec_Fetch_Site=None), "cross-site": H(Sec_Fetch_Site="cross-site"),
         "same-site (a sibling subdomain)": H(Sec_Fetch_Site="same-site"), "none": H(Sec_Fetch_Site="none"),
         "no X-Novashare": H(X_Novashare=None), "view level": H(X_Auth_Level="view"), "no level": H(X_Auth_Level=None),
         "unmapped email": H("stranger@example.invalid"), "no email": H(X_Auth_Email=None)}
for name, h in cases.items():
    g = C.get("/devices/api/mine", headers=h)
    p = C.post("/devices/api/register", headers=h, json={"label": "x", **kp()})
    check(f"guard: {name} -> 403 on read and write ({g.status_code}, {p.status_code})", g.status_code == 403 and p.status_code == 403)
g = C.get("/devices/api/mine", headers=H(X_Drop_Key="k-test", X_Auth_Level=None, X_Auth_Email=None))
check("guard: the drop key alone (a service caller) is not a person -> 403", g.status_code == 403)

# --- 2. register, retry, fingerprint
body = {"label": "Jason phone", "platform": "Android", **kp()}
r1 = C.post("/devices/api/register", headers=H(), json=body)
d1 = r1.json().get("device_id", "")
check("register -> 201 with a 22-character id", r1.status_code == 201 and len(d1) == 22)
fp = devices.device_fp("jason", d1, base64.urlsafe_b64decode(body["x25519_pub"] + "="),
                       base64.urlsafe_b64decode(body["mlkem_pub"] + "=" * (-len(body["mlkem_pub"]) % 4)))
check("fingerprint = SHA-256 over the length-prefixed record", r1.json().get("fingerprint") == fp)
r2 = C.post("/devices/api/register", headers=H(), json=body)
check("the same keys again -> 200, the same device (a retry adds nothing)", r2.status_code == 200 and r2.json()["device_id"] == d1)
check("registration response says no key change", r2.json()["key_changed"] is False)

# --- 3. input and key checks
bad = {
    "short X25519": {"x25519_pub": b64u(b"\x01" * 31)},
    "all-zero X25519 (small order)": {"x25519_pub": b64u(bytes(32))},
    "X25519 = 1 with the top bit set (small order)": {"x25519_pub": b64u(b"\x01" + bytes(30) + b"\x80")},
    "ML-KEM coefficient >= q": {"mlkem_pub": b64u(mlkem_pub(bad=True))},
    "ML-KEM 1183 bytes": {"mlkem_pub": b64u(mlkem_pub()[:-1])},
    "padded base64": {"x25519_pub": b64u(os.urandom(32)) + "="},
    "standard base64 (+/)": {"x25519_pub": base64.b64encode(b"\xfb" * 32).decode().rstrip("=")},
}
for name, over in bad.items():
    r = C.post("/devices/api/register", headers=H(), json={"label": "x", **kp(), **over})
    check(f"refused: {name} -> 400 bad_key", r.status_code == 400 and r.json().get("code") == "bad_key")
for name, lab in {"empty label": "  ", "41-character label": "x" * 41, "label not text": 7}.items():
    r = C.post("/devices/api/register", headers=H(), json={"label": lab, **kp()})
    check(f"refused: {name} -> 400", r.status_code == 400)
r = C.post("/devices/api/register", headers={**H(), "Content-Type": "text/plain"}, content=b"{}")
check("refused: not JSON -> 415", r.status_code == 415)
r = C.post("/devices/api/register", headers={**H(), "Content-Type": "application/json"}, content=b"[" * 20000)
check("refused: a 20 KB body -> 413", r.status_code == 413)
r = C.post("/devices/api/register", headers=H(), json=["not", "an", "object"])
check("refused: JSON that is not an object -> 400", r.status_code == 400)

# --- 4. someone else's device can be neither re-keyed (registered over) nor revoked
z1 = reg("casey", "Casey phone").json()["device_id"]
r = C.post("/devices/api/register", headers=H("casey"), json={"label": "hijack", **kp(), "device_id": d1})
check("casey re-keying jason's device id -> 404 not_found (existence not confirmed)", r.status_code == 404 and r.json()["code"] == "not_found")
r = C.post("/devices/api/register", headers=H("casey"), json={"label": "x", **kp(), "device_id": "A" * 22})
check("a device id that does not exist -> the same 404", r.status_code == 404 and r.json()["code"] == "not_found")
r = C.post(f"/devices/api/mine/{d1}/revoke", headers=H("casey"))
check("casey revoking jason's device -> 404", r.status_code == 404)
pj = people("casey")["jason"]
check("jason's device is untouched after both attempts", [d["fingerprint"] for d in pj["devices"]] == [fp])
mine = C.get("/devices/api/mine", headers=H("casey")).json()
check("list my devices: casey sees only casey's", [d["id"] for d in mine["devices"]] == [z1] and mine["person"] == "casey")

# --- 5. directory and safety numbers
pp = people("jason")
check("directory lists household people only (jason, mom, casey; not kid, who cannot sign in)", sorted(pp) == ["jason", "mom", "casey"])
check("directory gives casey's public keys", len(pp["casey"]["devices"]) == 1 and len(pp["casey"]["devices"][0]["mlkem_pub"]) == 1579)
check("no safety number with someone who has no device (mom)", pp["mom"]["safety_number"] is None)
sn_j = pp["casey"]["safety_number"]
sn_z = people("casey")["jason"]["safety_number"]
check("safety number: 12 digits in 3 groups", bool(re.fullmatch(r"\d{4} \d{4} \d{4}", sn_j or "")))
check("safety number symmetric (jason->casey == casey->jason)", sn_j == sn_z)
check("safety number stable (asked again)", people("jason")["casey"]["safety_number"] == sn_j)
check("safety number = devices.safety_number(both person fingerprints), either order",
      devices.safety_number("jason", pp["jason"]["fingerprint"], "casey", pp["casey"]["fingerprint"]) == sn_j
      == devices.safety_number("casey", pp["casey"]["fingerprint"], "jason", pp["jason"]["fingerprint"]))
check("own entry has no safety number", pp["jason"]["safety_number"] is None and pp["jason"]["me"] is True)
check("one person: /people/casey", C.get("/devices/api/people/casey", headers=H()).json()["people"][0]["id"] == "casey")
check("one person: kid (no sign-in) -> 404", C.get("/devices/api/people/kid", headers=H()).status_code == 404)
check("one person: unknown -> 404", C.get("/devices/api/people/nobody", headers=H()).status_code == 404)

# --- 6. a key change on an existing device id: allowed for the owner, logged, number changes
r = C.post("/devices/api/register", headers=H(), json={"label": "Jason phone", **kp(), "device_id": d1})
check("owner re-keys own device -> 200 key_changed, same id", r.status_code == 200 and r.json()["key_changed"] is True and r.json()["device_id"] == d1)
new_fp = r.json()["fingerprint"]
db = sqlite3.connect(os.environ["INTAKE_DEVICES_DB"])
ev = db.execute("SELECT old_fp, new_fp FROM key_event WHERE kind='key_changed' AND device_id=?", (d1,)).fetchall()
check("key_event row with old and new fingerprint", ev == [(fp, new_fp)])
log = pathlib.Path(os.environ["INTAKE_LOG"]).read_text()
check("server log has the key change (device + fingerprint prefixes)", f"KEY CHANGE d={d1[:6]} old={fp[:8]} new={new_fp[:8]}" in log)
check("safety number changed after the key change", people("casey")["jason"]["safety_number"] != sn_z)

# --- 7. revoke: only mine, repeatable, revoked keys leave the directory, a revoked id cannot be re-keyed
r = C.post(f"/devices/api/mine/{d1}/revoke", headers=H())
check("revoke my device -> 200 revoked", r.status_code == 200 and r.json()["state"] == "revoked")
check("revoke again -> 200 (idempotent)", C.post(f"/devices/api/mine/{d1}/revoke", headers=H()).status_code == 200)
check("bad id shape -> 404", C.post("/devices/api/mine/..%2F..%2Fx/revoke", headers=H()).status_code == 404)
pz = people("casey")
check("revoked device excluded from the directory", pz["jason"]["devices"] == [] and pz["jason"]["fingerprint"] is None)
check("no safety number once jason has no active device", pz["jason"]["safety_number"] is None)
m = C.get("/devices/api/mine", headers=H()).json()["devices"]
check("my own list still shows the revoked device, marked", len(m) == 1 and m[0]["revoked"] is not None)
r = C.post("/devices/api/register", headers=H(), json={"label": "x", **kp(), "device_id": d1})
check("re-keying a revoked device -> 409 revoked", r.status_code == 409 and r.json()["code"] == "revoked")
check("revocation logged", f"devices revoked d={d1[:6]}" in pathlib.Path(os.environ["INTAKE_LOG"]).read_text())

# --- 8. schema triggers (defence below the code)
for name, sql in {"un-revoke": "UPDATE device SET revoked=NULL WHERE id=?",
                  "change person": "UPDATE device SET person='casey' WHERE id=?",
                  "change a revoked device's key": "UPDATE device SET x25519_pub=randomblob(32) WHERE id=?"}.items():
    try:
        db.execute(sql, (d1,))
        db.commit()
        check(f"trigger refuses: {name}", False)
    except sqlite3.DatabaseError:
        db.rollback()                    # end the test's own transaction, or it holds the write lock
        check(f"trigger refuses: {name}", True)
try:
    db.execute("INSERT INTO device(id,person,label,x25519_pub,mlkem_pub,fingerprint,created,last_seen) VALUES(?,?,?,?,?,?,?,?)",
               ("B" * 22, "casey", "x", os.urandom(31), os.urandom(1184), "0" * 64, 1, 1))
    check("CHECK refuses a 31-byte X25519 key in the table itself", False)
except sqlite3.DatabaseError:
    db.rollback()
    check("CHECK refuses a 31-byte X25519 key in the table itself", True)
db.close()

# --- 9. device limit and rate limit
codes = [reg("mom", f"Mom {i}").status_code for i in range(devices.MAX_LIVE)]
r = reg("mom", "one too many")
check(f"{devices.MAX_LIVE} devices fine, the next -> 409 device_limit", codes == [201] * devices.MAX_LIVE and r.status_code == 409 and r.json()["code"] == "device_limit")
devices.REG_PER_HOUR = len(devices._RATE["mom"])
r = reg("mom", "rate")
check("over the hourly registration budget -> 429 rate_limited", r.status_code == 429 and r.json()["code"] == "rate_limited")
devices.REG_PER_HOUR = 20

# --- 10. the page, its files, the vendored bundle
pg = C.get("/devices", headers=H(Sec_Fetch_Site="none", X_Novashare=None))
csp = pg.headers.get("content-security-policy", "")
check("page: 200 for a signed-in person on a fresh navigation", pg.status_code == 200)
check("page: strict CSP (script-src 'self', no unsafe-inline, Trusted Types, no framing)",
      "script-src 'self'" in csp and "unsafe" not in csp and "require-trusted-types-for 'script'" in csp and "frame-ancestors 'none'" in csp)
check("page: no inline script and no inline handlers", not re.search(r"<script(?![^>]*\bsrc=)", pg.text) and not re.search(r"\son[a-z]+=", pg.text))
check("page: black/purple/orange tokens (horizon)", "#07050d" in devices.CSS.decode() and "#b388ff" in devices.CSS.decode() and "#ff8a3d" in devices.CSS.decode())
check("page: unmapped sign-in -> 403", C.get("/devices", headers=H("stranger@example.invalid")).status_code == 403)
check("page: view-only -> 403", C.get("/devices", headers=H(X_Auth_Level="view")).status_code == 403)
for src, sri in re.findall(r'(?:src|href)="(/devices/static/[^"]+)" integrity="([^"]+)"', pg.text):
    b = C.get(src, headers=H(Sec_Fetch_Site="same-origin", X_Novashare=None)).content
    check(f"SRI matches the served bytes: {src.rsplit('/', 1)[1]}", sri == "sha384-" + base64.b64encode(hashlib.sha384(b).digest()).decode())
v = C.get(f"/devices/static/{devices.vendor_noble_pq.NAME}", headers=H(X_Novashare=None))
check("vendored ML-KEM bundle: sha256 equals the recorded value", hashlib.sha256(v.content).hexdigest() == devices.vendor_noble_pq.SHA256
      and devices.VENDOR_OK)
check("vendored bundle is @noble/post-quantum 0.7.1 and defines NovashareMLKEM", b"0.7.1" in v.content and b"NovashareMLKEM" in v.content)
devices.VENDOR_OK = False
check("a bundle failing its hash is not served (503)", C.get(f"/devices/static/{devices.vendor_noble_pq.NAME}", headers=H(X_Novashare=None)).status_code == 503)
devices.VENDOR_OK = True
check("unknown static file -> 404", C.get("/devices/static/x.js", headers=H(X_Novashare=None)).status_code == 404)
check("static files need a signed-in person too", C.get("/devices/static/devices.js", headers=H("stranger@example.invalid")).status_code == 403)
check("API answers are not cached", C.get("/devices/api/mine", headers=H()).headers.get("cache-control") == "no-store")

# --- 11. logs and the rest of the app
log = pathlib.Path(os.environ["INTAKE_LOG"]).read_text()
check("no email address in the log", "@" not in log)
check("existing routes still there (/up/start, /share, /healthz)",
      {"/up/start", "/share", "/healthz"} <= {getattr(rt, "path", "") for rt in app.app.routes})
check("DB file is private (mode 600)", oct(os.stat(os.environ["INTAKE_DEVICES_DB"]).st_mode & 0o777) == "0o600")

print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
