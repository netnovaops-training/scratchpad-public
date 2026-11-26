"""test_relay.py - server proof for relay.py (Novashare E2E Step 2, 2026-11-24). Self-contained: run INSIDE the
patched image (SWAP-PATCH.sh does exactly this; no extra packages needed, the server never decrypts):

    docker run --rm --entrypoint python IMAGE test_relay.py

Fake people (example.invalid), temp DB / relay folder / log, INTAKE_MAX_MB=64 so sizes stay small. The crypto round
trip, tamper detection, crash + resume of a real server and the 2 GB grep are in test_relay_e2e.py.
Checks: importing app creates nothing; the guard on every route; device ownership (X-Device-Id); create validation
and the exact-device-set rule; replay = resumed, other body = id_taken; pieces (checksum, short, long, range,
conflict, any order, retries); atomic completion; the file holds exactly the pieces; only the sender device sees the
status and sends pieces; only a recipient device gets ITS OWN wrap; quotas (count, bytes, relay total, disk) also
under concurrent creates; one writer per piece under concurrent PUTs; a piece on disk without its row (a crash) is
asked for again; ENOSPC leaves no row and no file; schema triggers; no names or emails in the log; page CSP + SRI.
Last line: "ALL PASS" or "SOME FAILED".
Variables: fails, check(), H(), T (temp dir), D (device ids by name).
"""
import asyncio
import base64
import errno
import hashlib
import os
import pathlib
import random
import re
import sqlite3
import subprocess
import sys
import tempfile
import threading

