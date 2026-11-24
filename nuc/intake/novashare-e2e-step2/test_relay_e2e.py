"""test_relay_e2e.py - Novashare E2E Step 2 end to end (2026-11-24): real server, real crypto, 2 GB.

Runs in the SANDBOX TESTER image only (the patched intake image + cryptography 50.0.2 for relay_twin.py; the live
image is not changed):

    docker run --rm --entrypoint python intake-sandbox:relay-tester test_relay_e2e.py [--big-mb 2048]

It starts the patched app under uvicorn in a subprocess (temp DB, relay folder, log; fake example.invalid people),
talks to it over HTTP like the browser does, and checks:
  - round trip: sends of 0 bytes, exactly 4 MiB and 9 MiB+7 open on every recipient device, byte for byte, with the
    name and caption back from the manifest;
  - only the recipients' devices can fetch their own wraps; another device's wrap does not open with my keys;
  - tamper: pieces swapped, reordered, taken from another send, cut short, a byte flipped, a shrunk size, a piece
    claimed as the last one, a manifest from another send: every one is refused by the decryptor;
  - crash: the server is killed (SIGKILL) mid-upload, restarted, the same create answers "resumed" with the missing
    pieces, the send completes and opens;
  - 2 GB (+123 bytes): sent, opened, sha256 equal; then a grep over the relay store, the DB (+ WAL), the log and the
    staging folder finds NONE of the plaintext marker, the file name or the caption (and a control file proves
    the grep would have found them).
Last line: "ALL PASS" or "SOME FAILED".
Variables: fails, check(), T (temp dir), BASE, KEY, devs (name -> relay_twin.Device), srv (the uvicorn process).
"""
import argparse
import concurrent.futures
import hashlib
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx

import relay_twin as tw

ap = argparse.ArgumentParser()
ap.add_argument("--big-mb", type=int, default=2048)
args = ap.parse_args()

T = pathlib.Path(tempfile.mkdtemp(prefix="relaye2e-"))
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
KEY = os.urandom(16).hex()
RELAY = T / "relay"
ENV = {**os.environ, "DROP_CONFIG": str(T / "drop.toml"), "INTAKE_KEY": KEY, "INTAKE_TOKEN": "t-test",
       "INTAKE_ALLOWED_PEERS": "127.0.0.1", "INTAKE_STAGE": str(T / "stage"), "INTAKE_UNSORTED": str(T / "unsorted"),
       "INTAKE_LOG": str(T / "intake.log"), "INTAKE_DEVICES_DB": str(T / "devices.db"), "INTAKE_RELAY_DIR": str(RELAY),
       "INTAKE_MAX_MB": "10240", "INTAKE_RESERVE_MB": "64", "LLM_BASE": "http://10.255.255.1",
       "AUTH_LEVELS_URL": "http://10.255.255.1", "GOTENBERG_URL": "http://10.255.255.1"}
HERE = pathlib.Path(__file__).resolve().parent
EMAIL = {"jason": "j@example.invalid", "casey": "z@example.invalid", "mom": "m@example.invalid"}
fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label, flush=True)
    fails += not ok


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


PORT = free_port()
BASE = f"http://127.0.0.1:{PORT}"


def start():
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(PORT),
                          "--log-level", "warning"], cwd=str(HERE), env=ENV)
    for _ in range(100):
        try:
            if httpx.get(BASE + "/healthz", headers={"X-Drop-Key": KEY}, timeout=1).status_code in (200, 503):
                return p
        except httpx.HTTPError:
            time.sleep(0.1)
    raise SystemExit("server did not start")


srv = start()
C = httpx.Client(base_url=BASE, timeout=120)
devs = {}
for who, name in (("jason", "J1"), ("jason", "J2"), ("casey", "Z1"), ("casey", "Z2"), ("mom", "M1")):
    devs[name] = tw.register(C, EMAIL[who], KEY, name)
J1 = devs["J1"]


def H(who, dev=None):
    return tw.headers(EMAIL[who], KEY, devs[dev].id if dev else None)


