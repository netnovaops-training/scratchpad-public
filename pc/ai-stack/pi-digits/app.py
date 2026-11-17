#!/usr/bin/env python3
"""/pi -- compute pi on SuperNova and keep only the equation and its answers (2026-11-08 / 11-09).

STORED: /data/pi.snpi (version 2, see snpi.py) -- the recipe once, 40 bytes per run (decimal count +
Merkle root), one Ed25519 signature, one OpenTimestamps proof. No digits. Signing key:
/data/keys/ed25519.pem (never shared, not part of the result).

AT START: the digits are regenerated from the recipe (MPFR, in its own process) and held in memory
only; every stored root must match before the page serves digits. A restart redoes this (~2-3 min for
100 million). Nothing regenerated is written to disk.

A NEW RUN: MPFR computes, mpmath (Chudnovsky) recomputes and every digit is compared, the root is taken,
40 bytes are appended, the file is re-signed and re-timestamped.

HTTP (behind Caddy at /pi/, household sign-in; prefix stripped)
  GET /                       the page
  GET /api/status             runs, job, file size, whether the digits are rebuilt yet
  GET /api/digits?from=k&n=m  decimals k .. k+m-1 (m <= 2000), from the regenerated digits
  GET /api/commitment?run=N   64 sample blocks + proofs for run N (built in memory)
  GET /api/certificate        the equation, the run records, ID, signature, timestamp status
  GET /api/certificate/ots    the timestamp proof
  GET /api/verify             signature + every run's root redone from the regenerated digits
  GET /api/file               pi.snpi itself
  GET /api/verifier.py        stand-alone checker
"""
import hashlib
import json
import multiprocessing as mp
import os
import random
import struct
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import snpi

DATA = Path(os.environ.get("PI_DATA", "/data"))
FILE = DATA / "pi.snpi"
KEYS = DATA / "keys"
PORT = int(os.environ.get("PI_PORT", "8020"))
MAX = int(os.environ.get("PI_MAX_DIGITS", "100000000"))
TARGETS = sorted({int(x) for x in os.environ.get("PI_TARGETS", "1000000,10000000,100000000").split(",") if x.strip()})
BLOCK, SAMPLES = 1000, 64
HERE = Path(__file__).parent
JOBFILE = DATA / "job.json"

MEM = {"digits": "", "n": 0, "ready": False}          # regenerated digits: RAM only, never saved
SAMPLE_CACHE, OTS_CACHE = {}, {}
FILE_LOCK, CHECK_LOCK = threading.Lock(), threading.Lock()


# ------------------------------------------------------------------ job state
def get_job():
    try:
        return json.loads(JOBFILE.read_text())
    except (OSError, ValueError):
        return {"state": "idle"}


def set_job(**kw):
    j = get_job(); j.update(kw)
    tmp = JOBFILE.with_suffix(".tmp"); tmp.write_text(json.dumps(j)); os.replace(tmp, JOBFILE)


# ------------------------------------------------------------------ key, recipe, signing, timestamp
def signing_key():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519
    KEYS.mkdir(parents=True, exist_ok=True)
    os.chmod(KEYS, 0o700)
    p = KEYS / "ed25519.pem"
    if not p.exists():
        k = ed25519.Ed25519PrivateKey.generate()
        p.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                      serialization.NoEncryption()))
        os.chmod(p, 0o600)
    return serialization.load_pem_private_key(p.read_bytes(), None)


def public_key_hex():
    from cryptography.hazmat.primitives import serialization
    return signing_key().public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw).hex()


def make_recipe() -> bytes:
    import gmpy2
    import mpmath
    return snpi.canon({
        "constant": "pi",
        "digits": "gmpy2.digits(gmpy2.const_pi(), 10, N+21)[0][1:N+1] with gmpy2 precision int((N+40)*3.3219280948873626) bits",
        "software": {"gmpy2": gmpy2.version(), "mpfr": gmpy2.mpfr_version(), "gmp": gmpy2.mp_version(), "mpmath": mpmath.__version__},
        "new_run_check": "recomputed with mpmath (Chudnovsky) and every decimal compared before the run is recorded",
        "merkle": {"hash": "SHA-256", "block": BLOCK, "leaf": "sha256(f'{first_position}:' + up to 1000 decimals)",
                   "pair": "sha256(left + right); odd level: last node pairs with itself"},
        "run_record": "u64 N (little-endian) + 32-byte Merkle root of decimals 1..N",
        "id": "SHA-256(recipe bytes || run records); Ed25519 signs the same bytes",
        "public_key_ed25519": public_key_hex(),
        "issuer": "SuperNova (Site Owner's home server)"})