T = pathlib.Path(tempfile.mkdtemp(prefix="relaytest-"))
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
id = "ann"
name = "Ann"
emails = ["a@example.invalid"]
""")
os.environ.update(DROP_CONFIG=str(T / "drop.toml"), INTAKE_KEY="k-test", INTAKE_TOKEN="t-test",
                  INTAKE_ALLOWED_PEERS="127.0.0.1", INTAKE_STAGE=str(T / "stage"), INTAKE_UNSORTED=str(T / "unsorted"),
                  INTAKE_LOG=str(T / "intake.log"), INTAKE_DEVICES_DB=str(T / "devices.db"),
                  INTAKE_RELAY_DIR=str(T / "relay"), INTAKE_MAX_MB="64", INTAKE_RESERVE_MB="1")
for k in ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL"):
    os.environ.setdefault(k, "http://10.255.255.1")
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    fails += not ok


# --- 0. importing app (a second process, as rescan_held does hourly) creates no DB and no relay folder
probe_db, probe_dir = T / "probe.db", T / "probe-relay"
r = subprocess.run([sys.executable, "-c", "import app"], capture_output=True, text=True, cwd=str(HERE),
                   env={**os.environ, "INTAKE_DEVICES_DB": str(probe_db), "INTAKE_RELAY_DIR": str(probe_dir)})
check("importing app creates no DB and no relay folder", r.returncode == 0 and not probe_db.exists() and not probe_dir.exists())

import app  # noqa: E402
import devices  # noqa: E402
import relay  # noqa: E402
import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

C = TestClient(app.app, client=("127.0.0.1", 50000))
EMAIL = {"jason": "j@example.invalid", "casey": "z@example.invalid", "mom": "m@example.invalid", "ann": "a@example.invalid"}
CT = relay.CT_PIECE


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
for who, name in (("jason", "J1"), ("jason", "J2"), ("casey", "Z1"), ("casey", "Z2"), ("mom", "M1")):
    D[name], FP[name] = reg(who, name)


def wrap(name):
    return {"device_id": D[name], "device_fp": FP[name], "eph_pub": b64u(os.urandom(32)),
            "kem_ct": b64u(os.urandom(1088)), "wrapped_key": b64u(os.urandom(60))}


def new_id():
    return b64u(os.urandom(16))


def body(plain=3 * 1048576 // 2 + CT * 0, to=("casey", "jason"), wraps=("Z1", "Z2", "J2"), tid=None, man=None):
    """A create body for `plain` plaintext bytes (pieces of 4 MiB, each ciphertext piece +16)."""
    pieces = max(1, -(-plain // relay.PLAIN_PIECE))
    return {"id": tid or new_id(), "alg": "novashare-v1", "to": list(to), "size_ct": plain + 16 * pieces,
            "pieces": pieces, "manifest": man or b64u(os.urandom(12 + 256 + 16)), "wraps": [wrap(w) for w in wraps]}


def create(b, who="jason", dev="J1"):
    return C.post("/relay/api/transfers", headers=H(who, D.get(dev, dev)), json=b)


def put(tid, n, data, who="jason", dev="J1", sha=None):
    h = H(who, D.get(dev, dev))
    if sha is not False:
        h["X-Piece-Sha256"] = sha or hashlib.sha256(data).hexdigest()
    return C.put(f"/relay/api/transfers/{tid}/pieces/{n}", headers=h, content=data)


def code(r):
    try:
        return r.json().get("code")
    except ValueError:
        return None


# --- 1. the guard, on every relay API route
probe = body()
routes = [("POST", "/relay/api/transfers", probe), ("GET", f"/relay/api/transfers/{probe['id']}", None),
          ("PUT", f"/relay/api/transfers/{probe['id']}/pieces/0", None), ("GET", f"/relay/api/transfers/{probe['id']}/envelope", None)]
cases = {"no Sec-Fetch-Site": H(dev=D["J1"], Sec_Fetch_Site=None), "cross-site": H(dev=D["J1"], Sec_Fetch_Site="cross-site"),
         "same-site": H(dev=D["J1"], Sec_Fetch_Site="same-site"), "no X-Novashare": H(dev=D["J1"], X_Novashare=None),
         "view level": H(dev=D["J1"], X_Auth_Level="view"), "unmapped email": H("x@example.invalid", D["J1"]),
         "drop key only": {"X-Drop-Key": "k-test", "Sec-Fetch-Site": "same-origin", "X-Novashare": "1", "X-Device-Id": D["J1"]}}
for name, h in cases.items():
    codes = []
    for m, path, js in routes:
        codes.append(C.request(m, path, headers={**h, "X-Piece-Sha256": "0" * 64}, json=js).status_code)
    check(f"guard: {name} -> 403 on create, status, piece and envelope {codes}", codes == [403] * 4)

# --- 2. device ownership
r = create(body(), dev=None)
check("create without X-Device-Id -> 403 not_your_device", r.status_code == 403 and code(r) == "not_your_device")
r = create(body(), dev="Z1")
check("jason naming casey's device -> 403 not_your_device", r.status_code == 403 and code(r) == "not_your_device")

# --- 3. create validation
bad = {
    "id not 22 base64url": dict(id="x" * 21), "alg": dict(alg="aes"), "pieces off by one": dict(pieces=2),
    "size_ct not splittable (last piece < 16)": dict(size_ct=CT + 5, pieces=2),
    "manifest not padded to 256": dict(manifest=b64u(os.urandom(300))), "manifest too small": dict(manifest=b64u(os.urandom(100))),
    "manifest padded base64": dict(manifest=b64u(os.urandom(284)) + "="), "to empty": dict(to=[]), "to twice": dict(to=["casey", "casey"]),
}
for name, over in bad.items():
    r = create({**body(), **over})
    check(f"refused: {name} -> 400", r.status_code == 400)
b = body()
b["wraps"].append(dict(b["wraps"][0]))
check("refused: a device wrapped twice -> 400", create(b).status_code == 400)
b = body()
b["wraps"][0]["kem_ct"] = b64u(os.urandom(1087))
r = create(b)
check("refused: ML-KEM ciphertext 1087 bytes -> 400 bad_key", r.status_code == 400 and code(r) == "bad_key")
b = body()
b["wraps"][0]["eph_pub"] = b64u(bytes(32))
check("refused: small-order ephemeral X25519 -> 400 bad_key", code(create(b)) == "bad_key")
r = create(body(to=["casey", "stranger"]))
check("refused: someone outside the household -> 400", r.status_code == 400)
r = create(body(to=["ann"], wraps=["Z1"]))
check("refused: a person with no device (ann) -> 422 no_devices", r.status_code == 422 and code(r) == "no_devices")
r = create(body(wraps=("Z1", "J2")))
check("a live device left out (Z2) -> 409 devices_changed", r.status_code == 409 and code(r) == "devices_changed")
r = create(body(wraps=("Z1", "Z2", "J2", "M1")))
check("a device of someone not named (mom) -> 409 devices_changed", code(r) == "devices_changed")
r = create(body(wraps=("Z1", "Z2", "J2", "J1")))
check("a wrap for the sending device itself -> 409 devices_changed", code(r) == "devices_changed")
b = body()
b["wraps"][0]["device_fp"] = "0" * 64
check("a stale fingerprint (key changed since the sender looked) -> 409 devices_changed", code(create(b)) == "devices_changed")
r = create(body(plain=64 * 1048576 + 1))
check("over INTAKE_MAX_MB (64 MB here) -> 413 too_big", r.status_code == 413 and code(r) == "too_big")
check("exactly INTAKE_MAX_MB -> accepted", create(body(plain=64 * 1048576)).status_code == 201)
r = C.post("/relay/api/transfers", headers={**H("jason", D["J1"]), "Content-Type": "application/json"}, content=b"[" * 300000)
check("a 300 KB create body -> 413", r.status_code == 413)

# --- 4. a good create, replay, id reuse
PLAIN = 2 * relay.PLAIN_PIECE + 12345                   # 3 pieces: two full, one short
B = body(plain=PLAIN)
TID = B["id"]
r = create(B)
check("create -> 201 uploading, 3 pieces, all missing", r.status_code == 201 and r.json()["pieces"] == 3 and r.json()["missing"] == [0, 1, 2])
data_p = relay.data_path(TID)
check("ciphertext file made at full size (fallocate), mode 600 in a 700 folder",
      data_p.stat().st_size == B["size_ct"] and oct(data_p.stat().st_mode & 0o777) == "0o600"
      and oct(data_p.parent.stat().st_mode & 0o777) == "0o700")
check("ciphertext lives under INTAKE_RELAY_DIR/<id>/data, nowhere near the staging folder",
      data_p == pathlib.Path(os.environ["INTAKE_RELAY_DIR"]) / TID / "data" and not any((T / "stage").glob("*")))
r = create(B)
check("the same create again -> 200 resumed with the missing list", r.status_code == 200 and r.json()["resumed"] is True and r.json()["missing"] == [0, 1, 2])
r = create({**body(plain=PLAIN), "id": TID})
check("same id, other body -> 409 id_taken", r.status_code == 409 and code(r) == "id_taken")
r = create({**B, "to": ["jason"], "wraps": [wrap("J1")]}, who="casey", dev="Z1")
check("same id from another person -> 409 id_taken (nothing about the first send leaks)", code(r) == "id_taken" and "jason" not in r.text)
r = create(B, dev="J2")
check("same id + body from the sender's OTHER device -> 409 id_taken", code(r) == "id_taken")

# --- 5. status: only the sending device
check("status from the sending device -> 200", C.get(f"/relay/api/transfers/{TID}", headers=H("jason", D["J1"])).status_code == 200)
for who, dev in (("jason", "J2"), ("casey", "Z1")):
    r = C.get(f"/relay/api/transfers/{TID}", headers=H(who, D[dev]))
    check(f"status from {dev} -> 404 not_found", r.status_code == 404 and code(r) == "not_found")

# --- 6. pieces
P = [os.urandom(CT), os.urandom(CT), os.urandom(B["size_ct"] - 2 * CT)]
r = put(TID, 0, P[0], sha=False)
check("piece without X-Piece-Sha256 -> 428", r.status_code == 428)
r = put(TID, 0, P[0], sha="0" * 64)
check("piece with a wrong checksum -> 422 checksum_mismatch", r.status_code == 422 and code(r) == "checksum_mismatch")
r = put(TID, 0, P[0][:-1])
check("piece cut short -> 400 piece_short", r.status_code == 400 and code(r) == "piece_short")
r = put(TID, 2, P[2] + b"x")
check("piece longer than its place -> 413", r.status_code == 413)
check("piece number past the end -> 416", put(TID, 3, P[2]).status_code == 416)
check("negative piece number -> 416 (or 404 from the router)", put(TID, -1, P[0]).status_code in (404, 416, 422))
for who, dev in (("casey", "Z1"), ("jason", "J2")):
    r = put(TID, 0, P[0], who=who, dev=dev)
    check(f"piece from {dev} (not the sending device) -> 404", r.status_code == 404)
r = C.get(f"/relay/api/transfers/{TID}/envelope", headers=H("casey", D["Z1"]))
check("envelope before the send is complete -> 409 not_ready", r.status_code == 409 and code(r) == "not_ready")
r = put(TID, 2, P[2])
check("last piece first (any order) -> 200, still uploading", r.status_code == 200 and r.json()["state"] == "uploading")
r = put(TID, 2, P[2])
check("same piece again -> 200 already", r.status_code == 200 and r.json().get("already") is True)
r = put(TID, 2, os.urandom(len(P[2])))
check("same piece number, other bytes -> 409 piece_conflict", r.status_code == 409 and code(r) == "piece_conflict")
check("status lists 0 and 1 missing", C.get(f"/relay/api/transfers/{TID}", headers=H("jason", D["J1"])).json()["missing"] == [0, 1])

# a crash between the disk write and the row: the bytes are there, the row is not -> still missing, sent again
relay._write_piece(TID, 0, P[0])
check("piece on disk without its row (a crash) is still reported missing",
      C.get(f"/relay/api/transfers/{TID}", headers=H("jason", D["J1"])).json()["missing"] == [0, 1])
check("piece 0 -> 200", put(TID, 0, P[0]).status_code == 200)
r = put(TID, 1, P[1])
check("final piece -> state ready, received 3 of 3", r.status_code == 200 and r.json()["state"] == "ready" and r.json()["received"] == 3)
check("after ready, a retry of a piece -> 200 already", put(TID, 1, P[1]).json().get("already") is True)
check("the file holds exactly the three pieces in place", data_p.read_bytes() == b"".join(P))
db = sqlite3.connect(os.environ["INTAKE_DEVICES_DB"])
st, ready_at, n = db.execute("SELECT state, ready_at, (SELECT count(*) FROM piece WHERE transfer_id=?) FROM transfer WHERE id=?",
                             (TID, TID)).fetchone()
check("DB: ready with ready_at and exactly 3 piece rows (atomic completion)", st == "ready" and ready_at and n == 3)

# --- 7. envelope: a recipient device gets ITS OWN wrap only
sent = {w["device_id"]: w for w in B["wraps"]}
for who, dev in (("casey", "Z1"), ("casey", "Z2"), ("jason", "J2")):
    r = C.get(f"/relay/api/transfers/{TID}/envelope", headers=H(who, D[dev]))
    e = r.json() if r.status_code == 200 else {}
    w = e.get("wrap", {})
    check(f"envelope for {dev}: 200 with its own wrap, byte for byte", r.status_code == 200 and w.get("device_id") == D[dev]
          and all(w.get(k) == sent[D[dev]][k] for k in ("eph_pub", "kem_ct", "wrapped_key")))
    check(f"envelope for {dev}: no other device's wrap anywhere in it",
          not any(sent[o]["wrapped_key"] in r.text for o in sent if o != D[dev]))
check("envelope carries the manifest and the sender device's public key", e.get("manifest") == B["manifest"]
      and e["sender"]["device_id"] == D["J1"] and len(e["sender"]["x25519_pub"]) == 43)
for who, dev, want in (("jason", "J1", 404), ("mom", "M1", 404), ("casey", "M1", 403), ("casey", None, 403)):
    r = C.get(f"/relay/api/transfers/{TID}/envelope", headers=H(who, D.get(dev) if dev else None))
    check(f"envelope for {who} with device {dev}: {want}", r.status_code == want)
r = C.get(f"/relay/api/transfers/{new_id()}/envelope", headers=H("casey", D["Z1"]))
check("envelope of an id that does not exist -> the same 404", r.status_code == 404 and code(r) == "not_found")
C.post(f"/devices/api/mine/{D['Z2']}/revoke", headers=H("casey"))
r = C.get(f"/relay/api/transfers/{TID}/envelope", headers=H("casey", D["Z2"]))
check("a revoked device gets no envelope -> 403", r.status_code == 403)
r = create(body(wraps=("Z1", "Z2", "J2")))
check("after the revoke, wrapping for the revoked device -> 409 devices_changed", code(r) == "devices_changed")
WR = ("Z1", "J2")

# --- 8. concurrent PUTs of one piece: one writer, one row; the rest already/busy
B2 = body(plain=relay.PLAIN_PIECE + 10, wraps=WR)
check("second create -> 201", create(B2).status_code == 201)
T2 = B2["id"]
p0 = os.urandom(CT)


async def burst():
    tr = httpx.ASGITransport(app=app.app, client=("127.0.0.1", 50001))
    async with httpx.AsyncClient(transport=tr, base_url="http://t") as ac:
        h = {**H("jason", D["J1"]), "X-Piece-Sha256": hashlib.sha256(p0).hexdigest()}
        return await asyncio.gather(*[ac.put(f"/relay/api/transfers/{T2}/pieces/0", headers=h, content=p0) for _ in range(8)])

res = asyncio.run(burst())
okish = [r.status_code == 200 or code(r) == "busy" for r in res]
rows = db.execute("SELECT count(*) FROM piece WHERE transfer_id=? AND idx=0", (T2,)).fetchone()[0]
check(f"8 parallel PUTs of piece 0: all 200 or 409 busy, exactly one row ({[r.status_code for r in res]})", all(okish) and rows == 1)

# --- 9. quotas, including concurrent creates
r = create(body(wraps=WR), dev="J1")
os.environ["INTAKE_RELAY_MAX_OPEN"] = str(db.execute("SELECT count(*) FROM transfer WHERE sender_person='jason' AND state IN ('uploading','ready')").fetchone()[0])
r = create(body(wraps=WR))
check("over the open-send count -> 429 quota_count", r.status_code == 429 and code(r) == "quota_count")
os.environ["INTAKE_RELAY_MAX_OPEN"] = "1000"
held = db.execute("SELECT sum(size_ct) FROM transfer WHERE sender_person='jason' AND state IN ('uploading','ready')").fetchone()[0]
os.environ["INTAKE_RELAY_OPEN_MB"] = str(-(-held // 1048576) + 5)    # room for one more 4 MB send, not two
r1 = create(body(plain=4 * 1048576, wraps=WR))
r2 = create(body(plain=4 * 1048576, wraps=WR))
check("open-bytes cap: one more fits, the next -> 429 quota_bytes", r1.status_code == 201 and code(r2) == "quota_bytes")
check("casey's own budget is separate", create({**body(to=["jason"], wraps=()), "wraps": [wrap("J1"), wrap("J2")]}, who="casey", dev="Z1").status_code == 201)

# concurrent: 6 threads race for room for exactly 3 sends of 4 MB
held = db.execute("SELECT sum(size_ct) FROM transfer WHERE sender_person='jason' AND state IN ('uploading','ready')").fetchone()[0]
os.environ["INTAKE_RELAY_OPEN_MB"] = str(held // 1048576 + 13)
out = []
bodies = [body(plain=4 * 1048576, wraps=WR) for _ in range(6)]
start = threading.Barrier(6)


def go(b):
    start.wait()
    try:
        out.append(relay.do_create("jason", D["J1"], b)[0])
    except devices.Refuse as e:
        out.append(e.code)

ths = [threading.Thread(target=go, args=(b,)) for b in bodies]
[t.start() for t in ths]
[t.join() for t in ths]
check(f"6 concurrent creates, room for 3: exactly 3 made, 3 quota_bytes ({sorted(map(str, out))})",
      out.count(201) == 3 and out.count("quota_bytes") == 3)
os.environ["INTAKE_RELAY_OPEN_MB"] = "100000"
os.environ["INTAKE_RELAY_CAP_MB"] = "1"
r = create(body(wraps=WR))
check("relay total cap -> 507 relay_full", r.status_code == 507 and code(r) == "relay_full")
os.environ["INTAKE_RELAY_CAP_MB"] = "51200"
real_reserve = relay.reserve
relay.reserve = lambda: 1 << 60
r = create(body(wraps=WR))
check("free space under the reserve -> 507 no_space", r.status_code == 507 and code(r) == "no_space")
relay.reserve = real_reserve

# ENOSPC from fallocate itself: no row and no folder left behind
real_fa = os.posix_fallocate


def full(fd, off, ln):
    raise OSError(errno.ENOSPC, "No space left on device")

os.posix_fallocate = full
bf = body(wraps=WR)
r = create(bf)
os.posix_fallocate = real_fa
gone = db.execute("SELECT count(*) FROM transfer WHERE id=?", (bf["id"],)).fetchone()[0] == 0
check("ENOSPC at fallocate -> 507, no transfer row, no wraps, no folder", r.status_code == 507 and gone
      and db.execute("SELECT count(*) FROM wrap WHERE transfer_id=?", (bf["id"],)).fetchone()[0] == 0
      and not (pathlib.Path(os.environ["INTAKE_RELAY_DIR"]) / bf["id"]).exists())

# --- 10. schema triggers
for name, sql in {"change size_ct": "UPDATE transfer SET size_ct=size_ct+1 WHERE id=?",
                  "change the manifest": "UPDATE transfer SET manifest=randomblob(284) WHERE id=?",
                  "ready -> uploading": "UPDATE transfer SET state='uploading' WHERE id=?"}.items():
    try:
        db.execute(sql, (TID,))
        db.commit()
        check(f"trigger refuses: {name}", False)
    except sqlite3.DatabaseError:
        db.rollback()
        check(f"trigger refuses: {name}", True)
try:
    db.execute("INSERT INTO transfer(id,sender_person,sender_device,body_hash,alg,size_ct,piece_ct,pieces,manifest,state,created,"
               "last_activity,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
               ("C" * 22, "jason", D["J1"], "0" * 64, "novashare-v1", CT + 16, CT, 1, os.urandom(284), "uploading", 1, 1, 1))
    db.commit()
    check("CHECK refuses a wrong piece count in the table itself", False)
except sqlite3.DatabaseError:
    db.rollback()
    check("CHECK refuses a wrong piece count in the table itself", True)
db.close()

# --- 10b. a DB failure mid-read is a 503 with a stable code, not a bare 500 (audit 2026-11-24)
real_missing = relay._missing


def locked(*a):
    raise sqlite3.OperationalError("database is locked")

relay._missing = locked
r = C.get(f"/relay/api/transfers/{T2}", headers=H("jason", D["J1"]))
relay._missing = real_missing
check("DB error during status -> 503 relay_unavailable", r.status_code == 503 and code(r) == "relay_unavailable")

# --- 11. the page, the log, the rest of the app
pg = C.get("/relay/send", headers=H(Sec_Fetch_Site="none", X_Novashare=None))
csp = pg.headers.get("content-security-policy", "")
check("send page: 200, strict CSP with Trusted Types", pg.status_code == 200 and "script-src 'self'" in csp
      and "unsafe" not in csp and "require-trusted-types-for 'script'" in csp)
check("send page: no inline script or handlers", not re.search(r"<script(?![^>]*\bsrc=)", pg.text) and not re.search(r"\son[a-z]+=", pg.text))
for src, sri in re.findall(r'(?:src|href)="(/(?:devices|relay)/static/[^"]+)" integrity="([^"]+)"', pg.text):
    bb = C.get(src, headers=H(X_Novashare=None)).content
    check(f"SRI matches the served bytes: {src.rsplit('/', 1)[1]}", sri == "sha384-" + base64.b64encode(hashlib.sha384(bb).digest()).decode())
check("send page: view-only -> 403", C.get("/relay/send", headers=H(X_Auth_Level="view")).status_code == 403)
check("unknown relay static file -> 404", C.get("/relay/static/x.js", headers=H(X_Novashare=None)).status_code == 404)
check("API answers are not cached", C.get(f"/relay/api/transfers/{TID}", headers=H("jason", D["J1"])).headers.get("cache-control") == "no-store")
log = pathlib.Path(os.environ["INTAKE_LOG"]).read_text()
check("log has create and ready lines", f"relay create t={TID[:8]}" in log and f"relay ready t={TID[:8]}" in log)
check("no email address and no full transfer id in the log", "@" not in log and TID not in log)
check("existing routes still there (/up/start, /share, /healthz, /devices)",
      {"/up/start", "/share", "/healthz", "/devices"} <= {getattr(rt, "path", "") for rt in app.app.routes})
check("healthz untouched by the relay", C.get("/healthz").status_code in (200, 503) and "relay" not in C.get("/healthz").text)

print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