def send(plain: bytes, name="doc.bin", caption="", to=("casey", "jason")):
    """Encrypt + create + upload every piece (as relay.js). Returns (tid, body, K, prefix)."""
    meta = {"v": 1, "name": name, "type": "application/octet-stream", "size": len(plain), "last_modified": 1,
            "caption": caption}
    body, K, prefix = tw.prepare_send(C, EMAIL["jason"], KEY, J1, list(to), meta)
    r = C.post("/relay/api/transfers", headers=H("jason", "J1"), json=body)
    assert r.status_code == 201, r.text
    n = body["pieces"]
    for i in range(n):
        tw.put_piece(C, EMAIL["jason"], KEY, J1, body["id"], i,
                     tw.encrypt_piece(K, prefix, body["id"], i, i == n - 1, plain[i * tw.PLAIN:(i + 1) * tw.PLAIN]))
    return body["id"], body, K, prefix


class Sink:
    def __init__(self):
        self.h, self.n, self.buf = hashlib.sha256(), 0, bytearray()

    def write(self, b):
        self.h.update(b)
        self.n += len(b)
        if len(self.buf) < 64 * 1048576:
            self.buf += b


def open_as(who, dev, tid, read=None, env_over=None):
    """(manifest, plaintext bytes) as device `dev`; raises tw.Tampered on any failed check."""
    r = C.get(f"/relay/api/transfers/{tid}/envelope", headers=H(who, dev))
    r.raise_for_status()
    env = {**r.json(), **(env_over or {})}
    K = tw.unwrap(env, devs[dev])
    man, prefix = tw.open_manifest(K, tid, tw.unb64u(env["manifest"]))
    s = Sink()
    tw.decrypt_to(K, prefix, tid, man, env["size_ct"], read or tw.read_store(RELAY, tid, env["size_ct"], env["pieces"]), s)
    return man, bytes(s.buf)


def refused(fn):
    try:
        fn()
        return False
    except tw.Tampered:
        return True


# --- 1. round trips
for label, plain in (("0 bytes", b""), ("exactly 4 MiB", os.urandom(tw.PLAIN)), ("9 MiB + 7", os.urandom(9 * 1048576 + 7))):
    tid, body, _, _ = send(plain, name=f"rt {label}.bin", caption="hello " + label)
    ok = True
    for who, dev in (("casey", "Z1"), ("casey", "Z2"), ("jason", "J2")):
        man, got = open_as(who, dev, tid)
        ok &= got == plain and man["name"] == f"rt {label}.bin" and man["caption"] == "hello " + label
    check(f"round trip {label}: Z1, Z2 and J2 each open it byte for byte (pieces={body['pieces']})", ok)

PLAIN3 = os.urandom(2 * tw.PLAIN + 777)
TID, B3, K3, PRE3 = send(PLAIN3, name="three.bin")
TIDB, BB, _, _ = send(os.urandom(2 * tw.PLAIN + 777), name="other.bin")

# --- 2. wrap access
for who, dev in (("jason", "J1"), ("mom", "M1")):
    r = C.get(f"/relay/api/transfers/{TID}/envelope", headers=H(who, dev))
    check(f"{dev} (not a recipient) cannot fetch any wrap -> 404", r.status_code == 404)
r = C.get(f"/relay/api/transfers/{TID}/envelope", headers=tw.headers(EMAIL["mom"], KEY, devs["Z1"].id))
check("mom naming casey's device id -> 403", r.status_code == 403)
envZ2 = C.get(f"/relay/api/transfers/{TID}/envelope", headers=H("casey", "Z2")).json()
envZ2["wrap"]["device_id"] = devs["Z1"].id               # a server pretending Z2's wrap is Z1's
check("Z2's wrap handed to Z1 does not open with Z1's keys", refused(lambda: tw.unwrap({**envZ2, "id": TID}, devs["Z1"])))
envZ1 = C.get(f"/relay/api/transfers/{TID}/envelope", headers=H("casey", "Z1")).json()
check("a wrap moved to another transfer id does not open (the id is bound)", refused(lambda: tw.unwrap({**envZ1, "id": TIDB}, devs["Z1"])))
fake_sender = {**envZ1["sender"], "x25519_pub": tw.b64u(tw.Device().xpub)}
check("a wrap claimed to be from another sender key does not open (sender-static term)",
      refused(lambda: tw.unwrap({**envZ1, "sender": fake_sender}, devs["Z1"])))

