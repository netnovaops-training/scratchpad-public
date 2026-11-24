"""test_push_e2e.py - Novashare E2E Step 4 sandbox end-to-end (2026-11-24). Run by push_sbx.sh e2e INSIDE the running
sandbox container (docker exec), against the real uvicorn server (lifespan -> push worker thread) on 127.0.0.1:8100,
with a mock push service (mock_push.py) in its own container at MOCK. Fake people (example.invalid), throwaway VAPID key.

    python test_push_e2e.py http://sbx-novashare-push-mock:8099

Flow: three devices register (Jason J1; Casey Z1, Z2). Z1 and J1 subscribe with REAL browser-like keys (P-256 + auth) to
the mock; Z2 to a "gone" endpoint (the mock answers 410), later to a "flaky" one (503 once). Jason sends to Casey. Then:
  - the mock captured Z1's push; it is decrypted HERE with Z1's private key and auth secret (RFC 8291 aes128gcm) and must
    be exactly {v:1, kind:new, id:<transfer id>, body:"You have a new Novashare item"}: no names, labels, sizes;
  - the VAPID header verifies (ES256 over the served public key, aud = the mock's origin);
  - Z2's 410 deleted its subscription; the flaky endpoint got one retry and then the push;
  - Casey saves on Z1 -> J1 gets a 'delivered' push (decrypted: result saved);
  - revoking Z1 deletes its subscription.
Last line: ALL PASS / SOME FAILED.
"""
import base64
import hashlib
import json
import os
import re
import sqlite3
import sys
import time

import httpx
import http_ece
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature

MOCK = sys.argv[1].rstrip("/")
BASE = "http://127.0.0.1:8100"
EMAIL = {"jason": "jason@example.invalid", "casey": "casey@example.invalid"}
fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    fails += not ok


def b64u(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def H(who, dev=None):
    h = {"X-Auth-Email": EMAIL[who], "X-Auth-Level": "full", "Sec-Fetch-Site": "same-origin", "X-Novashare": "1"}
    if dev:
        h["X-Device-Id"] = dev
    return h


c = httpx.Client(base_url=BASE, timeout=30)


def mlkem_pub():
    import random
    co = [random.randrange(3329) for _ in range(768)]
    out = bytearray()
    for i in range(0, 768, 2):
        a, b = co[i], co[i + 1]
        out += bytes([a & 0xFF, (a >> 8) | ((b & 0x0F) << 4), b >> 4])
    return bytes(out) + os.urandom(32)


D, FP, WHO = {}, {}, {"J1": "jason", "Z1": "casey", "Z2": "casey"}
for name, who in WHO.items():
    r = c.post("/devices/api/register", headers=H(who), json={"label": name + " Kitchen tablet",
                                                              "x25519_pub": b64u(os.urandom(32)), "mlkem_pub": b64u(mlkem_pub())})
    assert r.status_code == 201, r.text
    D[name], FP[name] = r.json()["device_id"], r.json()["fingerprint"]


class Sub:
    def __init__(self, name):
        self.priv = ec.generate_private_key(ec.SECP256R1())
        self.pub = self.priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
        self.auth = os.urandom(16)
        self.name = name + "-" + b64u(os.urandom(9))
        self.endpoint = MOCK + "/p/" + self.name

    def body(self):
        return {"endpoint": self.endpoint, "keys": {"p256dh": b64u(self.pub), "auth": b64u(self.auth)}}

    def captured(self, want=1, wait=20):
        for _ in range(int(wait * 5)):
            got = httpx.get(MOCK + "/captured/" + self.name, timeout=5).json()
            if len(got) >= want:
                return got
            time.sleep(0.2)
        return httpx.get(MOCK + "/captured/" + self.name, timeout=5).json()

    def open(self, cap):
        return json.loads(http_ece.decrypt(base64.b64decode(cap["body"]), private_key=self.priv, auth_secret=self.auth,
                                           version="aes128gcm"))


key = c.get("/relay/api/push/key", headers=H("casey")).json()
check("server: push is on with the sandbox key", key["enabled"] is True and len(b64d(key["public_key"])) == 65)

z1, j1, z2 = Sub("z1"), Sub("j1"), Sub("gone")
for dev, s in (("Z1", z1), ("J1", j1), ("Z2", z2)):
    r = c.put("/relay/api/push/sub", headers=H(WHO[dev], D[dev]), json=s.body())
    check(f"{dev} subscribes to the mock push service", r.status_code == 200)

# Jason sends something to Casey (random ciphertext: the server never decrypts; the push only needs the transfer)
size_ct, pieces = 4096 + 16, 1
tid = b64u(os.urandom(16))
body = {"id": tid, "alg": "novashare-v1", "to": ["casey"], "size_ct": size_ct, "pieces": pieces,
        "manifest": b64u(os.urandom(12 + 256 + 16)),
        "wraps": [{"device_id": D[w], "device_fp": FP[w], "eph_pub": b64u(os.urandom(32)), "kem_ct": b64u(os.urandom(1088)),
                   "wrapped_key": b64u(os.urandom(60))} for w in ("Z1", "Z2")]}
check("Jason creates the send", c.post("/relay/api/transfers", headers=H("jason", D["J1"]), json=body).status_code == 201)
piece = os.urandom(size_ct)
t0 = time.time()
r = c.put(f"/relay/api/transfers/{tid}/pieces/0", headers={**H("jason", D["J1"]), "X-Piece-Sha256": hashlib.sha256(piece).hexdigest()},
          content=piece)
check("the last piece makes it ready, and the upload answer did not wait on the push (< 2 s)",
      r.json().get("state") == "ready" and time.time() - t0 < 2)

cap = z1.captured()
check("the mock push service received exactly one push for Z1", len(cap) == 1)
pl = z1.open(cap[0]) if cap else {}
check("decrypted with Z1's keys: exactly {v, kind:new, id, body}", pl == {"v": 1, "kind": "new", "id": tid,
                                                                          "body": "You have a new Novashare item"})
raw = json.dumps({k: v for k, v in pl.items() if k != "id"})   # the id is random base64url (checked equal above)
check("decrypted payload has no names, emails, labels, sizes or file details",
      not any(w in raw for w in ("Jason", "jason", "Casey", "casey", "example.invalid", "Kitchen", "tablet", str(size_ct), "4096")))
hd = {k.lower(): v for k, v in cap[0]["headers"].items()} if cap else {}
check("headers: aes128gcm, TTL, Urgency high", hd.get("content-encoding") == "aes128gcm" and hd.get("ttl") == "86400"
      and hd.get("urgency") == "high")
m = re.fullmatch(r"vapid t=([A-Za-z0-9_.-]+), ?k=([A-Za-z0-9_-]+)", hd.get("authorization", ""))
ok = False
if m:
    h64, c64, s64 = m.group(1).split(".")
    sig, claims = b64d(s64), json.loads(b64d(c64))
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b64d(m.group(2))).verify(
            encode_dss_signature(int.from_bytes(sig[:32], "big"), int.from_bytes(sig[32:], "big")),
            (h64 + "." + c64).encode(), ec.ECDSA(hashes.SHA256()))
        ok = m.group(2) == key["public_key"] and claims["aud"] == MOCK and claims["exp"] > time.time()
    except Exception:  # noqa: BLE001
        ok = False
