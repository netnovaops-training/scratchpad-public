"""test_push.py - server proof for push.py (Novashare E2E Step 4, 2026-11-24). Self-contained: run INSIDE the patched
image (SWAP-PATCH.sh does exactly this; the image has pywebpush, http_ece and cryptography):

    docker run --rm --entrypoint python IMAGE test_push.py

Fake people (example.invalid), temp DB / relay folder / log, a THROWAWAY VAPID key made here (never printed).
Checks: the guard on every push route; subscribe / state / unsubscribe / test only for the caller's own live device
(another person's device id, a revoked one, none = 403); endpoint allowlist (SSRF door closed) and key shapes; one row per
device; an endpoint held by another person = 409; a new send queues a push only for subscribed, still-pending receiving
devices and only once it is ready; the payload has NO names, emails, labels, file names, captions or sizes; save /
decline queue 'delivered' pushes to the sender's devices; 410 / 404 delete the subscription; 5xx retries once then gives
up; revoke deletes the subscription and its queued pushes; push off without a key; an old-key subscription is dropped;
the test route's rate limit; the pages carry push.js with a matching SRI; the worker's push handlers; and ONE REAL
pywebpush send to an in-process mock push service, whose captured body is decrypted with the subscription's private key
(RFC 8291) and whose VAPID JWT is verified (RFC 8292). No names or endpoints in the log. Last line: ALL PASS / SOME FAILED.
Variables: fails, check(), H(), T (temp dir), D/FP (device ids / fingerprints by label), SENT (fake push results).
"""
import base64
import hashlib
import http.server
import json
import os
import pathlib
import re
import sqlite3
import sys
import tempfile
import threading
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

