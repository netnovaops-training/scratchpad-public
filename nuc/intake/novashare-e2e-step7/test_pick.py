"""test_pick.py - server proof for pick.py and relay.py's "only" (Novashare E2E Step 5, 2026-11-24). Self-contained:
run INSIDE the patched image (SWAP-PATCH.sh does exactly this):

    docker run --rm --entrypoint python IMAGE test_pick.py

Fake people (example.invalid), temp DB / relay folder / log. Checks: the guard on every new route (origin, custom
header, view-only level, not in the household); the info answer (limits, room left after a send, Abyss only for the
admin); remembering the last choice: only household people other than me, only my own live device (someone else's
or a revoked one = 404, never confirmed), Abyss only for the admin, bad bodies refused, one row per person, a stored
choice re-checked when read (revoked device / person gone); relay create with "only": one of my devices, never
someone else's, never the sending one, never a revoked one, needs me in "to", the body hash covers it, Step 2's rule
unchanged without it; the page: CSP, COOP, SRI of every script and style; the drop's service worker opens the picker
for the horizon theme only (the old text for atomic, and with INTAKE_SHARE_PICKER=0); the library path's pages still
answer; the log has no emails, person ids or device ids from this module. Last line: ALL PASS / SOME FAILED.
Variables: fails, check(), H(), T (temp dir), D (device ids by name).
"""
import base64
import hashlib
import json
import os
import pathlib
import re
import sqlite3
import subprocess
import sys
import tempfile

T = pathlib.Path(tempfile.mkdtemp(prefix="picktest-"))
TOML = """[brand]
name = "Novashare"
theme = "{theme}"
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
"""
(T / "drop.toml").write_text(TOML.format(theme="horizon"))
(T / "atomic.toml").write_text(TOML.format(theme="atomic"))
os.environ.update(DROP_CONFIG=str(T / "drop.toml"), INTAKE_KEY="k-test", INTAKE_TOKEN="t-test",
                  INTAKE_ALLOWED_PEERS="127.0.0.1", INTAKE_STAGE=str(T / "stage"), INTAKE_UNSORTED=str(T / "unsorted"),
                  INTAKE_LOG=str(T / "intake.log"), INTAKE_DEVICES_DB=str(T / "devices.db"),
                  INTAKE_RELAY_DIR=str(T / "relay"), INTAKE_MAX_MB="64", INTAKE_RESERVE_MB="1")
for k in ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL"):
    os.environ.setdefault(k, "http://10.255.255.1")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import app  # noqa: E402
import atomic_ui  # noqa: E402
import devices  # noqa: E402
import pick  # noqa: E402
import relay  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    fails += not ok


C = TestClient(app.app, client=("127.0.0.1", 50000))
EMAIL = {"jason": "j@example.invalid", "casey": "z@example.invalid", "mom": "m@example.invalid"}


def H(who="jason", dev=None, **over):
    h = {"X-Auth-Email": EMAIL.get(who, who), "X-Auth-Level": "full", "Sec-Fetch-Site": "same-origin", "X-Novashare": "1"}
    if dev:
        h["X-Device-Id"] = dev
    for k, v in over.items():
        k = k.replace("_", "-")
        if v is None:
            h.pop(k, None)
        else:
            h[k] = v
    return h


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def mlkem_pub():
    import random
    co = [random.randrange(3329) for _ in range(768)]
    out = bytearray()
    for i in range(0, 768, 2):
        a, b = co[i], co[i + 1]
        out += bytes([a & 0xFF, (a >> 8) | ((b & 0x0F) << 4), b >> 4])
    return bytes(out) + os.urandom(32)


def reg(who, label):
    r = C.post("/devices/api/register", headers=H(who), json={"label": label, "x25519_pub": b64u(os.urandom(32)),
                                                              "mlkem_pub": b64u(mlkem_pub())})
    assert r.status_code == 201, r.text
    return r.json()["device_id"], r.json()["fingerprint"]


D, FP = {}, {}
for who, name in (("jason", "J1"), ("jason", "J2"), ("jason", "J3"), ("jason", "J4"), ("casey", "Z1"), ("casey", "Z2"),
                  ("mom", "M1")):
    D[name], FP[name] = reg(who, name)


def code(r):
    try:
        return r.json().get("code")
    except ValueError:
        return None


