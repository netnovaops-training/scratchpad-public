"""test_sweep.py - server proof for Novashare E2E Step 6 (sweep.py, audit.py, and the Step 6 changes in relay.py,
receive.py, push.py, devices.py), 2026-11-24. Self-contained: run INSIDE the patched image (SWAP-PATCH.sh does this):

    docker run --rm --entrypoint python IMAGE test_sweep.py

Fake people (example.invalid), temp DB / relay folder / audit folder / log. Checks, in order:
  quotas and rate under concurrency (10 parallel creates near each limit: bytes, count, relay total, rate; exactly
  the allowed number pass and the sums never exceed the cap; a resumed create is not counted);
  the sweep with a faked clock (7-day expiry, 6-hour abandoned upload, skip while a piece is read, all receiving
  devices removed -> finished, a lost file, purge at 24 h, cancelled kept until 7 days, orphan folders with no row
  after 1 h and with a finished row at once, a non-id entry left alone, symlinks removed without being followed,
  orphan rows), quota released after expiry;
  idempotence (two sweeps at once with the lease bypassed = the same end state; a held lease skips; a crash between
  commit and unlink, in-process and as a killed process, is finished by the next run);
  sender cancel (guard, someone else's send = 404, from another own device, uploading and ready, wraps + pieces + file
  gone, repeat = already, piece write after = refused, a download cut off = 410, recipients' inbox "cancelled", a
  'withdrawn' push only to subscribed waiting devices with no names, finished send = 409);
  the push_out migration (old CHECK -> 'withdrawn', rows and the Step 4 triggers kept, idempotent);
  the audit log (shape, hashed device ids, no names / emails / labels / full ids, mode 600 / 700, rotation and
  pruning, admin-only reader); Jason's Relay line and stats (admin only); the worker under the app lifespan;
  the log has no emails or person ids. Last line: ALL PASS / SOME FAILED.
Variables: fails, check(), H(), T (temp dir), D / FP (device ids and fingerprints by name).
"""
import base64
import hashlib
import json
import os
import pathlib
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time

T = pathlib.Path(tempfile.mkdtemp(prefix="sweeptest-"))
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
ENV = dict(DROP_CONFIG=str(T / "drop.toml"), INTAKE_KEY="k-test", INTAKE_TOKEN="t-test",
           INTAKE_ALLOWED_PEERS="127.0.0.1", INTAKE_STAGE=str(T / "stage"), INTAKE_UNSORTED=str(T / "unsorted"),
           INTAKE_LOG=str(T / "intake.log"), INTAKE_DEVICES_DB=str(T / "devices.db"),
           INTAKE_RELAY_DIR=str(T / "relay"), INTAKE_RELAY_AUDIT_DIR=str(T / "audit"), INTAKE_MAX_MB="64",
           INTAKE_RESERVE_MB="1", INTAKE_RELAY_SWEEP_FIRST_S="1", INTAKE_RELAY_SWEEP_S="3600",
           LLM_BASE="http://10.255.255.1", AUTH_LEVELS_URL="http://10.255.255.1", GOTENBERG_URL="http://10.255.255.1")
os.environ.update(ENV)
HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import app  # noqa: E402
import audit  # noqa: E402
import push  # noqa: E402
import relay  # noqa: E402
import sweep  # noqa: E402
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


LABELS = {}


def reg(who, label):
    lab = f"{label}-label-canary"
    r = C.post("/devices/api/register", headers=H(who), json={"label": lab, "x25519_pub": b64u(os.urandom(32)),
                                                              "mlkem_pub": b64u(mlkem_pub())})
    assert r.status_code == 201, r.text
    LABELS[label] = lab
    return r.json()["device_id"], r.json()["fingerprint"]


D, FP = {}, {}
for who, name in (("jason", "J1"), ("jason", "J2"), ("casey", "Z1"), ("casey", "Z2"), ("mom", "M1")):
    D[name], FP[name] = reg(who, name)
OWNER = {"J1": "jason", "J2": "jason", "Z1": "casey", "Z2": "casey", "M1": "mom"}


def code(r):
    try:
        return r.json().get("code")
    except ValueError:
        return None