T = pathlib.Path(tempfile.mkdtemp(prefix="pushtest-"))
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
""")


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


VKEY = ec.generate_private_key(ec.SECP256R1())          # throwaway VAPID key, only in this process
VRAW = b64u(VKEY.private_numbers().private_value.to_bytes(32, "big"))
os.environ.update(DROP_CONFIG=str(T / "drop.toml"), INTAKE_KEY="k-test", INTAKE_TOKEN="t-test",
                  INTAKE_ALLOWED_PEERS="127.0.0.1", INTAKE_STAGE=str(T / "stage"), INTAKE_UNSORTED=str(T / "unsorted"),
                  INTAKE_LOG=str(T / "intake.log"), INTAKE_DEVICES_DB=str(T / "devices.db"),
                  INTAKE_RELAY_DIR=str(T / "relay"), INTAKE_MAX_MB="64", INTAKE_RESERVE_MB="1",
                  VAPID_PRIVATE_KEY="  " + VRAW + "\r\n")        # stray CR / spaces must be stripped
for k in ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL"):
    os.environ.setdefault(k, "http://10.255.255.1")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import app  # noqa: E402
import devices  # noqa: E402
import push  # noqa: E402
import receive  # noqa: E402,F401  (imported so its routes and page hooks exist)
import relay  # noqa: E402,F401
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
WHO = {"J1": "jason", "J2": "jason", "Z1": "casey", "Z2": "casey", "Z3": "casey", "M1": "mom"}
for name, who in WHO.items():
    D[name], FP[name] = reg(who, name + "-label-Kitchen")


def code(r):
    try:
        return r.json().get("code")
    except ValueError:
        return None


class Sub:
    """A browser-like push subscription: a real P-256 key pair and a 16-byte auth secret."""

    def __init__(self, endpoint):
        self.priv = ec.generate_private_key(ec.SECP256R1())
        self.pub = self.priv.public_key().public_bytes(serialization.Encoding.X962,
                                                       serialization.PublicFormat.UncompressedPoint)
        self.auth = os.urandom(16)
        self.endpoint = endpoint

    def body(self):
        return {"endpoint": self.endpoint, "keys": {"p256dh": b64u(self.pub), "auth": b64u(self.auth)}}


def subscribe(dev, sub):
    return C.put("/relay/api/push/sub", headers=H(WHO[dev], D[dev]), json=sub.body())


def send(to=("casey",), wraps=("Z1", "Z2", "Z3"), finish=True, sender="J1"):
    """A Step 2 send with random ciphertext (the server never decrypts); returns the transfer id."""
    plain = 10
    size_ct, pieces = plain + 16, 1
    tid = b64u(os.urandom(16))
    body = {"id": tid, "alg": "novashare-v1", "to": list(to), "size_ct": size_ct, "pieces": pieces,
            "manifest": b64u(os.urandom(12 + 256 + 16)),
            "wraps": [{"device_id": D[w], "device_fp": FP[w], "eph_pub": b64u(os.urandom(32)),
                       "kem_ct": b64u(os.urandom(1088)), "wrapped_key": b64u(os.urandom(60))} for w in wraps]}
    r = C.post("/relay/api/transfers", headers=H(WHO[sender], D[sender]), json=body)
    assert r.status_code == 201, r.text
    if finish:
        p = os.urandom(size_ct)
        h = {**H(WHO[sender], D[sender]), "X-Piece-Sha256": hashlib.sha256(p).hexdigest()}
        assert C.put(f"/relay/api/transfers/{tid}/pieces/0", headers=h, content=p).status_code == 200
    return tid


db = sqlite3.connect(os.environ["INTAKE_DEVICES_DB"])


def outbox(dev=None):
    q = "SELECT device_id, kind, transfer_id, result, tries FROM push_out"
    rows = db.execute(q + (" WHERE device_id=?" if dev else "") + " ORDER BY id", ((D[dev],) if dev else ())).fetchall()
    return rows


def subrow(dev):
    return db.execute("SELECT endpoint, kid FROM push_sub WHERE device_id=?", (D[dev],)).fetchone()


# fake push service for the rule checks: push._post is replaced; SENT collects (device, payload json)
SENT, ANSWER = [], {}
_real_post = push._post


def fake_post(sub, data, urgency):
    dev = next(k for k, v in D.items() if v == sub["device_id"])
    SENT.append((dev, json.loads(data), urgency))
    st = ANSWER.get(dev, 201)
    return (st() if callable(st) else st), 0


push._post = fake_post
push.load_key()
FCM = "https://fcm.googleapis.com/fcm/send/"

# --- 1. key route and push on
r = C.get("/relay/api/push/key", headers=H("casey"))
pk = r.json().get("public_key") or ""
check("key: enabled with the throwaway key (CR / spaces stripped)", r.status_code == 200 and r.json()["enabled"] is True)
pub_expected = VKEY.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
check("key: the public key is the 65-byte uncompressed point of the private key", b64d(pk) == pub_expected)
check("key: the private key never appears in the answer", VRAW not in r.text)
check("key: Cache-Control no-store", r.headers.get("cache-control") == "no-store")

# --- 2. the guard on every push route
routes = [("GET", "/relay/api/push/key"), ("GET", "/relay/api/push/sub"), ("PUT", "/relay/api/push/sub"),
          ("DELETE", "/relay/api/push/sub"), ("POST", "/relay/api/push/test")]
cases = {"no Sec-Fetch-Site": H("casey", D["Z1"], Sec_Fetch_Site=None),
         "cross-site": H("casey", D["Z1"], Sec_Fetch_Site="cross-site"),
         "same-site sibling": H("casey", D["Z1"], Sec_Fetch_Site="same-site"),
         "no X-Novashare": H("casey", D["Z1"], X_Novashare=None), "view level": H("casey", D["Z1"], X_Auth_Level="view"),
         "outsider": H("x@evil.invalid", D["Z1"]), "no email": H("casey", D["Z1"], X_Auth_Email=None)}
bad = []
for m, path in routes:
    for name, h in cases.items():
        rr = C.request(m, path, headers=h, json=Sub(FCM + "a" * 40).body() if m == "PUT" else None)
        if rr.status_code != 403:
            bad.append(f"{m} {path} {name}={rr.status_code}")
check("guard: every push route refuses every bad request (403)", not bad)
if bad:
    print("   ", bad[:6])

# --- 3. subscribe: only the caller's own live device
s = Sub(FCM + "z1-" + "x" * 40)
r = C.put("/relay/api/push/sub", headers=H("casey"), json=s.body())
check("own device: no X-Device-Id = 403 not_your_device", r.status_code == 403 and code(r) == "not_your_device")
r = C.put("/relay/api/push/sub", headers=H("jason", D["Z1"]), json=s.body())
check("own device: Jason with Casey's device id = 403 (nothing stored)", r.status_code == 403 and subrow("Z1") is None)
r = C.get("/relay/api/push/sub", headers=H("jason", D["Z1"]))
check("own device: Jason cannot read Casey's device state", r.status_code == 403)
r = C.delete("/relay/api/push/sub", headers=H("jason", D["Z1"]))
check("own device: Jason cannot unsubscribe Casey's device", r.status_code == 403)
r = subscribe("Z1", s)
check("subscribe: Z1 own device = 200 subscribed", r.status_code == 200 and r.json()["subscribed"] is True)
row = subrow("Z1")
check("subscribe: one row for Z1 with the current key id", row is not None and row[0] == s.endpoint and row[1] == push.KEY.kid)
r = subscribe("Z1", s)
n = db.execute("SELECT count(*) FROM push_sub WHERE device_id=?", (D["Z1"],)).fetchone()[0]
check("subscribe: the same subscription again = still one row", r.status_code == 200 and n == 1)
s1b = Sub(FCM + "z1-new-" + "y" * 40)
subscribe("Z1", s1b)
check("subscribe: a new browser subscription REPLACES Z1's row (one per device)", subrow("Z1")[0] == s1b.endpoint
      and db.execute("SELECT count(*) FROM push_sub WHERE device_id=?", (D["Z1"],)).fetchone()[0] == 1)
st = C.get("/relay/api/push/sub", headers=H("casey", D["Z1"])).json()
check("state: Z1 subscribed with the current key", st == {"subscribed": True, "current_key": True, "enabled": True})
st = C.get("/relay/api/push/sub", headers=H("casey", D["Z2"])).json()
check("state: Z2 not subscribed", st["subscribed"] is False)

# --- 4. endpoint allowlist (the server POSTs there: SSRF) and key shapes
bad_eps = ["http://fcm.googleapis.com/fcm/send/abc", "https://fcm.googleapis.com:8443/fcm/send/abc",
           "https://evil.example.com/push/abc", "https://fcm.googleapis.com.evil.com/x/abc",
           "https://user@fcm.googleapis.com/fcm/send/abc", "https://169.254.169.254/latest/meta-data",
           "https://127.0.0.1/x/abcdefgh", "https://localhost/push/abcdef", "ftp://fcm.googleapis.com/abc",
           "https://evilpush.apple.com.attacker.net/x", "https://fcm.googleapis.com/a b",
           "https://fcm.googleapis.com/x#frag", "https://notify.windows.com\\@evil.com/x", "x" * 2000]
acc = []
for ep in bad_eps:
    rr = subscribe("Z2", Sub(ep))
    if rr.status_code != 400:
        acc.append(f"{ep[:50]}={rr.status_code}")
check("allowlist: 14 hostile / wrong endpoints refused (400)", not acc)
if acc:
    print("   ", acc)
good = ["https://fcm.googleapis.com/fcm/send/abc123", "https://updates.push.services.mozilla.com/wpush/v2/abc",
        "https://web.push.apple.com/QGuQyavXutnMH", "https://wns2-par02p.notify.windows.com/w/?token=abc"]
ok = [subscribe("Z3", Sub(ep)).status_code for ep in good]
check("allowlist: FCM, Mozilla, Apple and Windows push services accepted", ok == [200] * 4)
bs = Sub(FCM + "shape")
bad_bodies = [{"endpoint": FCM + "a1"}, {**bs.body(), "keys": {"p256dh": b64u(b"\x04" + os.urandom(64)), "auth": b64u(bs.auth)}},
              {**bs.body(), "keys": {"p256dh": b64u(bs.pub), "auth": b64u(os.urandom(15))}},
              {**bs.body(), "keys": {"p256dh": b64u(bs.pub[:33]), "auth": b64u(bs.auth)}}, [1, 2], "x"]
codes = [C.put("/relay/api/push/sub", headers=H("casey", D["Z2"]), json=b).status_code for b in bad_bodies]
check("shapes: missing keys, a point off the curve, short auth, compressed point, non-objects = 400", codes == [400] * 6)
r = C.put("/relay/api/push/sub", headers={**H("casey", D["Z2"]), "Content-Type": "application/json"},
          content=b'{"endpoint":"' + b"a" * 5000 + b'"}')
check("shapes: a body over 4 KB = 413", r.status_code == 413)
check("shapes: Z2 still has no subscription after all of that", subrow("Z2") is None)

# --- 5. one endpoint, one person
r = subscribe("M1", s1b)
check("endpoint held by Casey's device: Mom's device gets 409 endpoint_taken", r.status_code == 409 and code(r) == "endpoint_taken"
      and subrow("Z1")[0] == s1b.endpoint and subrow("M1") is None)
r = subscribe("Z2", s1b)
check("the same person's other device may take it over (moves, still one row)", r.status_code == 200
      and subrow("Z2") is not None and subrow("Z1") is None)
s1 = Sub(FCM + "z1-final-" + "q" * 30)
subscribe("Z1", s1)
s2 = Sub(FCM + "z2-final-" + "r" * 30)
subscribe("Z2", s2)
C.delete("/relay/api/push/sub", headers=H("casey", D["Z3"]))
j1 = Sub(FCM + "j1-" + "s" * 30)
check("Jason's J1 subscribes", subscribe("J1", j1).status_code == 200)

# --- 6. a new send: a push for each subscribed, pending, live receiving device, only once ready
db.execute("DELETE FROM push_out")
db.commit()
tid = send(finish=False)
check("uploading: nothing queued before the send is ready", outbox() == [])
p = os.urandom(26)
h = {**H("jason", D["J1"]), "X-Piece-Sha256": hashlib.sha256(p).hexdigest()}
check("last piece -> ready", C.put(f"/relay/api/transfers/{tid}/pieces/0", headers=h, content=p).json()["state"] == "ready")
q = outbox()
check("ready: exactly Z1 and Z2 queued ('new'), not unsubscribed Z3, not the sender",
      sorted((r[0], r[1], r[2]) for r in q) == sorted([(D["Z1"], "new", tid), (D["Z2"], "new", tid)]))
SENT.clear()
push.drain_once()
check("drain: two pushes sent, outbox empty", len(SENT) == 2 and outbox() == [])
names = ["Jason", "jason", "Casey", "casey", "Mom", "example.invalid", "Kitchen", "label", "size", "name", "caption", "file"]
def _no_id(pl):
    return json.dumps({k: v for k, v in pl.items() if k != "id"})   # the id is random base64url: it may spell "casey"


leak = [(d, k) for d, pl, _ in SENT for k in names if k in _no_id(pl)]
check("payload: no names, emails, device labels, sizes, file names or captions", not leak)
check("payload: exactly {v, kind, id, body} with the transfer id and the fixed text",
      all(pl == {"v": 1, "kind": "new", "id": tid, "body": "You have a new Novashare item"} for _, pl, _ in SENT))
check("payload: 'new' is sent with Urgency high", all(u == "high" for _, _, u in SENT))
tid_b = send(to=("casey", "mom"), wraps=("Z1", "Z2", "Z3", "M1"))
check("a send to two people: only the subscribed devices (Z1, Z2) queued", sorted(r[0] for r in outbox()) == sorted([D["Z1"], D["Z2"]]))
push.drain_once()

# --- 7. saved / declined -> 'delivered' to the sender's subscribed devices
SENT.clear()
C.post(f"/relay/api/transfers/{tid}/accept", headers=H("casey", D["Z1"]))
check("accept alone queues nothing", outbox() == [])
C.post(f"/relay/api/transfers/{tid}/complete", headers=H("casey", D["Z1"]))
q = outbox()
check("Z1 saved: one 'delivered' (saved) push for the sender's J1 only", [(r[0], r[1], r[2], r[3]) for r in q]
      == [(D["J1"], "delivered", tid, "saved")])
push.drain_once()
C.post(f"/relay/api/transfers/{tid}/decline", headers=H("casey", D["Z2"]))
q = outbox()
check("Z2 declined: one 'delivered' (declined) push for J1", [(r[0], r[1], r[3]) for r in q] == [(D["J1"], "delivered", "declined")])
push.drain_once()
check("delivered payloads: kind, id, result and fixed text only", [pl for d, pl, _ in SENT] == [
    {"v": 1, "kind": "delivered", "id": tid, "body": "Your Novashare item was delivered", "result": "saved"},
    {"v": 1, "kind": "delivered", "id": tid, "body": "Your Novashare item was declined", "result": "declined"}])
check("delivered payloads carry no names", not [k for _, pl, _ in SENT for k in names if k in _no_id(pl)])

# --- 8. the push service says gone (410 / 404): the subscription is deleted
SENT.clear()
ANSWER["Z2"] = 410
tid2 = send()
push.drain_once()
check("410 from the push service: Z2's subscription deleted, its queue empty", subrow("Z2") is None and outbox("Z2") == [])
check("410: Z1 (201) keeps its subscription", subrow("Z1") is not None)
check("410: state route now says Z2 is not subscribed", C.get("/relay/api/push/sub", headers=H("casey", D["Z2"])).json()["subscribed"] is False)
subscribe("Z2", s2)
ANSWER["Z2"] = 404
send()
push.drain_once()
check("404 is treated the same (subscription deleted)", subrow("Z2") is None)
ANSWER.pop("Z2")

# --- 9. 5xx / network: one retry with backoff, then dropped; the subscription stays
ANSWER["Z1"] = 503
send(wraps=("Z1", "Z2", "Z3"))
push.drain_once()
q = outbox("Z1")
due = db.execute("SELECT next_at FROM push_out WHERE device_id=?", (D["Z1"],)).fetchone()
check("503: kept for ONE retry (tries=1, due in >= 30 s)", len(q) == 1 and q[0][4] == 1 and due[0] >= time.time() + 25)
n_before = len(SENT)
push.drain_once()
check("503: not retried before it is due", len(SENT) == n_before)
db.execute("UPDATE push_out SET next_at=0")
db.commit()
push.drain_once()
check("503 again: given up (outbox empty), subscription kept", outbox("Z1") == [] and subrow("Z1") is not None)
ANSWER["Z1"] = None                                   # None = a network failure / timeout
send(wraps=("Z1", "Z2", "Z3"))
push.drain_once()
check("network failure: one retry queued", [r[4] for r in outbox("Z1")] == [1])
ANSWER["Z1"] = 201
db.execute("UPDATE push_out SET next_at=0")
db.commit()
push.drain_once()
check("network failure then 201 on the retry: sent, outbox empty", outbox("Z1") == [])
ANSWER["Z1"] = 400
send(wraps=("Z1", "Z2", "Z3"))
push.drain_once()
check("400 (a refusal): dropped at once, no retry, subscription kept", outbox("Z1") == [] and subrow("Z1") is not None)
ANSWER.pop("Z1")
db.execute("INSERT INTO push_out(device_id,kind,transfer_id,created,next_at) VALUES(?,?,?,?,?)",
           (D["Z1"], "new", tid, int(time.time()) - 90000, 0))
db.commit()
n_before = len(SENT)
push.drain_once()
check("a queued push older than 1 day is dropped unsent", outbox("Z1") == [] and len(SENT) == n_before)

# --- 10. revoke and unsubscribe
subscribe("Z3", Sub(FCM + "z3-" + "t" * 30))
send(wraps=("Z1", "Z2", "Z3"))
check("Z3 queued before revoke", len(outbox("Z3")) == 1)
r = C.post(f"/devices/api/mine/{D['Z3']}/revoke", headers=H("casey"))
check("revoke: Z3's subscription and queued pushes deleted (trigger, same transaction)", r.status_code == 200
      and subrow("Z3") is None and outbox("Z3") == [])
r = subscribe("Z3", Sub(FCM + "z3-again-" + "u" * 30))
check("revoke: a revoked device cannot subscribe again (403)", r.status_code == 403 and subrow("Z3") is None)
push.drain_once()
r = C.delete("/relay/api/push/sub", headers=H("casey", D["Z1"]))
r2 = C.delete("/relay/api/push/sub", headers=H("casey", D["Z1"]))
check("unsubscribe: Z1 row gone; repeat = 200", r.status_code == 200 and r2.status_code == 200 and subrow("Z1") is None)
send(wraps=("Z1", "Z2"))
check("unsubscribed devices get nothing queued", outbox() == [])
subscribe("Z1", s1)

# --- 11. the test route: own subscribed device, rate limited
SENT.clear()
r = C.post("/relay/api/push/test", headers=H("casey", D["Z2"]))
check("test: an unsubscribed device = 409 not_subscribed", r.status_code == 409 and code(r) == "not_subscribed")
codes = [C.post("/relay/api/push/test", headers=H("casey", D["Z1"])).status_code for _ in range(6)]
check("test: 5 per 10 minutes, the 6th = 429", codes == [200] * 5 + [429])
push.drain_once()
check("test: payload {v, kind:test, body} only", all(pl == {"v": 1, "kind": "test", "body": "Notifications work on this device"}
                                                     for _, pl, _ in SENT) and len(SENT) == 5)

# --- 12. a VAPID key change drops old subscriptions; no key = push off
old_kid = push.KEY.kid
VKEY2 = ec.generate_private_key(ec.SECP256R1())
os.environ["VAPID_PRIVATE_KEY"] = b64u(VKEY2.private_numbers().private_value.to_bytes(32, "big"))
push.load_key()
st = C.get("/relay/api/push/sub", headers=H("casey", D["Z1"])).json()
check("key change: state says subscribed but NOT with the current key (the page re-subscribes)",
      st["subscribed"] is True and st["current_key"] is False and push.KEY.kid != old_kid)
SENT.clear()
send(wraps=("Z1", "Z2"))
push.drain_once()
check("key change: an old-key subscription is dropped, not sent to", SENT == [] and subrow("Z1") is None)
os.environ["VAPID_PRIVATE_KEY"] = VRAW
push.load_key()
for bad_key in ("", "not-a-key", "A" * 44, b64u(b"\x00" * 32)):
    os.environ["VAPID_PRIVATE_KEY"] = bad_key
    push.load_key()
    if push.KEY.vapid is not None:
        break
r = C.get("/relay/api/push/key", headers=H("casey"))
check("no / bad key: push off, key route says enabled false", r.json() == {"enabled": False, "public_key": None})
r = subscribe("Z1", s1)
check("push off: subscribe = 503 push_off", r.status_code == 503 and code(r) == "push_off")
check("push off: the reason names no key material", "not-a-key" not in push.KEY.why and VRAW not in push.KEY.why)
os.environ["VAPID_PRIVATE_KEY"] = VRAW
push.load_key()
subscribe("Z1", s1)

# --- 13. pages, script, service worker
for path in ("/devices", "/relay/inbox"):
    pg = C.get(path, headers=H("casey", Sec_Fetch_Site="none", X_Novashare=None))
    m = re.search(r'src="/relay/push/push.js" integrity="([^"]+)"', pg.text)
    check(f"page {path}: push.js tag with SRI and the Notifications box", pg.status_code == 200 and m is not None
          and 'id=push-box' in pg.text and m.group(1) == devices._sri(push.JS))
js = C.get("/relay/push/push.js", headers=H("casey", Sec_Fetch_Site="same-origin", X_Novashare=None))
check("push.js: served (signed in), nosniff, the same bytes as the SRI", js.status_code == 200 and js.content == push.JS
      and js.headers.get("x-content-type-options") == "nosniff")
check("push.js: outsiders get 403", C.get("/relay/push/push.js", headers=H("x@evil.invalid")).status_code == 403)
pj = push.JS.decode()
first_req = pj.index("Notification.requestPermission")
check("push.js: the permission prompt is only inside turnOn (after a tap), never at load",
      pj.count("requestPermission") == 1 and pj.rfind("async function turnOn", 0, first_req) != -1)
check("push.js: no innerHTML / eval / outside URLs", not re.search(r"innerHTML|eval\(|https?://", pj))
sw = C.get("/relay/sw.js", headers=H("casey", Sec_Fetch_Site="same-origin", X_Novashare=None)).text
check("sw.js: push + notificationclick handlers; Accept opens /relay/inbox#<id>",
      "addEventListener('push'" in sw and "addEventListener('notificationclick'" in sw
      and "'/relay/inbox'" in sw and "'#' + id" in sw and "action: 'accept'" in sw)
check("sw.js: the text is fixed per kind; the id must look like a transfer id", "/^[A-Za-z0-9_-]{22}$/" in sw
      and "d.body" not in sw)
check("sw.js: still has no imports and no network fetch of its own", "importScripts" not in sw and "fetch(" not in sw)

# --- 14. ONE REAL SEND through pywebpush to an in-process mock push service; decrypt the capture (RFC 8291)
CAP = []


class Mock(http.server.BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        CAP.append((self.path, dict(self.headers), body))
        st = 410 if self.path.startswith("/p/gone") else 307 if self.path.startswith("/p/redir") else 201
        if self.path.startswith("/p/slow"):
            time.sleep(3)
        self.send_response(st)
        if st == 307:
            self.send_header("Location", "/p/stolen-by-redirect")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Mock)
threading.Thread(target=srv.serve_forever, daemon=True).start()
ORIGIN = f"http://127.0.0.1:{srv.server_address[1]}"
check("allowlist: the mock origin is refused until INTAKE_PUSH_TEST_ORIGINS names it",
      subscribe("J2", Sub(ORIGIN + "/p/j2-abcdef")).status_code == 400)
os.environ["INTAKE_PUSH_TEST_ORIGINS"] = ORIGIN
push._post = _real_post
real = Sub(ORIGIN + "/p/z1real-" + b64u(os.urandom(12)))
check("real: Z1 subscribes to the mock", subscribe("Z1", real).status_code == 200)
gone = Sub(ORIGIN + "/p/gone-" + b64u(os.urandom(12)))
subscribe("Z2", gone)
db.execute("DELETE FROM push_out")
db.commit()
tid3 = send(wraps=("Z1", "Z2"))
push.drain_once()
urlpath = "/p/" + real.endpoint.split("/p/")[1]
got = [c for c in CAP if c[0] == urlpath]
check("real: the mock received exactly one POST for Z1", len(got) == 1)
import http_ece  # noqa: E402

hdr, body = ({k.lower(): v for k, v in got[0][1].items()}, got[0][2]) if got else ({}, b"")
try:
    plain = http_ece.decrypt(body, private_key=real.priv, auth_secret=real.auth, version="aes128gcm")
    pl = json.loads(plain)
except Exception as e:  # noqa: BLE001
    plain, pl = b"", {"error": type(e).__name__}
check("real: the captured body decrypts with the subscription's private key + auth (aes128gcm)",
      pl == {"v": 1, "kind": "new", "id": tid3, "body": "You have a new Novashare item"})
check("real: the ciphertext does not contain the transfer id or the text in the clear",
      tid3.encode() not in body and b"Novashare" not in body)
check("real: Content-Encoding aes128gcm, TTL 86400, Urgency high",
      hdr.get("content-encoding") == "aes128gcm" and hdr.get("ttl") == "86400" and hdr.get("urgency") == "high")
auth = hdr.get("authorization", "")
m = re.fullmatch(r"vapid t=([A-Za-z0-9_.-]+), ?k=([A-Za-z0-9_-]+)", auth)
ok_jwt = False
if m:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    tok, k = m.group(1), m.group(2)
    h64, c64, s64 = tok.split(".")
    sig = b64d(s64)
    claims = json.loads(b64d(c64))
    pubk = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(k))
    try:
        pubk.verify(encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
                    (h64 + "." + c64).encode(), ec.ECDSA(hashes.SHA256()))
        ok_jwt = (b64d(k) == pub_expected and claims.get("aud") == ORIGIN and claims.get("sub") == push.subject()
                  and time.time() < claims.get("exp", 0) <= time.time() + 86400 and json.loads(b64d(h64))["alg"] == "ES256")
    except Exception:  # noqa: BLE001
        ok_jwt = False
check("real: VAPID header 'vapid t=JWT, k=<served public key>', ES256 signature valid, aud = push origin, sub, exp <= 24 h", ok_jwt)
check("real: 410 from the mock deleted Z2's subscription", subrow("Z2") is None)
slow = Sub(ORIGIN + "/p/slow-" + b64u(os.urandom(12)))
subscribe("Z2", slow)
push.TIMEOUT_S = 1
db.execute("DELETE FROM push_out")
db.commit()
send(wraps=("Z1", "Z2"))
t0 = time.time()
push.drain_once()
check("real: a push service slower than the timeout counts as a network failure (one retry queued, no hang)",
      [r[4] for r in outbox("Z2")] == [1] and time.time() - t0 < 8)
push.TIMEOUT_S = 10
redir = Sub(ORIGIN + "/p/redir-" + b64u(os.urandom(12)))
subscribe("Z2", redir)
db.execute("DELETE FROM push_out")
db.commit()
CAP.clear()
send(wraps=("Z1", "Z2"))
push.drain_once()
check("real: a 307 from the push service is NOT followed (no request to the redirect target; one retry queued)",
      not any(c[0] == "/p/stolen-by-redirect" for c in CAP) and [r[4] for r in outbox("Z2")] == [1])
subscribe("Z2", slow)

# --- 15. the worker thread drains on its own; the log stays clean
db.execute("DELETE FROM push_out")
db.commit()
CAP.clear()
push.WORKER.start()
send(wraps=("Z1", "Z2"))
for _ in range(50):
    if any(c[0].startswith("/p/z1real-") for c in CAP):
        break
    time.sleep(0.2)
check("worker thread: a send turning ready reaches the mock within ~2 s, without a request waiting on it",
      any(c[0].startswith("/p/z1real-") for c in CAP))
push.WORKER.stop()
log = (T / "intake.log").read_text(encoding="utf-8", errors="replace")
leaks = [w for w in ("j@example", "z@example", "m@example", "Kitchen", "Jason", "Casey", VRAW, "/fcm/send/", "/p/z1real",
                     real.endpoint, b64u(real.auth)) if w in log]
check("log: no emails, names, labels, VAPID key, endpoint paths or auth secrets", not leaks)
check("log: push lines name only the push service host", "push sent kind=new" in log and "via=fcm.googleapis.com" in log)
if leaks:
    print("   ", leaks)

# --- 16. earlier steps still answer
r = C.get("/relay/api/inbox", headers=H("casey", D["Z1"]))
check("Step 3 inbox still answers", r.status_code == 200)
check("Step 1 devices still answer", C.get("/devices/api/mine", headers=H("casey")).status_code == 200)

srv.shutdown()
print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