# --- 3. tamper: the store is the attacker's (a hacked server or disk)
rd = tw.read_store(RELAY, TID, B3["size_ct"], 3)
rdB = tw.read_store(RELAY, TIDB, BB["size_ct"], 3)
cases = {
    "pieces 0 and 1 swapped": lambda i: rd({0: 1, 1: 0}.get(i, i)),
    "piece 1 served in slot 0 (reorder)": lambda i: rd(1) if i == 0 else rd(i),
    "piece 1 swapped in from another send": lambda i: rdB(1) if i == 1 else rd(i),
    "last piece cut short by one byte": lambda i: rd(i)[:-1] if i == 2 else rd(i),
    "last piece dropped (served empty)": lambda i: b"" if i == 2 else rd(i),
    "one byte flipped in piece 1": lambda i: (lambda b: b[:100] + bytes([b[100] ^ 1]) + b[101:])(rd(i)) if i == 1 else rd(i),
}
for name, fn in cases.items():
    check(f"tamper refused: {name}", refused(lambda: open_as("casey", "Z1", TID, read=fn)))
check("tamper refused: the server reports a smaller size (cut the send short)",
      refused(lambda: open_as("casey", "Z1", TID, env_over={"size_ct": B3["size_ct"] - tw.CT})))
try:                                                     # a truncating server claims piece 1 is the end
    tw.AESGCM(K3).decrypt(PRE3 + tw.u32(1), rd(1), tw.piece_aad(TID, 1, True))
    check("tamper refused: piece 1 presented as the last piece", False)
except Exception:  # noqa: BLE001
    check("tamper refused: piece 1 presented as the last piece", True)
envB = C.get(f"/relay/api/transfers/{TIDB}/envelope", headers=H("casey", "Z1")).json()
check("tamper refused: the manifest of another send", refused(lambda: open_as("casey", "Z1", TID, env_over={"manifest": envB["manifest"]})))
check("untampered, the same send still opens", open_as("casey", "Z1", TID)[1] == PLAIN3)

# --- 4. crash mid-upload: SIGKILL the server, restart, resume
PL = os.urandom(10 * tw.PLAIN + 5)
meta = {"v": 1, "name": "crash.bin", "type": "", "size": len(PL), "last_modified": 1, "caption": ""}
body, K, pre = tw.prepare_send(C, EMAIL["jason"], KEY, J1, ["casey"], meta)
tid = body["id"]
check("crash test: create -> 201", C.post("/relay/api/transfers", headers=H("jason", "J1"), json=body).status_code == 201)
cts = [tw.encrypt_piece(K, pre, tid, i, i == 10, PL[i * tw.PLAIN:(i + 1) * tw.PLAIN]) for i in range(11)]
for i in (0, 3, 5, 9):
    tw.put_piece(C, EMAIL["jason"], KEY, J1, tid, i, cts[i])


def slow_body():                                         # piece 6 dribbles in while the server dies
    yield cts[6][:1048576]
    time.sleep(3)
    yield cts[6][1048576:]

hit = {}


def slow_put():
    try:
        h = {**H("jason", "J1"), "X-Piece-Sha256": hashlib.sha256(cts[6]).hexdigest()}
        hit["r"] = httpx.put(f"{BASE}/relay/api/transfers/{tid}/pieces/6", headers=h, content=slow_body(), timeout=30)
    except httpx.HTTPError as e:
        hit["err"] = type(e).__name__

th = threading.Thread(target=slow_put)
th.start()
time.sleep(1)
srv.send_signal(signal.SIGKILL)
srv.wait()
th.join()
check("server killed mid-piece: the in-flight PUT failed", "err" in hit or hit.get("r") is None or hit["r"].status_code >= 500)
srv = start()
C.close()
C = httpx.Client(base_url=BASE, timeout=120)
r = C.post("/relay/api/transfers", headers=H("jason", "J1"), json=body)
miss = r.json().get("missing")
check(f"after restart the same create -> 200 resumed, missing {miss}", r.status_code == 200 and r.json().get("resumed") is True
      and miss == [1, 2, 4, 6, 7, 8, 10])
for i in miss:
    last = tw.put_piece(C, EMAIL["jason"], KEY, J1, tid, i, cts[i])
check("resumed send completes -> ready", last.get("state") == "ready")
check("resumed send opens byte for byte on Z1", open_as("casey", "Z1", tid)[1] == PL)