def body(to, wraps, only=None, plain=1000, tid=None):
    pieces = max(1, -(-plain // relay.PLAIN_PIECE))
    b = {"id": tid or b64u(os.urandom(16)), "alg": "novashare-v1", "to": list(to), "size_ct": plain + 16 * pieces,
         "pieces": pieces, "manifest": b64u(os.urandom(12 + 256 + 16)),
         "wraps": [{"device_id": D[w], "device_fp": FP[w], "eph_pub": b64u(os.urandom(32)),
                    "kem_ct": b64u(os.urandom(1088)), "wrapped_key": b64u(os.urandom(60))} for w in wraps]}
    if only is not None:
        b["only"] = [D.get(o, o) for o in only]
    return b


def create(b, dev="J1", who="jason"):
    return C.post("/relay/api/transfers", headers=H(who, D[dev]), json=b)


def wraps_of(tid):
    c = sqlite3.connect(T / "devices.db")
    try:
        return sorted(r[0] for r in c.execute("SELECT device_id FROM wrap WHERE transfer_id=?", (tid,)))
    finally:
        c.close()


INFO, LAST = "/relay/api/pick", "/relay/api/pick/last"

# ---- 1. the guard on every new API route
for method, path, js in (("GET", INFO, None), ("PUT", LAST, {"kind": "library"})):
    for label, over, want in (("cross-site", {"Sec_Fetch_Site": "cross-site"}, 403),
                              ("no Sec-Fetch-Site", {"Sec_Fetch_Site": None}, 403),
                              ("no X-Novashare", {"X_Novashare": None}, 403),
                              ("view-only level", {"X_Auth_Level": "view"}, 403),
                              ("not in the household", {"X_Auth_Email": "x@example.invalid"}, 403),
                              ("no sign-in", {"X_Auth_Email": None}, 403)):
        r = C.request(method, path, headers=H(**over), json=js)
        check(f"{method} {path}: {label} -> {want}", r.status_code == want)

# ---- 2. info
r = C.get(INFO, headers=H("casey"))
j = r.json()
check("info: 200 for a household person", r.status_code == 200 and j["me"] == "casey")
check("info: per-file limit = INTAKE_MAX_MB (64 MB here)", j["limits"]["max_bytes"] == 64 * 1048576)
check("info: room left starts at the open cap", j["limits"]["open_left_bytes"] == relay.open_cap()
      and j["limits"]["open_left_sends"] == relay.max_open() and j["limits"]["max_people"] == relay.MAX_TO)
check("info: no last choice yet", j["last"] is None)
check("info: Send as Abyss is not offered to Casey", j["abyss"] is None)
ja = C.get(INFO, headers=H("jason")).json()
check("info: Send as Abyss is offered to the admin, with its https origin and 200 MB",
      ja["abyss"] and ja["abyss"]["url"].startswith("https://") and ja["abyss"]["max_bytes"] == 200 * 1048576)
check("info: no file names, sizes of others or device ids in the answer", set(ja) == {"me", "limits", "last", "abyss"})

# ---- 3. remembering the last choice: people
ok = C.put(LAST, headers=H("jason"), json={"kind": "people", "people": ["casey", "mom"]})
check("last: people [casey, mom] -> 200", ok.status_code == 200 and ok.json()["last"] == {"kind": "people", "people": ["casey", "mom"]})
check("last: read back for Jason", C.get(INFO, headers=H("jason")).json()["last"] == {"kind": "people", "people": ["casey", "mom"]})
check("last: per person (Casey still has none)", C.get(INFO, headers=H("casey")).json()["last"] is None)
for label, b in (("myself among the people", {"kind": "people", "people": ["jason"]}),
                 ("someone outside the household", {"kind": "people", "people": ["ghost"]}),
                 ("a person twice", {"kind": "people", "people": ["casey", "casey"]}),
                 ("no people", {"kind": "people", "people": []}),
                 ("too many people", {"kind": "people", "people": [f"p{i}" for i in range(relay.MAX_TO + 1)]}),
                 ("people not a list", {"kind": "people", "people": "casey"}),
                 ("a person id not a string", {"kind": "people", "people": [1]}),
                 ("an unknown kind", {"kind": "everyone"}),
                 ("no kind", {"people": ["casey"]}),
                 ("not an object", ["casey"])):
    r = C.put(LAST, headers=H("jason"), json=b)
    check(f"last: {label} -> 400 bad_request", r.status_code == 400 and code(r) == "bad_request")
check("last: a refused PUT kept the earlier choice",
      C.get(INFO, headers=H("jason")).json()["last"] == {"kind": "people", "people": ["casey", "mom"]})

# ---- 4. devices: only my own live device
r = C.put(LAST, headers=H("jason"), json={"kind": "device", "device": D["J2"]})
check("last: my own device J2 -> 200", r.status_code == 200 and r.json()["last"] == {"kind": "device", "device": D["J2"]})
r = C.put(LAST, headers=H("jason"), json={"kind": "device", "device": D["Z1"]})
check("last: Casey's device -> 404 (never confirmed as someone else's)", r.status_code == 404 and code(r) == "not_found")
r404 = C.put(LAST, headers=H("jason"), json={"kind": "device", "device": b64u(os.urandom(16))})
check("last: a device that does not exist -> the same 404", r404.status_code == 404 and r404.json() == r.json())
r = C.put(LAST, headers=H("jason"), json={"kind": "device", "device": "../../etc"})
check("last: a malformed device id -> 400", r.status_code == 400)
check("last: Casey cannot pick Jason's device either",
      C.put(LAST, headers=H("casey"), json={"kind": "device", "device": D["J2"]}).status_code == 404)
rv = C.post(f"/devices/api/mine/{D['J4']}/revoke", headers=H("jason"))
r = C.put(LAST, headers=H("jason"), json={"kind": "device", "device": D["J4"]})
check("last: a revoked device of mine -> 404", rv.status_code == 200 and r.status_code == 404)
check("last: still J2 after the refusals", C.get(INFO, headers=H("jason")).json()["last"] == {"kind": "device", "device": D["J2"]})

# ---- 5. library and abyss
check("last: library -> 200", C.put(LAST, headers=H("casey"), json={"kind": "library"}).json()["last"] == {"kind": "library"})
r = C.put(LAST, headers=H("casey"), json={"kind": "abyss"})
check("last: abyss for Casey -> 403 not_admin", r.status_code == 403 and code(r) == "not_admin")
check("last: abyss for the admin -> 200", C.put(LAST, headers=H("jason"), json={"kind": "abyss"}).status_code == 200)
check("last: extra keys are dropped (only the cleaned choice is stored)",
      C.put(LAST, headers=H("mom"), json={"kind": "library", "note": "x" * 100}).json()["last"] == {"kind": "library"})
r = C.put(LAST, headers={**H("jason"), "Content-Type": "text/plain"}, content=b'{"kind":"library"}')
check("last: not JSON -> 415", r.status_code == 415)
r = C.put(LAST, headers={**H("jason"), "Content-Type": "application/json"}, content=b"{" + b" " * 5000 + b"}")
check("last: a body over 4 KB -> 413", r.status_code == 413)
r = C.put(LAST, headers={**H("jason"), "Content-Type": "application/json"}, content=b"{nope")
check("last: broken JSON -> 400", r.status_code == 400)
c = sqlite3.connect(T / "devices.db")
n = c.execute("SELECT count(*) FROM pick_last WHERE person='jason'").fetchone()[0]
check("last: one row per person, replaced each time", n == 1)

# ---- 6. a stored choice is re-checked when read
C.put(LAST, headers=H("jason"), json={"kind": "device", "device": D["J3"]})
C.post(f"/devices/api/mine/{D['J3']}/revoke", headers=H("jason"))
check("re-check: the remembered device was revoked -> last is null", C.get(INFO, headers=H("jason")).json()["last"] is None)
c.execute("UPDATE pick_last SET choice=? WHERE person='jason'", (json.dumps({"kind": "people", "people": ["casey", "ghost"]}),))
c.commit()
check("re-check: a person who left the household is dropped from the remembered people",
      C.get(INFO, headers=H("jason")).json()["last"] == {"kind": "people", "people": ["casey"]})
c.execute("UPDATE pick_last SET choice='not json' WHERE person='jason'")
c.commit()
check("re-check: a damaged row reads as no choice (not a 500)", C.get(INFO, headers=H("jason")).json()["last"] is None)
c.close()

# ---- 7. relay create with "only" (My devices: one of my other devices)
D["J5"], FP["J5"] = reg("jason", "J5")     # J3, J4 are revoked by now: live others of J1 = J2, J5
b = body(["jason"], ["J2"], only=["J2"])
r = create(b)
check("only: J1 -> my J2 alone -> 201", r.status_code == 201, )
check("only: the transfer has exactly one wrap, for J2", wraps_of(b["id"]) == [D["J2"]])
r = create(b)
check("only: the same body again -> 200 resumed", r.status_code == 200 and r.json().get("resumed") is True)
b2 = dict(b, only=[D["J2"], D["J1"]])
check("only: the same id with a different only -> 409 id_taken (the body hash covers it)", code(create(b2)) == "id_taken")
r = create(body(["jason"], ["J2"]))
check("only: without only, wrapping just J2 is still refused (Step 2's rule) -> 409 devices_changed",
      r.status_code == 409 and code(r) == "devices_changed")
r = create(body(["jason"], ["J2"], only=["Z1"]))
check("only: someone else's device -> 409 devices_changed", r.status_code == 409 and code(r) == "devices_changed")
r = create(body(["jason"], ["J2"], only=["J1"]))
check("only: the sending device itself -> 409 devices_changed", r.status_code == 409 and code(r) == "devices_changed")
r = create(body(["jason"], ["J2"], only=["J4"]))
check("only: a revoked device of mine -> 409 devices_changed", r.status_code == 409 and code(r) == "devices_changed")
r = create(body(["jason"], ["J2"], only=[b64u(os.urandom(16))]))
check("only: a device that does not exist -> the same 409", r.status_code == 409 and code(r) == "devices_changed")
r = create(body(["casey"], ["Z1", "Z2"], only=["J2"]))
check("only: needs me among the people -> 400", r.status_code == 400 and code(r) == "bad_request")
for label, bad in (("not a list", "x"), ("empty", []), ("a malformed id", ["../x"]), ("an id twice", [D["J2"], D["J2"]]),
                   ("too many", [b64u(os.urandom(16)) for _ in range(devices.MAX_LIVE + 1)]), ("a number", [5])):
    bb = body(["jason"], ["J2"])
    bb["only"] = bad
    r = create(bb)
    check(f"only: {label} -> 400", r.status_code == 400 and code(r) == "bad_request")
r = create(body(["jason"], ["J1"], only=["J2"]))
check("only: wraps that do not match the picked device -> 409 devices_changed", code(r) == "devices_changed")
b3 = body(["jason", "casey"], ["J2", "Z1", "Z2"], only=["J2"])
r = create(b3)
check("only: me (J2 only) and Casey (all her devices) in one send -> 201 with 3 wraps",
      r.status_code == 201 and wraps_of(b3["id"]) == sorted([D["J2"], D["Z1"], D["Z2"]]))
r = create(body(["jason", "casey"], ["J2", "Z1"], only=["J2"]))
check("only: Casey's devices must still be all of hers -> 409", code(r) == "devices_changed")
r = create(body(["casey"], ["Z1", "Z2"]), dev="J2")
check("Step 2 regression: a normal send to Casey from J2 -> 201", r.status_code == 201)
r = create(body(["jason"], ["J1"], only=["J1"]), dev="Z1", who="casey")
check("only: Casey cannot aim a send at Jason's device through only -> 409 (her own devices only)",
      r.status_code in (400, 409))

# ---- 8. room left after sends
ja = C.get(INFO, headers=H("jason")).json()
check("info: room left went down by what Jason has open", ja["limits"]["open_left_bytes"] < relay.open_cap()
      and ja["limits"]["open_left_sends"] == relay.max_open() - 3)

# ---- 9. the page and its files
r = C.get("/relay/share?k=123", headers=H(Sec_Fetch_Site="none", X_Novashare=None))
page = r.text
check("page: 200 for a signed-in person on a fresh navigation", r.status_code == 200)
check("page: strict CSP (no inline script, Trusted Types)", r.headers.get("content-security-policy") == devices.CSP)
check("page: COOP allows the Abyss pop-up only", r.headers.get("cross-origin-opener-policy") == "same-origin-allow-popups")
check("page: no-store, nosniff, DENY framing", r.headers.get("cache-control") == "no-store"
      and r.headers.get("x-content-type-options") == "nosniff" and r.headers.get("x-frame-options") == "DENY")
check("page: no inline script", not re.search(r"<script(?![^>]*\bsrc=)", page))
for path in ("/relay/pick/pick.js", "/relay/pick/pick.css", "/relay/static/relay.js", "/devices/static/devices.css",
             f"/devices/static/{devices.vendor_noble_pq.NAME}"):
    m = re.search(re.escape(path) + r'" integrity="([^"]+)"', page)
    got = C.get(path, headers=H(Sec_Fetch_Site="same-origin", X_Novashare=None))
    check(f"page: {path} has SRI and the served bytes match it", bool(m) and got.status_code == 200
          and m.group(1) == devices._sri(got.content))
check("page: the library option says the server reads the file", "The server" in page and "reads this file to sort it" in page)
check("page: big tap target styles (58 px button, 64 px options)", b"min-height:58px" in pick._CSS and b"min-height:64px" in pick._CSS)
check("page: motion only with prefers-reduced-motion: no-preference", b"prefers-reduced-motion:no-preference" in pick._CSS
      and b"transition" not in pick._CSS.split(b"no-preference")[0])
check("page: the sticky send bar never hides a focused option (scroll-padding)", b"scroll-padding-bottom" in pick._CSS)
r = C.get("/relay/share", headers=H(X_Auth_Level="view", Sec_Fetch_Site="none"))
check("page: view-only -> 403", r.status_code == 403)
check("static: an unknown name -> 404", C.get("/relay/pick/x.js", headers=H()).status_code == 404)
check("static: signed out -> 403", C.get("/relay/pick/pick.js", headers=H(X_Auth_Email=None)).status_code == 403)
js = pick_js_text = pick.pick_js.JS
check("pick.js: no innerHTML / insertAdjacentHTML / eval", not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|eval\(|new Function", js))
check("pick.js: the library copies to atomic-share and opens /sending?k=", "tx(LIB, 'readwrite'" in js and "'/sending?k='" in js)
rj = relay._JS_BYTES.decode()
check("relay.js: exports NovashareSend and runs main() on the Send page only",
      "globalThis.NovashareSend" in rj and "if ($('file') && $('send')) main();" in rj)
check("relay.js: no plaintext path added (no fetch of /up/ or /share)", "/up/" not in rj and "'share'" not in rj)

# ---- 10. the drop's service worker: picker for horizon only
sw = C.get("/sw.js").text
check("sw.js (horizon): a share opens /relay/share in its own store", atomic_ui.PICKER and "relay/share?k=" in sw
      and "'novashare-pick'" in sw and "sending?k=" not in sw)


def sw_with(toml, extra=None):
    env = {**os.environ, "DROP_CONFIG": str(T / toml), **(extra or {})}
    out = subprocess.run([sys.executable, "-c", "import atomic_ui,hashlib;print(atomic_ui.PICKER, 'sending?k=' in atomic_ui.SW,"
                          " 'novashare-pick' in atomic_ui.SW)"], cwd=str(HERE), env=env, capture_output=True, text=True)
    return out.stdout.strip()


check("sw.js (atomic theme): the old worker, straight to /sending", sw_with("atomic.toml") == "False True False")
check("sw.js (horizon, INTAKE_SHARE_PICKER=0): the old worker", sw_with("drop.toml", {"INTAKE_SHARE_PICKER": "0"}) == "False True False")

# ---- 11. the library path itself still answers
r = C.get("/sending", headers={"X-Auth-Email": EMAIL["casey"]})
# step 7: the page script is the file /assets/sending.js now (strict CSP, no inline script)
js = C.get("/assets/sending.js", headers={"X-Auth-Email": EMAIL["casey"]}).text
check("library: /sending still serves its page, reading 'atomic-share'", r.status_code == 200 and "'atomic-share'" in js
      and "/assets/sending.js" in r.text)
r = C.post("/share", headers={"X-Auth-Email": EMAIL["casey"], "X-Auth-Level": "full", "Accept": "application/json"},
           data={"title": ""})
check("library: POST /share (no service worker) still answers as before", r.status_code == 200 and r.json() == [])
check("library: the manifest's share target is unchanged (POST share, files)",
      C.get("/manifest.webmanifest").json()["share_target"]["action"] == "share")

# ---- 12. the log
log = (T / "intake.log").read_text(errors="replace") if (T / "intake.log").exists() else ""
lines = [ln for ln in log.splitlines() if "pick" in ln]
check(f"log: pick lines were written ({len(lines)})", len(lines) >= 3)
check("log: no emails, person ids or device ids in the pick lines",
      not any(("@" in ln or "casey" in ln or "mom" in ln or any(d[:6] in ln for d in D.values())) for ln in lines))

print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