def ots_bytes(message: bytes, old: bytes = b"") -> bytes:
    """Stamp `message` (its SHA-256 is the ID), or upgrade an existing proof."""
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "pi.id"
        f.write_bytes(message)
        o = Path(d) / "pi.id.ots"
        if old:
            o.write_bytes(old)
            subprocess.run(["ots", "upgrade", str(o)], capture_output=True, timeout=120)
        else:
            subprocess.run(["ots", "stamp", str(f)], capture_output=True, timeout=120)
        return o.read_bytes() if o.exists() else old


def ots_status(ots: bytes) -> str:
    if not ots:
        return "not stamped yet"
    with tempfile.TemporaryDirectory() as d:
        o = Path(d) / "x.ots"; o.write_bytes(ots)
        txt = subprocess.run(["ots", "info", str(o)], capture_output=True, text=True, timeout=60).stdout
    if "BitcoinBlockHeaderAttestation" in txt:
        line = [l for l in txt.splitlines() if "BitcoinBlockHeaderAttestation" in l][0]
        return "anchored in Bitcoin block " + line.split("(")[-1].rstrip(")")
    return "pending: waiting for a Bitcoin block (usually a few hours)" if "Pending" in txt else "unknown"


def save(recipe: bytes, runs, ots: bytes = None):
    msg = snpi.signed_message(recipe, runs)
    if ots is None:
        ots = ots_bytes(msg)
    snpi.write(FILE, recipe, runs, signing_key().sign(msg), ots)
    OTS_CACHE.clear()


# ------------------------------------------------------------------ digits (own process: big maths holds the GIL)
def mpfr_digits(n: int) -> str:
    import gmpy2
    gmpy2.get_context().precision = int((n + 40) * 3.3219280948873626)
    return gmpy2.digits(gmpy2.const_pi(), 10, n + 21)[0][1:n + 1]


def child_regenerate(n, conn):
    conn.send_bytes(mpfr_digits(n).encode())
    conn.close()


def child_new_run(n, conn):
    import mpmath
    t = time.time()
    a = mpfr_digits(n)
    set_job(phase="computing again with mpmath (Chudnovsky) to check every digit")
    mpmath.mp.dps = n + 30
    b = mpmath.nstr(mpmath.mp.pi, n + 20, strip_zeros=False)[2:n + 2]
    if a != b:
        bad = next(i for i, (x, y) in enumerate(zip(a, b)) if x != y) + 1
        conn.send_bytes(json.dumps({"error": f"the two computations disagree from decimal #{bad}"}).encode())
    else:
        conn.send_bytes(a.encode())
    conn.close()


def in_process(target, n) -> str:
    ctx = mp.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    p = ctx.Process(target=target, args=(n, child), daemon=True)
    p.start(); child.close()
    data = parent.recv_bytes()
    p.join()
    if data.startswith(b"{"):
        raise RuntimeError(json.loads(data)["error"])
    return data.decode()


# ------------------------------------------------------------------ migration from version 1 (digits inside)
def migrate_v1():
    if not FILE.exists():
        return
    raw = FILE.read_bytes()
    if raw[:4] != b"SNPI" or struct.unpack_from("<H", raw, 4)[0] != 1:
        return
    set_job(state="running", target=None, phase="turning pi.snpi into equation + answers", started=time.time())
    magic, ver, flags, n, doff, dlen, coff, clen = struct.unpack_from("<4sHHQQQQQ", raw, 0)
    words = struct.unpack_from(f"<{dlen // 8}Q", raw, doff)
    digits = "".join(str(v).zfill(19 if (i + 1) * 19 <= n else n - i * 19) for i, v in enumerate(words))
    c, p, runs = raw[coff:coff + clen], 4, []
    for _ in range(struct.unpack_from("<I", c, 0)[0]):
        (cl,) = struct.unpack_from("<I", c, p); p += 4
        core = json.loads(c[p:p + cl]); p += cl + 64
        (ol,) = struct.unpack_from("<I", c, p); p += 4 + ol
        runs.append((core["decimals"], core["merkle"]["root"]))
    for rn, r in runs:
        if snpi.root(digits, rn, BLOCK) != r:
            raise RuntimeError(f"version-1 file: run {rn} does not match its root; left unchanged")
    trash = DATA / "_to_delete"
    trash.mkdir(exist_ok=True)
    os.replace(FILE, trash / f"pi-v1-with-digits-{time.strftime('%Y%m%d')}.snpi")
    save(make_recipe(), runs)
    MEM.update(digits=digits, n=n, ready=True)