# --- 5. 2 GB (+123): parallel upload like the browser (3 at a time), then grep the store for plaintext
MARK = b"NOVASHARE-PLAINTEXT-CANARY-" + os.urandom(6).hex().encode()
NAME = "canary-name-" + os.urandom(4).hex() + ".mkv"
CAP = "canary-caption-" + os.urandom(4).hex()
SIZE = args.big_mb * 1048576 + 123
SEED = os.urandom(32)


def plain_piece(i):
    """Piece i of the big file: fast pseudo-random bytes with MARK every 64 KiB (rebuilt identically on demand)."""
    ln = min(tw.PLAIN, SIZE - i * tw.PLAIN)
    blk = bytearray(hashlib.shake_256(SEED + i.to_bytes(4, "big")).digest(65536))
    blk[:len(MARK)] = MARK
    return (bytes(blk) * 64)[:ln]


t0 = time.time()
meta = {"v": 1, "name": NAME, "type": "video/x-matroska", "size": SIZE, "last_modified": 1, "caption": CAP}
body, K, pre = tw.prepare_send(C, EMAIL["jason"], KEY, J1, ["casey"], meta)
big = body["id"]
r = C.post("/relay/api/transfers", headers=H("jason", "J1"), json=body)
check(f"2 GB: create -> 201 ({body['pieces']} pieces, {body['size_ct']} bytes of ciphertext)", r.status_code == 201)
src = hashlib.sha256()
for i in range(body["pieces"]):
    src.update(plain_piece(i))
n = body["pieces"]


def up(i):
    ct = tw.encrypt_piece(K, pre, big, i, i == n - 1, plain_piece(i))
    with httpx.Client(base_url=BASE, timeout=120) as c:
        return tw.put_piece(c, EMAIL["jason"], KEY, J1, big, i, ct)["state"]

with concurrent.futures.ThreadPoolExecutor(3) as ex:
    states = list(ex.map(up, range(n)))
t_up = time.time() - t0
check(f"2 GB: every piece accepted, ends ready ({t_up:.0f} s)", states.count("ready") == 1 and set(states) <= {"uploading", "ready"})
check("2 GB: the store file is exactly size_ct", (RELAY / big / "data").stat().st_size == body["size_ct"])
r = C.get(f"/relay/api/transfers/{big}/envelope", headers=H("casey", "Z1"))
env = r.json()
Kz = tw.unwrap(env, devs["Z1"])
man, prefix = tw.open_manifest(Kz, big, tw.unb64u(env["manifest"]))
s = Sink()
s.buf = bytearray(64 * 1048576)                          # do not keep 2 GB in memory: hash only
tw.decrypt_to(Kz, prefix, big, man, env["size_ct"], tw.read_store(RELAY, big, env["size_ct"], env["pieces"]), s)
check("2 GB: Z1 opens it, sha256 equal to the source, name and caption from the manifest",
      s.h.hexdigest() == src.hexdigest() and s.n == SIZE and man["name"] == NAME and man["caption"] == CAP)

srv.send_signal(signal.SIGTERM)
srv.wait(timeout=30)
ctl = T / "grep-control.txt"
ctl.write_bytes(b"x" * 100 + MARK + b" " + NAME.encode() + b" " + CAP.encode())
targets = [str(RELAY), str(T / "devices.db"), str(T / "intake.log")] + [str(p) for p in T.glob("devices.db-*")]
targets += [str(T / "stage")] if (T / "stage").exists() else []
for label, needle in (("plaintext marker", MARK), ("file name", NAME.encode()), ("caption", CAP.encode())):
    g = subprocess.run(["grep", "-r", "-a", "-l", "-F", "-e", needle.decode()] + targets, capture_output=True, text=True)
    c = subprocess.run(["grep", "-a", "-c", "-F", "-e", needle.decode(), str(ctl)], capture_output=True, text=True)
    check(f"grep over the relay store, DB (+WAL), log, staging: {label} NOT found (control file: found)",
          g.returncode == 1 and g.stdout == "" and c.stdout.strip() == "1")
size_on_disk = sum(p.stat().st_size for p in RELAY.rglob("data"))
check(f"relay store holds only ciphertext files ({size_on_disk / 1073741824:.2f} GiB in {len(list(RELAY.iterdir()))} sends)",
      all(p.name == "data" for p in RELAY.rglob("*") if p.is_file()))
log = (T / "intake.log").read_text()
check("log: no email, no name, no caption, no full transfer id", "@" not in log and NAME not in log and CAP not in log and big not in log)

print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
