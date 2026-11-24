"""relay_twin.py - TEST-ONLY Python twin of the Novashare Step 2 crypto (2026-11-24). Never installed in the live app
(the patcher copies relay.py and relay_js.py only); it needs `cryptography` (50.0.2, ML-KEM via its bundled OpenSSL),
which the live image does not have. Used by test_relay_e2e.py and, from the PC, test_relay_browser.py.

It is the sender (as relay_js.py does it) and, ahead of Step 3, the recipient: unwrap the content key with the
device's private keys, open the manifest, decrypt the pieces in order and refuse anything moved, swapped, cut short
or changed (every AEAD check is fatal; the last piece must carry the last flag and be the declared last one).
Wire format: relay.py docstring.

CLI (inside the sandbox tester image, --network host):
  python relay_twin.py register BASE EMAIL LABEL KEYFILE      make keys, register a device, keep private keys in
                                                              KEYFILE (mode 600, test keys of a throw-away sandbox)
  python relay_twin.py open BASE EMAIL KEYFILE TID RELAYDIR OUT   fetch this device's envelope, decrypt to OUT,
                                                              print the manifest's name/size and the sha256 of OUT
The sandbox drop key is read from $SBX_KEY_FILE (never printed).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import pathlib
import sys

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import mlkem, x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

ALG = "novashare-v1"
PLAIN = 4 * 1048576
CT = PLAIN + 16
MANIFEST_IDX = 0xFFFFFFFF


class Tampered(Exception):
    """Raised by the decryptor on ANY failed check: the file is not finished and must not be used."""


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def unb64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def lp(b: bytes) -> bytes:
    return len(b).to_bytes(4, "big") + b


def u32(n: int) -> bytes:
    return n.to_bytes(4, "big")


def device_fp(person: str, did: str, x: bytes, m: bytes) -> str:
    return hashlib.sha256(b"novashare/device/v1" + lp(person.encode()) + lp(did.encode()) + lp(x) + lp(m)).hexdigest()


# ------------------------------------------------------------------ keys

class Device:
    """One device's key pairs (X25519 + ML-KEM-768). Private keys stay in this object (and KEYFILE for the CLI)."""

    def __init__(self, x: x25519.X25519PrivateKey | None = None, m: mlkem.MLKEM768PrivateKey | None = None):
        self.x = x or x25519.X25519PrivateKey.generate()
        self.m = m or mlkem.MLKEM768PrivateKey.generate()
        self.id = self.person = None

    @property
    def xpub(self) -> bytes:
        return self.x.public_key().public_bytes_raw()

    @property
    def mpub(self) -> bytes:
        return self.m.public_key().public_bytes_raw()

    def dump(self) -> dict:
        return {"id": self.id, "person": self.person, "x": b64u(self.x.private_bytes_raw()),
                "m": b64u(self.m.private_bytes_raw())}

    @classmethod
    def load(cls, d: dict) -> "Device":
        dev = cls(x25519.X25519PrivateKey.from_private_bytes(unb64u(d["x"])),
                  mlkem.MLKEM768PrivateKey.from_seed_bytes(unb64u(d["m"])))   # private_bytes_raw = the 64-byte seed
        dev.id, dev.person = d["id"], d["person"]
        return dev


# ------------------------------------------------------------------ the formulas (twins of relay_js.py)

def piece_aad(tid: str, i: int, last: bool) -> bytes:
    return b"novashare/piece/v1" + lp(tid.encode()) + u32(i) + (b"\x01" if last else b"\x00")


def manifest_aad(tid: str) -> bytes:
    return b"novashare/manifest/v1" + lp(tid.encode())


def wrap_ctx(tid: str, sdev: str, rdev: str, sx: bytes, rx: bytes, rm: bytes, eph: bytes, kem_ct: bytes) -> bytes:
    return (b"novashare/wrap/v1" + lp(ALG.encode()) + lp(tid.encode()) + lp(sdev.encode()) + lp(rdev.encode())
            + lp(sx) + lp(rx) + lp(rm) + lp(eph) + lp(kem_ct))


def _hkdf(ikm: bytes, ctx: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=bytes(32), info=ctx).derive(ikm)


