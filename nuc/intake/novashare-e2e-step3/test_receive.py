"""test_receive.py - server proof for receive.py (Novashare E2E Step 3, 2026-11-24). Self-contained: run INSIDE the
patched image (SWAP-PATCH.sh does exactly this; no extra packages, the server never decrypts):

    docker run --rm --entrypoint python IMAGE test_receive.py

Fake people (example.invalid), temp DB / relay folder / log, INTAKE_MAX_MB=64. Wraps and pieces are random bytes:
the server never opens them (the real crypto round trip is test_receive_browser.py, two Chrome profiles).
Checks: the guard on every new route; another person's device cannot list, accept, decline, complete, fetch a wrap
or a piece; the sender device cannot fetch pieces; nothing is listed before the send is ready; pieces only after
accept, byte for byte, range-checked; every state edge (repeat = 200 already, illegal = 409); 8 parallel accepts move
it once; decline deletes the wrap and access; complete deletes the wrap (replay of a used wrap = 404, pieces 410);
the last delivery to finish deletes the transfer and its ciphertext file; a revoked device does not keep a transfer
alive; triggers; the backfill of deliveries for Step 2 transfers; the sender's status list; page CSP + SRI; the
service worker headers; no names or emails in the log; Step 2's routes still answer. Last line: ALL PASS / SOME FAILED.
Variables: fails, check(), H(), T (temp dir), D (device ids by name).
"""
import base64
import hashlib
import os
import pathlib
import re
import sqlite3
import sys
import tempfile
import threading

T = pathlib.Path(tempfile.mkdtemp(prefix="receivetest-"))
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
os.environ.update(DROP_CONFIG=str(T / "drop.toml"), INTAKE_KEY="k-test", INTAKE_TOKEN="t-test",
                  INTAKE_ALLOWED_PEERS="127.0.0.1", INTAKE_STAGE=str(T / "stage"), INTAKE_UNSORTED=str(T / "unsorted"),
                  INTAKE_LOG=str(T / "intake.log"), INTAKE_DEVICES_DB=str(T / "devices.db"),
                  INTAKE_RELAY_DIR=str(T / "relay"), INTAKE_MAX_MB="64", INTAKE_RESERVE_MB="1")
for k in ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL"):
    os.environ.setdefault(k, "http://10.255.255.1")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import app  # noqa: E402
import devices  # noqa: E402
import receive  # noqa: E402
import relay  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    fails += not ok


C = TestClient(app.app, client=("127.0.0.1", 50000))
EMAIL = {"jason": "j@example.invalid", "casey": "z@example.invalid", "mom": "m@example.invalid"}
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
for who, name in (("jason", "J1"), ("casey", "Z1"), ("casey", "Z2"), ("mom", "M1")):
    D[name], FP[name] = reg(who, name)
WHO = {"J1": "jason", "Z1": "casey", "Z2": "casey", "M1": "mom"}


def code(r):
    try:
        return r.json().get("code")
    except ValueError:
        return None