# ------------------------------------------------------------------ worker
def worker():
    try:
        migrate_v1()
        if FILE.exists() and not MEM["ready"]:
            f = snpi.read(FILE)
            n = f["runs"][-1][0]
            set_job(state="running", target=n, phase="rebuilding the digits from the equation (MPFR)", started=time.time())
            d = in_process(child_regenerate, n)
            for rn, r in f["runs"]:
                if snpi.root(d, rn, BLOCK) != r:
                    raise RuntimeError(f"regenerated digits do not match run {rn}'s root")
            MEM.update(digits=d, n=n, ready=True)
        have = [r[0] for r in snpi.read(FILE)["runs"]] if FILE.exists() else []
        for n in [t for t in TARGETS if t <= MAX and t > (max(have) if have else 0)]:
            set_job(state="running", target=n, phase="computing with MPFR", started=time.time())
            d = in_process(child_new_run, n)
            set_job(phase="fingerprinting, signing, timestamping")
            r = snpi.root(d, n, BLOCK)
            with FILE_LOCK:
                if FILE.exists():
                    f = snpi.read(FILE)
                    recipe, runs = f["recipe_bytes"], f["runs"]
                else:
                    recipe, runs = make_recipe(), []
                save(recipe, runs + [(n, r)])
            MEM.update(digits=d, n=n, ready=True)
            SAMPLE_CACHE.clear()
            have.append(n)
        set_job(state="done", phase=None, target=None)
    except Exception as e:
        traceback.print_exc()
        set_job(state="error", error=f"{type(e).__name__}: {e}"[:300])


def stamper():
    while True:
        time.sleep(3 * 3600)
        try:
            with FILE_LOCK:
                f = snpi.read(FILE)
                new = ots_bytes(snpi.signed_message(f["recipe_bytes"], f["runs"]), f["ots"])
                if new != f["ots"]:
                    snpi.write(FILE, f["recipe_bytes"], f["runs"], f["sig"], new)
                    OTS_CACHE.clear()
        except Exception:
            traceback.print_exc()


# ------------------------------------------------------------------ answers built from memory
def samples(n):
    if n in SAMPLE_CACHE:
        return SAMPLE_CACHE[n]
    d = MEM["digits"]
    levels = snpi.levels_of(snpi.leaves(d, n, BLOCK))
    root = levels[-1][0]
    picks = sorted(random.Random(int.from_bytes(root[:8], "big")).sample(range(len(levels[0])), min(SAMPLES, len(levels[0]))))
    SAMPLE_CACHE[n] = {"decimals": n, "block": BLOCK, "merkle_root": root.hex(),
                       "samples": [{"block": i, "from_decimal": i * BLOCK + 1, "digits": d[i * BLOCK:min((i + 1) * BLOCK, n)],
                                    "proof": snpi.proof(levels, i)} for i in picks]}
    return SAMPLE_CACHE[n]