def body(to=("casey",), wraps=("Z1", "Z2"), plain=1000, tid=None):
    pieces = max(1, -(-plain // relay.PLAIN_PIECE))
    return {"id": tid or b64u(os.urandom(16)), "alg": "novashare-v1", "to": list(to), "size_ct": plain + 16 * pieces,
            "pieces": pieces, "manifest": b64u(os.urandom(12 + 256 + 16)),
            "wraps": [{"device_id": D[w], "device_fp": FP[w], "eph_pub": b64u(os.urandom(32)),
                       "kem_ct": b64u(os.urandom(1088)), "wrapped_key": b64u(os.urandom(60))} for w in wraps]}


def create(b, dev="J1"):
    return C.post("/relay/api/transfers", headers=H(OWNER[dev], D[dev]), json=b)


def put(b, n, dev="J1"):
    ln = relay.piece_len(b["size_ct"], b["pieces"], n)
    data = os.urandom(ln)
    return C.put(f"/relay/api/transfers/{b['id']}/pieces/{n}", content=data,
                 headers={**H(OWNER[dev], D[dev]), "X-Piece-Sha256": hashlib.sha256(data).hexdigest(),
                          "Content-Type": "application/octet-stream"})


def ready(dev="J1", **kw):
    b = body(**kw)
    r = create(b, dev)
    assert r.status_code == 201, r.text
    for n in range(b["pieces"]):
        assert put(b, n, dev).status_code == 200
    return b


def db():
    c = sqlite3.connect(T / "devices.db")
    c.row_factory = sqlite3.Row
    return c


def q(sql, *a):
    c = db()
    try:
        return c.execute(sql, a).fetchall()
    finally:
        c.close()


def tstate(tid):
    r = q("SELECT state FROM transfer WHERE id=?", tid)
    return r[0][0] if r else None


def fexists(tid):
    return (T / "relay" / tid).exists()


def set_times(tid, **cols):
    c = db()
    c.execute("PRAGMA foreign_keys=ON")
    for k, v in cols.items():
        c.execute(f"UPDATE transfer SET {k}=? WHERE id=?", (v, tid))   # test only: k from this file, never input
    c.commit()
    c.close()


def clear_open():
    """Every open send -> cancelled (test reset between quota rounds), files removed."""
    for r in q("SELECT id, sender_person, sender_device FROM transfer WHERE state IN ('uploading','ready')"):
        sweep.do_cancel(r["sender_person"], r["sender_device"], r["id"])


def env(**kv):
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = str(v)


def parallel(fn, n=10):
    """n threads start fn(i) at the same moment; returns the results (status, code) in index order."""
    bar = threading.Barrier(n)
    out = [None] * n

    def run(i):
        bar.wait()
        try:
            st, data = fn(i)
            out[i] = (st, data.get("id"))
        except relay.Refuse as e:
            out[i] = (e.status, e.code)
    th = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in th:
        t.start()
    for t in th:
        t.join()
    return out


# ====================================================================== 1. quotas + rate under concurrency
MB = 1048576
env(INTAKE_RELAY_OPEN_MB=30)
bs = [body(to=("casey",), wraps=("Z1", "Z2"), plain=5 * MB) for _ in range(10)]
res = parallel(lambda i: relay.do_create("mom", D["M1"], bs[i]))
ok = [r for r in res if r[0] == 201]
held = q("SELECT coalesce(sum(size_ct),0) FROM transfer WHERE sender_person='mom' AND state IN ('uploading','ready')")[0][0]
check(f"10 parallel 5 MB creates against a 30 MB per-person cap: exactly 5 pass ({len(ok)}), the rest quota_bytes",
      len(ok) == 5 and sorted(r[1] for r in res if r[0] != 201) == ["quota_bytes"] * 5)
check(f"per-person bytes held never above the cap ({held} <= {30 * MB})", held <= 30 * MB)
check("a file exists exactly for each send that passed (no file for a refused create)",
      sorted(p.name for p in (T / "relay").iterdir()) == sorted(r[1] for r in ok))
clear_open()
env(INTAKE_RELAY_OPEN_MB=None, INTAKE_RELAY_MAX_OPEN=3)
bs = [body(plain=1000) for _ in range(10)]
res = parallel(lambda i: relay.do_create("mom", D["M1"], bs[i]))
check(f"10 parallel creates against a 3-open-sends cap: exactly 3 pass, 7 quota_count {[r[0] for r in res]}",
      sum(r[0] == 201 for r in res) == 3 and sum(r[1] == "quota_count" for r in res) == 7)
clear_open()
env(INTAKE_RELAY_MAX_OPEN=None, INTAKE_RELAY_CAP_MB=20)
bs = [body(plain=4 * MB) for _ in range(5)] + [body(to=("jason",), wraps=("J1", "J2"), plain=4 * MB) for _ in range(5)]
res = parallel(lambda i: relay.do_create("mom" if i < 5 else "casey", D["M1"] if i < 5 else D["Z1"], bs[i]))
tot = q("SELECT coalesce(sum(size_ct),0) FROM transfer WHERE state IN ('uploading','ready')")[0][0]
check(f"two people, 10 parallel 4 MB creates against a 20 MB relay total: exactly 4 pass, the rest relay_full ({tot})",
      sum(r[0] == 201 for r in res) == 4 and sum(r[1] == "relay_full" for r in res) == 6 and tot <= 20 * MB)
clear_open()
env(INTAKE_RELAY_CAP_MB=None, INTAKE_RELAY_CREATES_PER_MIN=4)
c0 = q("SELECT count(*) FROM transfer WHERE sender_person='mom' AND created>?", int(time.time()) - 60)[0][0]
env(INTAKE_RELAY_CREATES_PER_MIN=4 + c0)
bs = [body(plain=1000) for _ in range(10)]
res = parallel(lambda i: relay.do_create("mom", D["M1"], bs[i]))
check(f"10 parallel creates against 4 per minute: exactly 4 pass, 6 rate_limited {[r[1] for r in res if r[0] != 201]}",
      sum(r[0] == 201 for r in res) == 4 and sum(r[1] == "rate_limited" for r in res) == 6)
first = next(bs[i] for i, r in enumerate(res) if r[0] == 201)
r = C.post("/relay/api/transfers", headers=H("mom", D["M1"]), json=first)
check("a resumed create (same id + body) is not counted against the rate: 200 resumed",
      r.status_code == 200 and r.json().get("resumed"))
r = C.post("/relay/api/transfers", headers=H("mom", D["M1"]), json=body(plain=1000))
check("over the rate over HTTP: 429 rate_limited with words", r.status_code == 429 and code(r) == "rate_limited"
      and "minute" in r.json()["error"])
r = C.post("/relay/api/transfers", headers=H("casey", D["Z1"]), json=body(to=("jason",), wraps=("J1", "J2")))
check("the rate is per person: Casey still sends", r.status_code == 201)
env(INTAKE_RELAY_CREATES_PER_MIN=None, INTAKE_RELAY_CREATES_PER_HOUR=1)
r = C.post("/relay/api/transfers", headers=H("casey", D["Z1"]), json=body(to=("jason",), wraps=("J1", "J2")))
check("the hourly rate holds too (1 per hour: the second is rate_limited)", code(r) == "rate_limited")
env(INTAKE_RELAY_CREATES_PER_HOUR=1000, INTAKE_RELAY_CREATES_PER_MIN=1000)   # the rest of the test sends a lot
clear_open()

# ====================================================================== 2. the sweep with a faked clock
now = int(time.time())
b = ready()
t1 = b["id"]
ru = q("SELECT expires_at, created FROM transfer WHERE id=?", t1)[0]
check("create sets expires_at = created + 7 days", ru["expires_at"] - ru["created"] == 7 * 86400)
out = sweep.sweep_once(now=ru["expires_at"] - 1)
check("one second before 7 days: still ready, file kept", tstate(t1) == "ready" and fexists(t1) and out["expired"] == 0)
with relay.using(t1):
    out = sweep.sweep_once(now=ru["expires_at"])
check("at 7 days while a piece read runs: skipped (busy=1), still ready", tstate(t1) == "ready" and out["busy"] == 1)
out = sweep.sweep_once(now=ru["expires_at"])
check("at 7 days: expired, wraps and pieces gone, file and folder removed",
      tstate(t1) == "expired" and out["expired"] == 1 and not fexists(t1)
      and not q("SELECT 1 FROM wrap WHERE transfer_id=?", t1) and not q("SELECT 1 FROM piece WHERE transfer_id=?", t1))
inbox = C.get("/relay/api/inbox", headers=H("casey", D["Z1"])).json()["items"]
it = next((x for x in inbox if x["id"] == t1), None)
check("Casey's inbox: the expired send shows 'gone', not open", it is not None and it["state"] == "gone" and not it["open"])
out2 = sweep.sweep_once(now=ru["expires_at"])
check("a second run changes nothing (idempotent)", tstate(t1) == "expired" and out2["expired"] == 0 and out2["errors"] == 0)
out = sweep.sweep_once(now=ru["expires_at"] + 86400)
check("24 h after expiry: the row is purged with its deliveries",
      tstate(t1) is None and not q("SELECT 1 FROM delivery WHERE transfer_id=?", t1) and out["purged"] >= 1)

# abandoned upload: 2 pieces, one sent
b = body(plain=relay.PLAIN_PIECE + 100)
create(b)
put(b, 0)
t2 = b["id"]
la = q("SELECT last_activity FROM transfer WHERE id=?", t2)[0][0]
info0 = C.get("/relay/api/pick", headers=H("jason", D["J1"])).json()["limits"]["open_left_sends"]
sweep.sweep_once(now=la + 5 * 3600)
check("an upload idle 5 h: still uploading", tstate(t2) == "uploading" and fexists(t2))
out = sweep.sweep_once(now=la + 6 * 3600)
info1 = C.get("/relay/api/pick", headers=H("jason", D["J1"])).json()["limits"]["open_left_sends"]
check("an upload with no piece for 6 h: expired as abandoned, file gone", tstate(t2) == "expired"
      and out["abandoned"] == 1 and not fexists(t2))
check(f"its quota is released (open sends left {info0} -> {info1})", info1 == info0 + 1)
r = put(b, 1)
check("a late piece for the abandoned upload is refused (409)", r.status_code == 409)

# all receiving devices removed -> finished
b = ready(to=("mom",), wraps=("M1",))
t3 = b["id"]
c = db()
c.execute("UPDATE device SET revoked=? WHERE id=?", (int(time.time()), D["M1"]))
c.commit()
c.close()
out = sweep.sweep_once()
check("a ready send whose only receiving device was removed: finished (deleted), file gone",
      tstate(t3) == "deleted" and out["finished"] == 1 and not fexists(t3))
D["M1x"] = D["M1"]
D["M1"], FP["M1"] = reg("mom", "M1b")
OWNER["M1b"] = "mom"

# lost file
b = ready()
t4 = b["id"]
os.unlink(T / "relay" / t4 / "data")
out = sweep.sweep_once()
check("a ready row whose file is missing: marked deleted, logged at ERROR", tstate(t4) == "deleted" and out["lost"] == 1
      and "was ready but its file is missing" in (T / "intake.log").read_text())

# orphans
orph = b64u(os.urandom(16))
(T / "relay" / orph).mkdir()
(T / "relay" / orph / "data").write_bytes(b"x" * 100)
(T / "relay" / "not-a-transfer").mkdir()
out = sweep.sweep_once()
check("an id-named folder with no row, just made: kept (a create may be committing)", (T / "relay" / orph).exists())
out = sweep.sweep_once(now=int(time.time()) + 3601)
check("the same folder 1 h later: removed as an orphan", not (T / "relay" / orph).exists() and out["orphan_dirs"] >= 1)
check("a folder not named like a transfer id: left alone and counted", (T / "relay" / "not-a-transfer").is_dir()
      and out["unknown"] >= 1)
os.rmdir(T / "relay" / "not-a-transfer")
outside = T / "outside"
outside.mkdir()
(outside / "keep.txt").write_text("keep")
sl1 = b64u(os.urandom(16))
(T / "relay" / sl1).mkdir()
os.symlink(outside / "keep.txt", T / "relay" / sl1 / "data")
sl2 = b64u(os.urandom(16))
os.symlink(outside, T / "relay" / sl2)
sweep.sweep_once(now=int(time.time()) + 3601)
check("symlinks are removed without being followed (the outside file and folder are untouched)",
      not os.path.lexists(T / "relay" / sl1) and not os.path.lexists(T / "relay" / sl2)
      and (outside / "keep.txt").read_text() == "keep")
c = sqlite3.connect(T / "devices.db")
c.execute("PRAGMA foreign_keys=OFF")
ghost = b64u(os.urandom(16))
c.execute("INSERT INTO piece(transfer_id, idx, sha256) VALUES(?,0,?)", (ghost, os.urandom(32)))
c.commit()
c.close()
out = sweep.sweep_once()
check("orphan rows (a piece with no transfer) are deleted", out["orphan_rows"] >= 1
      and not q("SELECT 1 FROM piece WHERE transfer_id=?", ghost))

# idempotence: two sweeps at once (lease bypassed), then a held lease
ids = [ready()["id"] for _ in range(6)]
for t in ids:
    set_times(t, expires_at=1)
res = [None, None]


def run(i):
    res[i] = sweep.sweep_once(force=True)


th = [threading.Thread(target=run, args=(i,)) for i in range(2)]
for t in th:
    t.start()
for t in th:
    t.join()
check(f"two sweeps at once: each of 6 sends expired exactly once ({res[0]['expired']} + {res[1]['expired']}), no errors",
      res[0]["expired"] + res[1]["expired"] == 6 and res[0]["errors"] == res[1]["errors"] == 0
      and all(tstate(t) == "expired" and not fexists(t) for t in ids))
c = db()
c.execute("INSERT INTO relay_meta(k,v) VALUES('sweep_lease',?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
          (f"other|{int(time.time()) + 300}",))
c.commit()
c.close()
check("a sweep while another holds the lease: skipped", sweep.sweep_once() == {"skipped": "lease"})
check("the lease runs out on its own (5 min): the next sweep runs", "expired" in sweep.sweep_once(now=int(time.time()) + 301))

# crash between commit and unlink, in-process
b = ready()
t5 = b["id"]
set_times(t5, expires_at=1)


def boom(tid):
    raise RuntimeError("simulated crash after commit")


sweep._after_commit = boom
try:
    sweep.sweep_once()
    crashed = False
except RuntimeError:
    crashed = True
sweep._after_commit = None
check("crash after the expiry commit: the state stands (expired), the file is still there",
      crashed and tstate(t5) == "expired" and fexists(t5))
out = sweep.sweep_once()
check("the next run removes the left file (finished row = orphan at once), counts it", not fexists(t5)
      and out["orphan_dirs"] >= 1)

# crash as a killed process (restart): a child process expires and is killed with os._exit before the unlink
b = ready()
t6 = b["id"]
set_times(t6, expires_at=1)
child = subprocess.run([sys.executable, "-c", "import os,sys;sys.path.insert(0,'.');import app,sweep;"
                        "sweep._after_commit=lambda t: os._exit(9);sweep.sweep_once()"],
                       cwd=str(HERE), env={**os.environ}, capture_output=True, text=True)
lease = q("SELECT v FROM relay_meta WHERE k='sweep_lease'")
check(f"killed process: exit 9, row expired, file left, its lease left behind ({child.returncode})",
      child.returncode == 9 and tstate(t6) == "expired" and fexists(t6) and len(lease) == 1)
check("restart: a sweep inside the dead lease's 5 min is skipped", sweep.sweep_once() == {"skipped": "lease"})
out = sweep.sweep_once(now=int(time.time()) + 301)
check("restart: after the lease runs out the next sweep finishes the job (file gone, no errors)",
      not fexists(t6) and out["errors"] == 0)

# ====================================================================== 3. sender cancel
b = body(plain=relay.PLAIN_PIECE + 100)
create(b)
put(b, 0)
tc = b["id"]
URL = f"/relay/api/transfers/{tc}/cancel"
check("cancel: needs the request guard (no X-Novashare = 403)",
      C.post(URL, headers=H("jason", D["J1"], X_Novashare=None)).status_code == 403)
check("cancel: view-only level = 403", C.post(URL, headers=H("jason", D["J1"], X_Auth_Level="view")).status_code == 403)
check("cancel: needs one of my live devices (403 not_your_device)",
      code(C.post(URL, headers=H("jason", D["Z1"]))) == "not_your_device")
r = C.post(URL, headers=H("casey", D["Z1"]))
check("cancel: someone else's send answers 404 like a missing one", r.status_code == 404 and code(r) == "not_found")
check("cancel: an unknown or malformed id = 404", C.post("/relay/api/transfers/" + "A" * 22 + "/cancel",
                                                         headers=H("jason", D["J1"])).status_code == 404
      and C.post("/relay/api/transfers/..%2f..%2fx/cancel", headers=H("jason", D["J1"])).status_code == 404)
before = C.get("/relay/api/pick", headers=H("jason", D["J1"])).json()["limits"]["open_left_sends"]
r = C.post(URL, headers=H("jason", D["J2"]))
check("cancel an uploading send from my OTHER device: 200 cancelled", r.status_code == 200
      and r.json()["state"] == "cancelled")
check("cancel: wraps and pieces deleted, ciphertext file and folder removed", tstate(tc) == "cancelled"
      and not q("SELECT 1 FROM wrap WHERE transfer_id=?", tc) and not q("SELECT 1 FROM piece WHERE transfer_id=?", tc)
      and not fexists(tc))
after = C.get("/relay/api/pick", headers=H("jason", D["J1"])).json()["limits"]["open_left_sends"]
check(f"cancel releases the quota ({before} -> {after})", after == before + 1)
r = C.post(URL, headers=H("jason", D["J1"]))
check("cancel again: 200 already", r.status_code == 200 and r.json().get("already"))
check("a piece after the cancel: refused (409)", put(b, 1).status_code == 409)
check("a send cancelled while uploading never shows in the recipients' inbox",
      all(x["id"] != tc for x in C.get("/relay/api/inbox", headers=H("casey", D["Z1"])).json()["items"]))

# cancel a ready send: Z1 subscribed (push), Z2 not; Z1 accepted and is downloading
push.load_key()
c = db()
c.execute("INSERT INTO push_sub(device_id,person,endpoint,p256dh,auth,kid,created,updated) VALUES(?,?,?,?,?,?,?,?)",
          (D["Z1"], "casey", "https://fcm.googleapis.com/fcm/send/test-" + os.urandom(4).hex(), b"\x04" + os.urandom(64),
           os.urandom(16), "0" * 16, 1, 1))
c.commit()
c.close()
b = ready()
tr = b["id"]
check("Z1 accepts the ready send", C.post(f"/relay/api/transfers/{tr}/accept", headers=H("casey", D["Z1"])).status_code == 200)
c = db()
c.execute("DELETE FROM push_out")
c.commit()
c.close()
r = C.post(f"/relay/api/transfers/{tr}/cancel", headers=H("jason", D["J1"]))
check("cancel a ready send: 200, waiting = 2 devices", r.status_code == 200 and r.json().get("waiting") == 2)
po = q("SELECT device_id, kind, transfer_id FROM push_out")
check(f"a 'withdrawn' push queued for Z1 (subscribed) only, in the same transaction {len(po)}",
      [(p["device_id"], p["kind"], p["transfer_id"]) for p in po] == [(D["Z1"], "withdrawn", tr)])
pl = push.payload("withdrawn", tr)
check("the withdrawn payload is kind, id and fixed text only (no names)",
      json.loads(pl) == {"v": 1, "kind": "withdrawn", "id": tr, "body": "A Novashare item was withdrawn"})
r = C.get(f"/relay/api/transfers/{tr}/pieces/0", headers=H("casey", D["Z1"]))
check("Z1's download after the cancel: the next piece answers 410 gone", r.status_code == 410 and code(r) == "gone")
it = {x["id"]: x for x in C.get("/relay/api/inbox", headers=H("casey", D["Z2"])).json()["items"]}.get(tr)
check("Z2's inbox shows the send as 'cancelled', not open", it is not None and it["state"] == "cancelled" and not it["open"])
check("the envelope (key wrap) is gone for Z2: 404",
      C.get(f"/relay/api/transfers/{tr}/envelope", headers=H("casey", D["Z2"])).status_code == 404)
check("Z2 cannot accept a cancelled send (410 gone)",
      code(C.post(f"/relay/api/transfers/{tr}/accept", headers=H("casey", D["Z2"]))) == "gone")
sent = {x["id"]: x for x in C.get("/relay/api/sent", headers=H("jason", D["J1"])).json()["items"]}
check("the sender's Sent list shows it cancelled", sent.get(tr, {}).get("state") == "cancelled")
b = ready()
C.post(f"/relay/api/transfers/{b['id']}/decline", headers=H("casey", D["Z1"]))
C.post(f"/relay/api/transfers/{b['id']}/decline", headers=H("casey", D["Z2"]))
r = C.post(f"/relay/api/transfers/{b['id']}/cancel", headers=H("jason", D["J1"]))
check("cancel a finished send (all declined): 409 wrong_state", r.status_code == 409 and code(r) == "wrong_state")
la = q("SELECT last_activity, expires_at FROM transfer WHERE id=?", tr)[0]
sweep.sweep_once(now=la["last_activity"] + 86400 + 1)
check("a cancelled row is kept past 24 h while its 7 days run (receivers still see 'cancelled')", tstate(tr) == "cancelled")
sweep.sweep_once(now=la["expires_at"] + 1)
check("... and purged after the 7 days", tstate(tr) is None)
pc = q("SELECT 1 FROM push_out WHERE kind='withdrawn'")
check("the outbox CHECK takes 'withdrawn' (fresh schema)", len(pc) == 1)

# ====================================================================== 4. push_out migration (Step 4 table)
m = sqlite3.connect(T / "migrate.db", isolation_level=None)
m.executescript("""
CREATE TABLE transfer(id TEXT PRIMARY KEY, state TEXT);
CREATE TABLE push_out(
  id INTEGER PRIMARY KEY, device_id TEXT NOT NULL CHECK(length(device_id)=22),
  kind TEXT NOT NULL CHECK(kind IN ('new','delivered','test')),
  transfer_id TEXT CHECK(transfer_id IS NULL OR length(transfer_id)=22),
  result TEXT CHECK(result IS NULL OR result IN ('saved','declined')),
  created INTEGER NOT NULL, tries INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL);
CREATE INDEX push_out_due ON push_out(next_at);
CREATE TRIGGER push_on_ready AFTER UPDATE OF state ON transfer WHEN NEW.state='ready'
  BEGIN INSERT INTO push_out(device_id, kind, transfer_id, created, next_at) VALUES('d' || substr(NEW.id,2), 'new', NEW.id, 1, 1); END;
""")
m.execute("INSERT INTO push_out(device_id,kind,created,next_at) VALUES(?,?,1,1)", ("x" * 22, "test"))
try:
    m.execute("INSERT INTO push_out(device_id,kind,created,next_at) VALUES(?,?,1,1)", ("x" * 22, "withdrawn"))
    old_refused = False
except sqlite3.IntegrityError:
    old_refused = True
push._migrate(m)
push._migrate(m)
m.execute("INSERT INTO push_out(device_id,kind,created,next_at) VALUES(?,?,1,1)", ("y" * 22, "withdrawn"))
m.execute("INSERT INTO transfer VALUES(?, 'uploading')", ("t" * 22,))
m.execute("UPDATE transfer SET state='ready'")
kinds = [r[0] for r in m.execute("SELECT kind FROM push_out ORDER BY id")]
idx = m.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='push_out_due'").fetchone()
check(f"migration: the old CHECK refused 'withdrawn'; after: taken, old row kept, the Step 4 trigger still writes {kinds}",
      old_refused and kinds == ["test", "withdrawn", "new"] and idx is not None)
check("migration: run twice = no change (idempotent), no leftover table",
      not m.execute("SELECT 1 FROM sqlite_master WHERE name='push_out_new'").fetchone())
m.close()

# ====================================================================== 5. audit log
A = T / "audit"
lines = [json.loads(x) for x in (A / "audit.jsonl").read_text().splitlines()]
evs = {x["ev"] for x in lines}
check(f"audit: create, ready, refused, accepted, declined, cancelled, expired, abandoned, finished, purged, sweep {sorted(evs)}",
      {"create", "ready", "refused", "accepted", "declined", "cancelled", "expired", "abandoned", "finished", "purged",
       "sweep", "orphan_dir", "lost"} <= evs)
allowed = {"ts", "ev", "t", "size", "n", "dev", "code"}
check("audit: every line has only ts, ev, t, size, n, dev, code", all(set(x) <= allowed for x in lines))
check("audit: transfer ids cut to 8, device ids hashed to 12 hex",
      all(len(x.get("t", "12345678")) == 8 for x in lines)
      and all(len(x["dev"]) == 12 and all(ch in "0123456789abcdef" for ch in x["dev"]) for x in lines if "dev" in x))
# every field but the 8-character id prefix (random base64 could spell a short name by chance)
raw = " ".join(json.dumps({k: v for k, v in json.loads(x).items() if k != "t"})
                for p in A.glob("audit.jsonl*") for x in p.read_text().splitlines())
bad = [w for w in [*EMAIL.values(), "jason", "casey", "mom", "Jason", "Casey", "Mom", *LABELS.values(), *D.values(), t1, tr]
       if w in raw]
check(f"audit: no emails, person ids, names, device labels, full device or transfer ids {bad}", not bad)
check("audit: refusals carry their code (quota_bytes, quota_count, relay_full, rate_limited)",
      {"quota_bytes", "quota_count", "relay_full", "rate_limited"} <= {x.get("code") for x in lines})
check("audit: folder 700, files 600", stat.S_IMODE(A.stat().st_mode) == 0o700
      and stat.S_IMODE((A / "audit.jsonl").stat().st_mode) == 0o600 and stat.S_IMODE((A / "key").stat().st_mode) == 0o600)
check("audit: the same device gets the same tag, another device another",
      audit.dev_hash(D["J1"]) == audit.dev_hash(D["J1"]) != audit.dev_hash(D["J2"]))
r = C.get("/relay/api/audit?limit=5", headers=H("jason"))
check("audit reader: Jason gets the newest 5 lines", r.status_code == 200 and len(r.json()["events"]) == 5)
r = C.get("/relay/api/audit", headers=H("casey"))
check("audit reader: anyone else 404", r.status_code == 404)
check("audit reader: needs the request guard", C.get("/relay/api/audit", headers=H("jason", X_Novashare=None)).status_code == 403)
old = audit.ROTATE_BYTES
audit.ROTATE_BYTES = 2000
for _ in range(80):
    audit.event("sweep", n=1)
audit.ROTATE_BYTES = old
rot = sorted(p.name for p in A.glob("audit.jsonl.*"))
check(f"audit: rotated by size, at most 5 old files {rot}", "audit.jsonl.1" in rot and len(rot) <= 5
      and stat.S_IMODE((A / "audit.jsonl.1").stat().st_mode) == 0o600)
n = audit.prune(now=int(time.time()) + 91 * 86400)
check(f"audit: rotated files older than 90 days pruned ({n}), the current file kept",
      n == len(rot) and (A / "audit.jsonl").exists())
audit.event("bad event!", tid="x")
check("audit: a malformed event is never written (logged instead)", "ERROR relay audit" in (T / "intake.log").read_text())

# ====================================================================== 6. Jason's numbers
ready()
s = C.get("/relay/api/relay-stats", headers=H("jason")).json()
check(f"stats (admin): live sends, bytes held, last sweep {s['live']} {s['bytes_held']}",
      s["live"] >= 1 and s["bytes_held"] > 0 and isinstance(s["last_sweep"], dict) and "expired" in s["last_sweep"])
check("stats: anyone else 404", C.get("/relay/api/relay-stats", headers=H("casey")).status_code == 404)
pj = C.get("/devices", headers=H("jason", Sec_Fetch_Site="none", X_Novashare=None)).text
pz = C.get("/devices", headers=H("casey", Sec_Fetch_Site="none", X_Novashare=None)).text
check("Devices page: Jason sees the Relay line (live, held, last sweep)", "id=relay-line" in pj
      and "transfer" in pj and "held" in pj and "last sweep" in pj)
check("Devices page: Casey does not", "relay-line" not in pz and "Relay:" not in pz)

# ====================================================================== 7. the worker under the app lifespan
c = db()
c.execute("DELETE FROM relay_meta")
c.commit()
c.close()
with TestClient(app.app, client=("127.0.0.1", 50000)):
    t0 = time.time()
    while time.time() - t0 < 15 and not q("SELECT 1 FROM relay_meta WHERE k='last_sweep'"):
        time.sleep(0.5)
    alive = sweep.WORKER._thread is not None and sweep.WORKER._thread.is_alive()
check("lifespan: the sweep worker starts with the app and runs its first sweep", alive
      and bool(q("SELECT 1 FROM relay_meta WHERE k='last_sweep'")))
check("lifespan: the worker stops with the app", sweep.WORKER._thread is None)

# ====================================================================== 8. the log
logtxt = (T / "intake.log").read_text()
leak = [w for w in [*EMAIL.values(), *LABELS.values(), *D.values()] if w in logtxt]
check(f"intake.log: no emails, device labels or full device ids {leak}", not leak)

print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