def send(plain, to=("casey",), wraps=("Z1", "Z2"), finish=True):
    """A Step 2 send from J1 with random ciphertext; returns (id, pieces as bytes)."""
    pieces = max(1, -(-plain // relay.PLAIN_PIECE))
    size_ct = plain + 16 * pieces
    tid = b64u(os.urandom(16))
    body = {"id": tid, "alg": "novashare-v1", "to": list(to), "size_ct": size_ct, "pieces": pieces,
            "manifest": b64u(os.urandom(12 + 256 + 16)),
            "wraps": [{"device_id": D[w], "device_fp": FP[w], "eph_pub": b64u(os.urandom(32)),
                       "kem_ct": b64u(os.urandom(1088)), "wrapped_key": b64u(os.urandom(60))} for w in wraps]}
    r = C.post("/relay/api/transfers", headers=H("jason", D["J1"]), json=body)
    assert r.status_code == 201, r.text
    P = [os.urandom(relay.piece_len(size_ct, pieces, i)) for i in range(pieces)]
    if finish:
        for i, p in enumerate(P):
            h = {**H("jason", D["J1"]), "X-Piece-Sha256": hashlib.sha256(p).hexdigest()}
            assert C.put(f"/relay/api/transfers/{tid}/pieces/{i}", headers=h, content=p).status_code == 200
    return tid, P


def act(tid, action, dev):
    return C.post(f"/relay/api/transfers/{tid}/{action}", headers=H(WHO.get(dev, "casey"), D.get(dev, dev)))


def piece(tid, n, dev):
    return C.get(f"/relay/api/transfers/{tid}/pieces/{n}", headers=H(WHO.get(dev, "casey"), D.get(dev, dev)))


def inbox(dev):
    return C.get("/relay/api/inbox", headers=H(WHO[dev], D[dev]))


def env(tid, dev):
    return C.get(f"/relay/api/transfers/{tid}/envelope", headers=H(WHO[dev], D[dev]))


db = sqlite3.connect(os.environ["INTAKE_DEVICES_DB"])


def dstate(tid, dev):
    r = db.execute("SELECT state FROM delivery WHERE transfer_id=? AND device_id=?", (tid, D[dev])).fetchone()
    return r and r[0]


def tstate(tid):
    return db.execute("SELECT state FROM transfer WHERE id=?", (tid,)).fetchone()[0]


def wraps(tid):
    return db.execute("SELECT count(*) FROM wrap WHERE transfer_id=?", (tid,)).fetchone()[0]


# --- 1. the guard on every new API route
probe, _ = send(10, finish=False)
routes = [("GET", "/relay/api/inbox"), ("GET", "/relay/api/sent"), ("POST", f"/relay/api/transfers/{probe}/accept"),
          ("POST", f"/relay/api/transfers/{probe}/decline"), ("POST", f"/relay/api/transfers/{probe}/complete"),
          ("GET", f"/relay/api/transfers/{probe}/pieces/0")]
cases = {"no Sec-Fetch-Site": H("casey", D["Z1"], Sec_Fetch_Site=None), "cross-site": H("casey", D["Z1"], Sec_Fetch_Site="cross-site"),
         "same-site": H("casey", D["Z1"], Sec_Fetch_Site="same-site"), "no X-Novashare": H("casey", D["Z1"], X_Novashare=None),
         "view level": H("casey", D["Z1"], X_Auth_Level="view"), "unmapped email": H("x@example.invalid", D["Z1"]),
         "drop key only": {"X-Drop-Key": "k-test", "Sec-Fetch-Site": "same-origin", "X-Novashare": "1", "X-Device-Id": D["Z1"]}}
for name, h in cases.items():
    codes = [C.request(m, p, headers=h).status_code for m, p in routes]
    check(f"guard: {name} -> 403 on inbox, sent, accept, decline, complete, piece {codes}", codes == [403] * 6)
for dev_hdr, label in ((None, "no X-Device-Id"), (D["J1"], "casey naming jason's device")):
    codes = [C.request(m, p, headers=H("casey", dev_hdr)).status_code for m, p in routes]
    check(f"device check: {label} -> 403 on every route {codes}", codes == [403] * 6)

# --- 2. nothing is listed or accepted before the send is ready
check("a send still uploading is not in the inbox", probe not in inbox("Z1").text)
r = act(probe, "accept", "Z1")
check("accept while uploading -> 409 not_ready", r.status_code == 409 and code(r) == "not_ready")

# --- 3. a ready send: who sees it
TID, P = send(2 * relay.PLAIN_PIECE + 777)        # 3 pieces
DATA = relay.data_path(TID)
it = [x for x in inbox("Z1").json()["items"] if x["id"] == TID]
check("Z1 inbox lists it: pending, open, sender Jason / J1, plaintext size, no name or manifest",
      len(it) == 1 and it[0]["state"] == "pending" and it[0]["open"] and it[0]["sender"]["name"] == "Jason"
      and it[0]["sender"]["label"] == "J1" and it[0]["size"] == 2 * relay.PLAIN_PIECE + 777 and "manifest" not in it[0])
check("Z2 (casey's other device) lists it too", TID in inbox("Z2").text)
check("mom's device does not list it", TID not in inbox("M1").text)
check("the sender's device does not list it as incoming", TID not in inbox("J1").text)
for a in ("accept", "decline", "complete"):
    r = act(TID, a, "M1")
    check(f"mom's device: {a} -> 404 not_found (existence not confirmed)", r.status_code == 404 and code(r) == "not_found")
r = piece(TID, 0, "M1")
check("mom's device: piece -> 404", r.status_code == 404)
check("mom's device: envelope (the wrap) -> 404", env(TID, "M1").status_code == 404)
r = C.get(f"/relay/api/transfers/{TID}/pieces/0", headers=H("jason", D["J1"]))
check("the sender's device cannot download the pieces -> 404", r.status_code == 404)
check("an id that does not exist answers the same 404", act(b64u(os.urandom(16)), "accept", "Z1").status_code == 404)
check("a malformed id -> 404", act("x" * 5, "accept", "Z1").status_code == 404)

# --- 4. pending: no pieces
r = piece(TID, 0, "Z1")
check("piece before accept -> 409 not_accepted", r.status_code == 409 and code(r) == "not_accepted")
r = act(TID, "complete", "Z1")
check("complete before accept -> 409 not_accepted", r.status_code == 409 and code(r) == "not_accepted")

# --- 5. accept, pieces
r = act(TID, "accept", "Z1")
check("Z1 accept -> 200 accepted", r.status_code == 200 and r.json()["state"] == "accepted")
r = act(TID, "accept", "Z1")
check("Z1 accept again -> 200 already", r.status_code == 200 and r.json().get("already") is True)
got = [piece(TID, i, "Z1") for i in range(3)]
check("Z1 gets every piece, byte for byte, no-store, octet-stream",
      all(g.status_code == 200 and g.content == P[i] for i, g in enumerate(got))
      and got[0].headers.get("cache-control") == "no-store" and got[0].headers["content-type"] == "application/octet-stream")
check("piece past the end -> 416", piece(TID, 3, "Z1").status_code == 416)
check("negative piece -> 404/416/422", piece(TID, -1, "Z1").status_code in (404, 416, 422))
r = piece(TID, 0, "Z2")
check("Z2 (still pending) gets no piece -> 409 not_accepted", code(r) == "not_accepted")
check("mom's device still gets nothing after casey accepted -> 404", piece(TID, 0, "M1").status_code == 404)

# 8 parallel accepts of one delivery: exactly one moves it
out = []
bar = threading.Barrier(8)


def go():
    bar.wait()
    try:
        out.append(receive.do_move("casey", D["Z2"], TID, "accept"))
    except devices.Refuse as e:
        out.append(e.code)

ths = [threading.Thread(target=go) for _ in range(8)]
[t.start() for t in ths]
[t.join() for t in ths]
moved = [o for o in out if isinstance(o, dict) and not o.get("already")]
check(f"8 parallel accepts from Z2: exactly one moved it, 7 'already' ({len(moved)} moved)",
      len(moved) == 1 and sum(1 for o in out if isinstance(o, dict) and o.get("already")) == 7 and dstate(TID, "Z2") == "accepted")

# --- 6. decline removes access
r = act(TID, "decline", "Z2")
check("Z2 decline (from accepted) -> 200 declined", r.status_code == 200 and r.json()["state"] == "declined")
check("decline deleted Z2's wrap (Z1's is still there)", wraps(TID) == 1
      and db.execute("SELECT count(*) FROM wrap WHERE transfer_id=? AND device_id=?", (TID, D["Z2"])).fetchone()[0] == 0)
check("Z2 envelope after decline -> 404", env(TID, "Z2").status_code == 404)
r = piece(TID, 0, "Z2")
check("Z2 piece after decline -> 410 gone", r.status_code == 410 and code(r) == "gone")
r = act(TID, "accept", "Z2")
check("Z2 accept after decline -> 409 wrong_state", r.status_code == 409 and code(r) == "wrong_state")
check("Z2 complete after decline -> 409 wrong_state", code(act(TID, "complete", "Z2")) == "wrong_state")
check("Z2 decline again -> 200 already", act(TID, "decline", "Z2").json().get("already") is True)
check("transfer still ready and its file still there (Z1 has not finished)", tstate(TID) == "ready" and DATA.exists())

# --- 7. the sender's status
s = C.get("/relay/api/sent", headers=H("jason", D["J1"])).json()
mine = {x["id"]: x for x in s["items"]}
dl = {d["device_id"]: d["state"] for d in mine.get(TID, {}).get("deliveries", [])}
check("sender status: Z1 accepted, Z2 declined, labels and names given", dl == {D["Z1"]: "accepted", D["Z2"]: "declined"}
      and {d["label"] for d in mine[TID]["deliveries"]} == {"Z1", "Z2"} and {d["name"] for d in mine[TID]["deliveries"]} == {"Casey"})
check("casey's own sent list does not show jason's send", TID not in C.get("/relay/api/sent", headers=H("casey", D["Z1"])).text)

# --- 8. complete: mark received; the last delivery deletes the transfer and its ciphertext
r = act(TID, "complete", "Z1")
check("Z1 complete -> 200 downloaded", r.status_code == 200 and r.json()["state"] == "downloaded")
check("after the last delivery: transfer 'deleted', no wraps, no piece rows, ciphertext file and folder gone",
      tstate(TID) == "deleted" and wraps(TID) == 0 and not DATA.exists() and not DATA.parent.exists()
      and db.execute("SELECT count(*) FROM piece WHERE transfer_id=?", (TID,)).fetchone()[0] == 0)
check("replay of the used wrap: Z1 envelope -> 404", env(TID, "Z1").status_code == 404)
r = piece(TID, 0, "Z1")
check("Z1 piece after complete -> 410 gone", r.status_code == 410)
check("Z1 complete again -> 200 already", act(TID, "complete", "Z1").json().get("already") is True)
check("Z1 accept after complete -> 409 wrong_state", code(act(TID, "accept", "Z1")) == "wrong_state")
check("Z1 decline after complete -> 409 wrong_state", code(act(TID, "decline", "Z1")) == "wrong_state")
it = {x["id"]: x for x in inbox("Z1").json()["items"]}
check("inbox keeps it under earlier: downloaded, not open", it[TID]["state"] == "downloaded" and not it[TID]["open"])
s = {x["id"]: x for x in C.get("/relay/api/sent", headers=H("jason", D["J1"])).json()["items"]}
check("sender status: transfer finished (deleted), Z1 downloaded", s[TID]["state"] == "deleted"
      and {d["device_id"]: d["state"] for d in s[TID]["deliveries"]}[D["Z1"]] == "downloaded")

# --- 9. decline by everyone deletes too
T2, _ = send(100)
act(T2, "decline", "Z1")
check("one of two declined: still ready", tstate(T2) == "ready" and relay.data_path(T2).exists())
act(T2, "decline", "Z2")
check("both declined: deleted, file gone", tstate(T2) == "deleted" and not relay.data_path(T2).exists())

# --- 10. a removed device does not keep a transfer alive
T3, _ = send(100)
C.post(f"/devices/api/mine/{D['Z2']}/revoke", headers=H("casey"))
check("revoked Z2: its inbox -> 403", inbox("Z2").status_code == 403)
act(T3, "accept", "Z1")
act(T3, "complete", "Z1")
check("Z1 done, Z2 revoked (still pending): transfer deleted, Z2's delivery marked declined, file gone",
      tstate(T3) == "deleted" and dstate(T3, "Z2") == "declined" and wraps(T3) == 0 and not relay.data_path(T3).exists())

# --- 11. a file missing under an accepted delivery -> 410, not a 500
T4, _ = send(100, wraps=("Z1",))
act(T4, "accept", "Z1")
relay._drop_files(relay.data_path(T4))
r = piece(T4, 0, "Z1")
check("ciphertext file missing -> 410 gone", r.status_code == 410 and code(r) == "gone")

# --- 12. triggers and the backfill
for name, sql, args in (("downloaded -> pending", "UPDATE delivery SET state='pending' WHERE transfer_id=?", (TID,)),
                        ("accepted -> pending", "UPDATE delivery SET state='pending' WHERE transfer_id=? AND device_id=?", (T4, D["Z1"])),
                        ("move a delivery to another device", "UPDATE delivery SET device_id=? WHERE transfer_id=?", ("A" * 22, T4)),
                        ("a state outside the list", "UPDATE delivery SET state='lost' WHERE transfer_id=?", (T4,))):
    try:
        db.execute(sql, args)
        db.commit()
        check(f"trigger/CHECK refuses: {name}", False)
    except sqlite3.DatabaseError:
        db.rollback()
        check(f"trigger/CHECK refuses: {name}", True)
T5, _ = send(100, wraps=("Z1",))
db.execute("DELETE FROM delivery WHERE transfer_id=?", (T5,))       # as if sent by Step 2 before this table existed
db.commit()
relay._READY.clear()
relay._db().close()
check("backfill: a Step 2 wrap with no delivery gets a pending one on first use", dstate(T5, "Z1") == "pending")
check("backfill does not revive a finished delivery", dstate(TID, "Z1") == "downloaded")

# --- 13. a DB failure is a 503 with a stable code
real = relay._my_device


def locked(*a):
    raise sqlite3.OperationalError("database is locked")

relay._my_device = locked
r = inbox("Z1")
relay._my_device = real
check("DB error during inbox -> 503 relay_unavailable", r.status_code == 503 and code(r) == "relay_unavailable")

# --- 14. pages, worker, log, the rest
pg = C.get("/relay/inbox", headers=H("casey", Sec_Fetch_Site="none", X_Novashare=None))
csp = pg.headers.get("content-security-policy", "")
check("inbox page: 200, strict CSP with Trusted Types", pg.status_code == 200 and "script-src 'self'" in csp
      and "unsafe" not in csp and "require-trusted-types-for 'script'" in csp)
check("inbox page: no inline script, style or handlers", not re.search(r"<script(?![^>]*\bsrc=)", pg.text)
      and not re.search(r"\son[a-z]+=", pg.text) and "<style" not in pg.text and " style=" not in pg.text)
for src, sri in re.findall(r'(?:src|href)="(/(?:devices|relay)/[^"]+)" integrity="([^"]+)"', pg.text):
    bb = C.get(src, headers=H("casey", X_Novashare=None)).content
    check(f"SRI matches the served bytes: {src}", sri == "sha384-" + base64.b64encode(hashlib.sha384(bb).digest()).decode())
check("inbox page: view-only -> 403", C.get("/relay/inbox", headers=H("casey", X_Auth_Level="view")).status_code == 403)
sw = C.get("/relay/sw.js", headers=H("casey", X_Novashare=None))
check("service worker: JS, no-cache, scope header, CSP, only /relay/dl/ handled",
      sw.status_code == 200 and "javascript" in sw.headers["content-type"] and sw.headers.get("service-worker-allowed") == "/relay/"
      and "require-trusted-types-for" in sw.headers.get("content-security-policy", "") and "/relay/dl/" in sw.text)
check("service worker: signed-out (view level) -> 403", C.get("/relay/sw.js", headers=H("casey", X_Auth_Level="view")).status_code == 403)
check("unknown inbox file -> 404", C.get("/relay/in/x.js", headers=H("casey", X_Novashare=None)).status_code == 404)
sp = C.get("/relay/send", headers=H(Sec_Fetch_Site="none", X_Novashare=None))
check("send page has the Sent list and links to the inbox", 'id=sent' in sp.text and 'href="/relay/inbox"' in sp.text)
log = pathlib.Path(os.environ["INTAKE_LOG"]).read_text()
check("log has accepted / declined / downloaded lines", f"relay accepted t={TID[:8]}" in log and f"relay declined t={TID[:8]}" in log
      and f"relay downloaded t={TID[:8]}" in log)
check("no email address and no full transfer id in the log", "@" not in log and TID not in log)
check("Step 2 routes still answer (status of a finished send from the sending device)",
      C.get(f"/relay/api/transfers/{TID}", headers=H("jason", D["J1"])).status_code == 200)
check("existing routes still there (/up/start, /share, /healthz, /devices, /relay/send)",
      {"/up/start", "/share", "/healthz", "/devices", "/relay/send"} <= {getattr(rt, "path", "") for rt in app.app.routes})
db.close()
print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