check("VAPID: ES256 JWT verifies against the served public key; aud = the push service origin", ok)

time.sleep(1)
st = c.get("/relay/api/push/sub", headers=H("casey", D["Z2"])).json()
check("Z2's endpoint answered 410: its subscription was deleted", z2.captured() and st["subscribed"] is False)

flaky = Sub("flaky")
c.put("/relay/api/push/sub", headers=H("casey", D["Z2"]), json=flaky.body())
c.post("/relay/api/push/test", headers=H("casey", D["Z2"]))
got = flaky.captured(want=2, wait=50)
check("flaky endpoint: 503 first, ONE retry about 30 s later, then delivered", len(got) == 2)
check("the retried push decrypts to the test payload", len(got) == 2 and flaky.open(got[1]) ==
      {"v": 1, "kind": "test", "body": "Notifications work on this device"})

# Casey saves it on Z1 -> Jason's J1 hears 'delivered'
c.post(f"/relay/api/transfers/{tid}/accept", headers=H("casey", D["Z1"]))
c.post(f"/relay/api/transfers/{tid}/complete", headers=H("casey", D["Z1"]))
capj = j1.captured()
check("Casey saved on Z1: Jason's J1 got one push", len(capj) == 1)
check("decrypted: {kind:delivered, id, result:saved} and no names",
      capj and j1.open(capj[0]) == {"v": 1, "kind": "delivered", "id": tid, "body": "Your Novashare item was delivered",
                                    "result": "saved"})

# revoke deletes the subscription
c.post(f"/devices/api/mine/{D['Z1']}/revoke", headers=H("casey"))
import devices  # noqa: E402  (this container's own DB path; read-only look)
db = sqlite3.connect(f"file:{devices.db_path()}?mode=ro", uri=True)
check("revoke: Z1's subscription is gone from the database", db.execute("SELECT count(*) FROM push_sub WHERE device_id=?",
                                                                       (D["Z1"],)).fetchone()[0] == 0)
check("the database holds no names in push tables", db.execute("SELECT count(*) FROM push_out").fetchone()[0] >= 0
      and not db.execute("SELECT 1 FROM pragma_table_info('push_out') WHERE name IN ('name','label','email','caption')").fetchone())
print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