def verify():
    from cryptography.hazmat.primitives.asymmetric import ed25519
    f, checks = snpi.read(FILE), []
    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})
    msg = snpi.signed_message(f["recipe_bytes"], f["runs"])
    try:
        ed25519.Ed25519PublicKey.from_public_bytes(bytes.fromhex(f["recipe"]["public_key_ed25519"])).verify(f["sig"], msg)
        add("Ed25519 signature over the equation and every run", True, f"ID {f['id'][:16]}…")
    except Exception:
        add("Ed25519 signature over the equation and every run", False)
    if not MEM["ready"]:
        add("digits rebuilt from the equation", False, "still rebuilding; try again in a few minutes")
    else:
        for n, r in f["runs"]:
            t = time.time()
            add(f"run {n:,}: regenerated digits land on its root", snpi.root(MEM["digits"], n, BLOCK) == r,
                f"{time.time() - t:.1f}s")
    add("timestamp", bool(f["ots"]), ots_status(f["ots"]))
    return {"id": f["id"], "all_ok": all(c["ok"] for c in checks if c["check"] != "timestamp"), "checks": checks}


PAGE = (HERE / "page.html").read_text()


class H(BaseHTTPRequestHandler):
    server_version = "pi"

    def send(self, code, body, ctype="application/json", name=None):
        data = body if isinstance(body, bytes) else (body.encode() if isinstance(body, str) else json.dumps(body).encode())
        self.send_response(code)
        self.send_header("Content-Type", ctype + ("" if isinstance(body, bytes) else "; charset=utf-8"))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        if name:
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        f = snpi.read(FILE) if FILE.exists() else None
        if u.path in ("/", ""):
            return self.send(200, PAGE, "text/html")
        if u.path == "/api/status":
            job = get_job()
            if job.get("started"):
                job["elapsed"] = round(time.time() - job["started"])
            return self.send(200, {"runs": [{"decimals": n, "merkle_root": r} for n, r in (f["runs"] if f else [])],
                                   "job": job, "max_digits": MAX, "targets": TARGETS, "ready": MEM["ready"],
                                   "file": {"name": "pi.snpi", "bytes": f["bytes"] if f else 0}})
        if u.path == "/api/certificate":
            if not f:
                return self.send(404, {"error": "nothing yet"})
            if f["id"] not in OTS_CACHE:
                OTS_CACHE[f["id"]] = ots_status(f["ots"])
            return self.send(200, {"id": f["id"], "equation": f["recipe"], "runs": [{"decimals": n, "merkle_root": r} for n, r in f["runs"]],
                                   "signature_ed25519": f["sig"].hex(), "timestamp": OTS_CACHE[f["id"]], "bytes": f["bytes"],
                                   "parts": {"header": snpi.HLEN, "equation": len(f["recipe_bytes"]), "runs": 40 * len(f["runs"]),
                                             "signature": 64, "timestamp": len(f["ots"])}})
        if u.path == "/api/certificate/ots":
            return self.send(200, f["ots"], "application/octet-stream", "pi.snpi.ots") if f and f["ots"] else self.send(404, {"error": "none"})
        if u.path == "/api/file":
            return self.send(200, FILE.read_bytes(), "application/octet-stream", "pi.snpi")
        if u.path == "/api/verifier.py":
            return self.send(200, (HERE / "verify_pi.py").read_text(), "text/x-python")
        if u.path in ("/api/digits", "/api/commitment", "/api/verify") and not MEM["ready"]:
            return self.send(503, {"error": "rebuilding the digits from the equation; ready in a few minutes", "rebuilding": True})
        if u.path == "/api/digits":
            try:
                k, m = int(q.get("from", "1")), int(q.get("n", "100"))
            except ValueError:
                return self.send(400, {"error": "from and n must be whole numbers"})
            n = MEM["n"]
            if k < 1 or k > n:
                return self.send(404, {"error": f"decimal #{k} is not computed yet (have {n:,})", "have": n})
            return self.send(200, {"from": k, "digits": MEM["digits"][k - 1:k - 1 + max(1, min(m, 2000))], "run": n})
        if u.path == "/api/commitment":
            n = int(q["run"]) if q.get("run", "").isdigit() else 0
            if not f or n not in [r[0] for r in f["runs"]]:
                return self.send(404, {"error": "no such run"})
            with CHECK_LOCK:
                return self.send(200, samples(n))
        if u.path == "/api/verify":
            if not CHECK_LOCK.acquire(blocking=False):
                return self.send(429, {"error": "another check is running; try again in a moment"})
            try:
                return self.send(200, verify())
            finally:
                CHECK_LOCK.release()
        self.send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    set_job(state="starting", phase=None, target=None, started=None, error=None)
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=stamper, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