def wrap_for(K: bytes, tid: str, sender: Device, rdev: str, rx: bytes, rm: bytes) -> dict:
    eph = x25519.X25519PrivateKey.generate()
    rxk = x25519.X25519PublicKey.from_public_bytes(rx)
    ss1, ss2 = eph.exchange(rxk), sender.x.exchange(rxk)
    ss3, kem_ct = mlkem.MLKEM768PublicKey.from_public_bytes(rm).encapsulate()
    ephp = eph.public_key().public_bytes_raw()
    ctx = wrap_ctx(tid, sender.id, rdev, sender.xpub, rx, rm, ephp, kem_ct)
    iv = os.urandom(12)
    return {"eph_pub": ephp, "kem_ct": kem_ct, "wrapped_key": iv + AESGCM(_hkdf(ss1 + ss2 + ss3, ctx)).encrypt(iv, K, ctx)}


def unwrap(env: dict, me: Device) -> bytes:
    """The content key from MY wrap in an envelope (relay.do_envelope). Tampered on any mismatch."""
    w, s = env["wrap"], env["sender"]
    if w["device_id"] != me.id:
        raise Tampered("this wrap is for another device")
    sx, eph, kem_ct, wk = unb64u(s["x25519_pub"]), unb64u(w["eph_pub"]), unb64u(w["kem_ct"]), unb64u(w["wrapped_key"])
    try:
        ss1 = me.x.exchange(x25519.X25519PublicKey.from_public_bytes(eph))
        ss2 = me.x.exchange(x25519.X25519PublicKey.from_public_bytes(sx))
        ss3 = me.m.decapsulate(kem_ct)
        ctx = wrap_ctx(env["id"], s["device_id"], me.id, sx, me.xpub, me.mpub, eph, kem_ct)
        return AESGCM(_hkdf(ss1 + ss2 + ss3, ctx)).decrypt(wk[:12], wk[12:], ctx)
    except Exception as e:  # noqa: BLE001  -- every failure is the same answer: not openable
        raise Tampered(f"key wrap does not open ({type(e).__name__})") from None


def manifest_blob(K: bytes, prefix: bytes, tid: str, meta: dict) -> bytes:
    js = json.dumps(meta).encode()
    padded = bytearray(-(-(4 + len(js)) // 256) * 256)
    padded[:4 + len(js)] = u32(len(js)) + js
    iv = prefix + u32(MANIFEST_IDX)
    return iv + AESGCM(K).encrypt(iv, bytes(padded), manifest_aad(tid))


def open_manifest(K: bytes, tid: str, blob: bytes) -> tuple[dict, bytes]:
    """(manifest dict, the 8-byte nonce prefix of the pieces). Tampered if it does not open."""
    if len(blob) < 28 or blob[8:12] != u32(MANIFEST_IDX):
        raise Tampered("manifest has the wrong shape")
    try:
        pt = AESGCM(K).decrypt(blob[:12], blob[12:], manifest_aad(tid))
    except Exception:  # noqa: BLE001
        raise Tampered("manifest does not open") from None
    n = int.from_bytes(pt[:4], "big")
    if 4 + n > len(pt) or any(pt[4 + n:]):
        raise Tampered("manifest padding is wrong")
    return json.loads(pt[4:4 + n]), blob[:8]


def pieces_for(size: int) -> int:
    return max(1, -(-size // PLAIN))


def encrypt_piece(K: bytes, prefix: bytes, tid: str, i: int, last: bool, plain: bytes) -> bytes:
    return AESGCM(K).encrypt(prefix + u32(i), plain, piece_aad(tid, i, last))


def decrypt_to(K: bytes, prefix: bytes, tid: str, manifest: dict, size_ct: int, read_piece, out) -> int:
    """Decrypt every piece in order into the file object `out`; returns the plaintext length.

    read_piece(i) -> ciphertext bytes of slot i. The declared geometry must match the manifest; each piece must open
    under its own index and the last flag only on the last slot. Any failure raises Tampered (caller discards out)."""
    n = manifest["pieces"]
    if n != pieces_for(manifest["size"]) or size_ct != manifest["size"] + 16 * n:
        raise Tampered("sizes do not agree with the manifest")
    total = 0
    for i in range(n):
        ct = read_piece(i)
        try:
            pt = AESGCM(K).decrypt(prefix + u32(i), ct, piece_aad(tid, i, i == n - 1))
        except Exception:  # noqa: BLE001
            raise Tampered(f"piece {i} does not open (moved, swapped, changed or cut short)") from None
        out.write(pt)
        total += len(pt)
    if total != manifest["size"]:
        raise Tampered("total size differs from the manifest")
    return total


def read_store(relay_dir: pathlib.Path, tid: str, size_ct: int, pieces: int):
    """A read_piece() over the relay store file (what Step 3's blob download will serve)."""
    path = relay_dir / tid / "data"

    def rd(i: int) -> bytes:
        ln = CT if i < pieces - 1 else size_ct - (pieces - 1) * CT
        with path.open("rb") as f:
            f.seek(i * CT)
            return f.read(ln)
    return rd


# ------------------------------------------------------------------ a sender over HTTP (what relay.js does)

def headers(email: str, key: str, did: str | None = None) -> dict:
    h = {"X-Drop-Key": key, "X-Auth-Email": email, "X-Auth-Level": "full", "Sec-Fetch-Site": "same-origin", "X-Novashare": "1"}
    if did:
        h["X-Device-Id"] = did
    return h


def register(client, email: str, key: str, label: str) -> Device:
    d = Device()
    r = client.post("/devices/api/register", headers=headers(email, key),
                    json={"label": label, "x25519_pub": b64u(d.xpub), "mlkem_pub": b64u(d.mpub)})
    r.raise_for_status()
    d.id = r.json()["device_id"]
    d.person = client.get("/devices/api/mine", headers=headers(email, key)).json()["person"]
    return d


def prepare_send(client, email: str, key: str, sender: Device, to: list[str], meta: dict, tid: str | None = None):
    """(create body, K, prefix): fetch the directory, check each fingerprint, wrap K for every device of `to`."""
    tid = tid or b64u(os.urandom(16))
    K, prefix = AESGCM.generate_key(256), os.urandom(8)
    ppl = {p["id"]: p for p in client.get("/devices/api/people", headers=headers(email, key, sender.id)).json()["people"]}
    wraps = []
    for p in to:
        for d in ppl[p]["devices"]:
            if d["id"] == sender.id:
                continue
            rx, rm = unb64u(d["x25519_pub"]), unb64u(d["mlkem_pub"])
            fp = device_fp(p, d["id"], rx, rm)
            assert fp == d["fingerprint"], "directory fingerprint mismatch"
            w = wrap_for(K, tid, sender, d["id"], rx, rm)
            wraps.append({"device_id": d["id"], "device_fp": fp, **{k: b64u(v) for k, v in w.items()}})
    n = pieces_for(meta["size"])
    meta = {**meta, "pieces": n}
    body = {"id": tid, "alg": ALG, "to": to, "size_ct": meta["size"] + 16 * n, "pieces": n,
            "manifest": b64u(manifest_blob(K, prefix, tid, meta)), "wraps": wraps}
    return body, K, prefix


def put_piece(client, email, key, sender, tid, i, ct) -> dict:
    h = {**headers(email, key, sender.id), "X-Piece-Sha256": hashlib.sha256(ct).hexdigest(),
         "Content-Type": "application/octet-stream"}
    r = client.put(f"/relay/api/transfers/{tid}/pieces/{i}", headers=h, content=ct)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------------ CLI for the browser test

def _main(argv: list[str]) -> None:
    import httpx
    key = pathlib.Path(os.environ["SBX_KEY_FILE"]).read_text().strip()
    cmd, base, email = argv[0], argv[1], argv[2]
    with httpx.Client(base_url=base, timeout=60) as c:
        if cmd == "register":
            label, kf = argv[3], pathlib.Path(argv[4])
            d = register(c, email, key, label)
            old = os.umask(0o077)
            kf.write_text(json.dumps(d.dump()))
            os.umask(old)
            print("registered", d.id[:6])
        elif cmd == "open":
            kf, tid, rdir, outp = pathlib.Path(argv[3]), argv[4], pathlib.Path(argv[5]), pathlib.Path(argv[6])
            me = Device.load(json.loads(kf.read_text()))
            r = c.get(f"/relay/api/transfers/{tid}/envelope", headers=headers(email, key, me.id))
            r.raise_for_status()
            env = r.json()
            K = unwrap(env, me)
            man, prefix = open_manifest(K, tid, unb64u(env["manifest"]))
            h = hashlib.sha256()

            class Tee:
                def __init__(self, f):
                    self.f = f

                def write(self, b):
                    h.update(b)
                    if self.f:
                        self.f.write(b)
            try:
                with (outp.open("wb") if str(outp) != "-" else open(os.devnull, "wb")) as f:
                    n = decrypt_to(K, prefix, tid, man, env["size_ct"], read_store(rdir, tid, env["size_ct"], env["pieces"]), Tee(f))
            except Tampered:
                if str(outp) != "-":
                    outp.unlink(missing_ok=True)
                raise
            print(json.dumps({"name": man["name"], "size": man["size"], "caption": man.get("caption", ""), "bytes": n,
                              "sha256": h.hexdigest()}))
        else:
            raise SystemExit("usage: see the docstring")


if __name__ == "__main__":
    _main(sys.argv[1:])
