"""relay.py - Novashare person-to-person E2E, Step 2: the encrypted send (2026-11-24).

Plan: NOVASHARE-E2E-PLAN.md step 2 ("a 2 GB file is sent; the server holds only ciphertext"). Design:
NOVASHARE-E2E-DESIGN.md; threats: NOVASHARE-E2E-THREATS.md. Builds on Step 1 (devices.py, LIVE 2026-11-24): the
same request guard, the same household, the same SQLite file (so a device check and a wrap insert sit in ONE
transaction). Like Step 1, narrower than the design doc: no identity keys, prekeys, signatures, push, inbox, accept,
blob download or sweep yet (steps 3-6). The recipient-side decrypt is step 3; test_relay_e2e.py has a test-only
Python decryptor that proves the round trip.

WHAT THE SERVER SEES
  Ciphertext pieces, one opaque manifest blob (name, type, size, caption are INSIDE it, padded to 256 bytes),
  one key wrap per receiving device, and who -> whom, ciphertext size, time. Never a name, a plaintext byte or a key.
  The sender's browser (relay_js.py) does all the crypto:
    content key K   32 random bytes per send (crypto.getRandomValues), AES-256-GCM.
    piece i         plaintext 4 MiB (last one shorter, an empty file = one empty piece); ciphertext = +16 tag.
                    nonce = prefix(8 random bytes, once per send) || uint32be(i)
                    AAD   = "novashare/piece/v1" || lp(transfer id) || uint32be(i) || (1 if last else 0)
                    so a piece cannot be moved, swapped in from another send, or the send cut short.
    manifest        lp-free: uint32be(len(json)) || json || zero padding to a multiple of 256 bytes;
                    nonce = prefix || 0xFFFFFFFF (an index no piece can have), AAD "novashare/manifest/v1" || lp(id).
    wrap per device eph = fresh X25519 pair; ss1 = X25519(eph, R.x25519); ss2 = X25519(sender device static, R.x25519)
                    (sender authenticity: only the sender device can make it); (kem_ct, ss3) = ML-KEM-768.Encaps(R.mlkem)
                    wrap_key = HKDF-SHA256(ikm = ss1 || ss2 || ss3, salt = 32 zero bytes, info = ctx)
                    ctx = "novashare/wrap/v1" || lp("novashare-v1") || lp(id) || lp(sender device id)
                          || lp(recipient device id) || lp(S.x25519) || lp(R.x25519) || lp(R.mlkem) || lp(eph) || lp(kem_ct)
                    wrapped_key = nonce12 (random) || AES-256-GCM(wrap_key, K, AAD = ctx)   (60 bytes)
                    Hybrid: an attacker must break X25519 AND ML-KEM-768 to learn K.
  lp(x) = uint32be(len(x)) || x, as in devices.py.

ROUTES (all signed-in, devices.guard: X-Auth-Level full, Sec-Fetch-Site same-origin, X-Novashare: 1; (D) = the
caller's own live device in X-Device-Id)
  GET  /relay/send                              the send page (strict CSP, no inline script)
  GET  /relay/static/relay.js                   its script (SRI in the page)
  POST /relay/api/transfers (D)                 create: {id, alg, to, size_ct, pieces, manifest, wraps[]}
                                                201 new / 200 {resumed: true} for the same id + same body
  GET  /relay/api/transfers/{id} (D, sender)    {state, pieces, missing, ...}: the resume point
  PUT  /relay/api/transfers/{id}/pieces/{n} (D, sender)  raw ciphertext, X-Piece-Sha256; last piece -> ready
  GET  /relay/api/transfers/{id}/envelope (D, recipient) THIS device's wrap only + manifest + sender public key

SIX ANSWERS (backend-design:design, 2026-11-24)
  1 Context: HTTP routes in the intake FastAPI app (one uvicorn process); callers are a browser page that retries.
    rescan_held.py imports app hourly, so nothing here opens a file at import (the DB opens on first request).
  2 Data: tables transfer, wrap, piece in devices.db (Step 1's file; ON DELETE CASCADE from transfer). Ciphertext
    in RELAY/<id>/data (INTAKE_RELAY_DIR, default /srv/unsorted/.novashare-relay: a dot folder, mode 700, never a
    library; nothing files into it or reads it but this module). The file is fallocated at create, so a full disk
    is refused up front. Invariants: pieces = ceil(size_ct / (4 MiB + 16)); every piece has an exact length; a
    piece row exists only after its bytes are on disk (fdatasync); state 'ready' is set in the SAME transaction as
    the last piece row (atomic completion); wraps = exactly the live devices of the named people at create time
    (minus the sending device), each to the fingerprint the sender saw.
  3 Failures: 400 bad_request / piece_short, 403 guard codes / not_your_device, 404 not_found (also someone else's
    transfer: existence is not confirmed), 409 id_taken / devices_changed / piece_conflict / not_uploading /
    not_ready / busy, 413 too_big / piece_too_long, 416 bad_piece, 422 no_devices / checksum_mismatch,
    428 checksum_required, 429 quota_count / quota_bytes, 503 relay_unavailable, 507 no_space / relay_full.
  4 Authorization: the person only from devices.guard; the device only if it is the caller's own, live device.
    Pieces and status: the sender device only. Envelope: only a device that has a wrap in this transfer, and only
    its own wrap. No drop-key-only, service-peer or token door. Every query carries the person/device predicate.
  5 Idempotence and concurrency: the client picks the transfer id (128 random bits) BEFORE create, because the AADs
    and the wrap context bind it; a create retried with the same body answers 200 resumed, any other body 409.
    Creates run in BEGIN IMMEDIATE, so the quota sums and the device-set check cannot race another create or a
    revoke. A piece retried with the same bytes is 200 already; different bytes 409 (the client's deterministic
    nonces make that a client bug). Two writers of the SAME piece at once: the second gets 409 busy (one process,
    _BUSY is the truth). Pieces of one send may arrive in any order and in parallel (pwrite at its own offset).
  6 Observability: one say() line per create / ready / refusal: transfer id cut to 8, device id to 6, sizes and
    counts only. No names, emails, person ids or key bytes. /healthz is not touched.

OUTCOME: see test_relay.py (server, in the patched image) and test_relay_e2e.py (crypto round trip, tamper cases,
crash + resume, 2 GB with a plaintext grep), and test_relay_browser.py (the browser's own send).
"""
from __future__ import annotations

import asyncio
import base64
import errno
import hashlib
import json
import os
import pathlib
import re
import shutil
import sqlite3
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response
from starlette.concurrency import run_in_threadpool

import devices
from devices import Refuse, _answer, _json, guard

router = APIRouter()

ALG = "novashare-v1"
PLAIN_PIECE = 4 * 1048576
CT_PIECE = PLAIN_PIECE + 16
X25519_LEN, KEM_CT_LEN, WRAPPED_LEN = 32, 1088, 60
MANIFEST_MIN, MANIFEST_MAX = 12 + 256 + 16, 65536 + 12 + 16      # nonce + padded JSON + tag
EXPIRE_S = 7 * 86400
MAX_TO = 10                                                     # people per send (each up to devices.MAX_LIVE)
BODY_MAX = 256 * 1024                                           # 10 people x 10 devices of wraps fits easily
ID_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")


def _env_mb(name: str, default_mb: int) -> int:
    return int(os.environ.get(name, str(default_mb))) * 1048576


def max_mb() -> int:
    """INTAKE_MAX_MB, the same file limit as the library path (live: 10240)."""
    return int(os.environ.get("INTAKE_MAX_MB", "2048"))


def open_cap() -> int:
    """Per-person bytes in uploading + ready sends (design default 2 x MAX_MB)."""
    return _env_mb("INTAKE_RELAY_OPEN_MB", 2 * max_mb())


def relay_cap() -> int:
    """All relay bytes together (design default 50 GB)."""
    return _env_mb("INTAKE_RELAY_CAP_MB", 51200)


def max_open() -> int:
    return int(os.environ.get("INTAKE_RELAY_MAX_OPEN", "20"))


def relay_dir() -> pathlib.Path:
    env = os.environ.get("INTAKE_RELAY_DIR", "").strip()
    if env:
        return pathlib.Path(env)
    import resumable                     # constants only (no import-time side effects since 2026-11-23)
    return resumable.STAGE.parent / ".novashare-relay"


def reserve() -> int:
    import resumable
    return resumable.RESERVE


def _say(msg: str) -> None:
    devices._say(msg)


def data_path(tid: str) -> pathlib.Path:
    """RELAY/<id>/data for a checked id; 404 for anything else (the normpath wall, as resumable._staged)."""
    if not ID_RE.match(tid or ""):
        raise Refuse(404, "not_found", "No such transfer.")
    base = os.path.normpath(str(relay_dir()))
    p = os.path.normpath(os.path.join(base, tid, "data"))
    if not p.startswith(base + os.sep):
        raise Refuse(404, "not_found", "No such transfer.")
    return pathlib.Path(p)


# ------------------------------------------------------------------ storage (same file as Step 1)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transfer(
  id TEXT PRIMARY KEY CHECK(length(id)=22),
  sender_person TEXT NOT NULL, sender_device TEXT NOT NULL CHECK(length(sender_device)=22),
  body_hash TEXT NOT NULL CHECK(length(body_hash)=64), alg TEXT NOT NULL CHECK(alg='novashare-v1'),
  size_ct INTEGER NOT NULL CHECK(size_ct>=16), piece_ct INTEGER NOT NULL CHECK(piece_ct=4194320),
  pieces INTEGER NOT NULL CHECK(pieces>=1 AND pieces=(size_ct+piece_ct-1)/piece_ct),
  manifest BLOB NOT NULL CHECK(length(manifest) BETWEEN 284 AND 65564),
  state TEXT NOT NULL CHECK(state IN ('uploading','ready','cancelled','expired','deleted')),
  created INTEGER NOT NULL, last_activity INTEGER NOT NULL, ready_at INTEGER, expires_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS transfer_open ON transfer(sender_person) WHERE state IN ('uploading','ready');
CREATE TRIGGER IF NOT EXISTS transfer_fixed BEFORE UPDATE OF id, sender_person, sender_device, body_hash, size_ct,
  pieces, manifest, created ON transfer
  BEGIN SELECT RAISE(ABORT, 'a transfer record never changes, only its state'); END;
CREATE TRIGGER IF NOT EXISTS transfer_ready_once BEFORE UPDATE OF state ON transfer
  WHEN OLD.state <> 'uploading' AND NEW.state = 'uploading'
  BEGIN SELECT RAISE(ABORT, 'a transfer never goes back to uploading'); END;
CREATE TABLE IF NOT EXISTS wrap(
  transfer_id TEXT NOT NULL REFERENCES transfer(id) ON DELETE CASCADE,
  device_id TEXT NOT NULL CHECK(length(device_id)=22), person TEXT NOT NULL,
  device_fp TEXT NOT NULL CHECK(length(device_fp)=64),
  eph_pub BLOB NOT NULL CHECK(length(eph_pub)=32), kem_ct BLOB NOT NULL CHECK(length(kem_ct)=1088),
  wrapped_key BLOB NOT NULL CHECK(length(wrapped_key)=60),
  PRIMARY KEY(transfer_id, device_id)) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS wrap_device ON wrap(device_id);
CREATE TABLE IF NOT EXISTS piece(
  transfer_id TEXT NOT NULL REFERENCES transfer(id) ON DELETE CASCADE, idx INTEGER NOT NULL CHECK(idx>=0),
  sha256 BLOB NOT NULL CHECK(length(sha256)=32), PRIMARY KEY(transfer_id, idx)) WITHOUT ROWID;
"""
_READY: set[str] = set()


def _db() -> sqlite3.Connection:
    """devices._db() (Step 1 schema, WAL, busy timeout) plus the relay tables on first use. Never at import."""
    c = devices._db()
    key = str(devices.db_path())
    try:
        c.execute("PRAGMA foreign_keys=ON")
        if key not in _READY:
            with devices._INIT_LOCK:
                if key not in _READY:
                    c.executescript(_SCHEMA)
                    _READY.add(key)
        return c
    except sqlite3.Error as e:
        c.close()
        _say(f"ERROR relay db: {type(e).__name__}")
        raise Refuse(503, "relay_unavailable", "Sending is not available right now. Try again soon.") from None


def _my_device(c: sqlite3.Connection, person: str, did: str | None) -> sqlite3.Row:
    """The caller's own live device named in X-Device-Id, else 403 (someone else's id looks like a missing one)."""
    if not did or not ID_RE.match(did):
        raise Refuse(403, "not_your_device", "Set up this device on the Devices page first.")
    row = c.execute("SELECT * FROM device WHERE id=? AND person=? AND revoked IS NULL", (did, person)).fetchone()
    if row is None:
        raise Refuse(403, "not_your_device", "This device is not one of your devices (or it was removed).")
    return row


# ------------------------------------------------------------------ create

def geometry(size_ct) -> int:
    """Number of pieces for a ciphertext size, or Refuse. Every piece is CT_PIECE except the last (16..CT_PIECE)."""
    if not isinstance(size_ct, int) or isinstance(size_ct, bool) or size_ct < 16:
        raise Refuse(400, "bad_request", "The size is not valid.")
    pieces = -(-size_ct // CT_PIECE)
    last = size_ct - (pieces - 1) * CT_PIECE
    if not 16 <= last <= CT_PIECE:
        raise Refuse(400, "bad_request", "The size does not split into encrypted pieces.")
    if size_ct - 16 * pieces > max_mb() * 1048576:
        raise Refuse(413, "too_big", f"This file is bigger than the {max_mb() / 1024:g} GB limit.")
    return pieces


def piece_len(size_ct: int, pieces: int, n: int) -> int:
    return CT_PIECE if n < pieces - 1 else size_ct - (pieces - 1) * CT_PIECE


def _validate(body) -> dict:
    """The create body, checked and decoded; Refuse 400 on anything off. Returns a dict with raw bytes."""
    if not isinstance(body, dict):
        raise Refuse(400, "bad_request", "Send a JSON object.")
    tid = body.get("id")
    if not isinstance(tid, str) or not ID_RE.match(tid):
        raise Refuse(400, "bad_request", "The transfer id must be 22 base64url characters.")
    if body.get("alg") != ALG:
        raise Refuse(400, "bad_request", "Unknown algorithm.")
    size_ct = body.get("size_ct")
    pieces = geometry(size_ct)
    if body.get("pieces") != pieces:
        raise Refuse(400, "bad_request", "The piece count does not match the size.")
    man = body.get("manifest")
    if not isinstance(man, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", man):
        raise Refuse(400, "bad_request", "The manifest is not base64url.")
    mraw = base64.urlsafe_b64decode(man + "=" * (-len(man) % 4))
    if devices.b64u(mraw) != man or not MANIFEST_MIN <= len(mraw) <= MANIFEST_MAX or (len(mraw) - 28) % 256:
        raise Refuse(400, "bad_request", "The manifest has the wrong size.")
    to = body.get("to")
    if not isinstance(to, list) or not 1 <= len(to) <= MAX_TO or not all(isinstance(p, str) for p in to):
        raise Refuse(400, "bad_request", f"Name 1-{MAX_TO} people to send to.")
    if len(set(to)) != len(to):
        raise Refuse(400, "bad_request", "A person is named twice.")
    wraps = body.get("wraps")
    if not isinstance(wraps, list) or not 1 <= len(wraps) <= MAX_TO * devices.MAX_LIVE:
        raise Refuse(400, "bad_request", "The key wraps are missing.")
    out = []
    for w in wraps:
        if not isinstance(w, dict):
            raise Refuse(400, "bad_request", "A key wrap is not an object.")
        did, fp = w.get("device_id"), w.get("device_fp")
        if not isinstance(did, str) or not ID_RE.match(did) or not isinstance(fp, str) or not re.fullmatch(r"[0-9a-f]{64}", fp):
            raise Refuse(400, "bad_request", "A key wrap names no valid device.")
        eph = devices.b64u_dec(w.get("eph_pub"), X25519_LEN, "ephemeral X25519")
        if not devices.x25519_ok(eph):
            raise Refuse(400, "bad_key", "That ephemeral X25519 key is not usable.")
        out.append({"device_id": did, "device_fp": fp, "eph_pub": eph,
                    "kem_ct": devices.b64u_dec(w.get("kem_ct"), KEM_CT_LEN, "ML-KEM ciphertext"),
                    "wrapped_key": devices.b64u_dec(w.get("wrapped_key"), WRAPPED_LEN, "wrapped")})
    if len({w["device_id"] for w in out}) != len(out):
        raise Refuse(400, "bad_request", "A device has two key wraps.")
    canon = json.dumps({"id": tid, "alg": ALG, "size_ct": size_ct, "pieces": pieces, "manifest": man,
                        "to": sorted(to), "wraps": sorted(([w["device_id"], w["device_fp"], w["eph_pub"].hex(),
                                                            w["kem_ct"].hex(), w["wrapped_key"].hex()] for w in out))},
                       sort_keys=True, separators=(",", ":"))
    return {"id": tid, "size_ct": size_ct, "pieces": pieces, "manifest": mraw, "to": to, "wraps": out,
            "hash": hashlib.sha256(canon.encode()).hexdigest()}


def _missing(c: sqlite3.Connection, tid: str, pieces: int) -> list[int]:
    have = {r[0] for r in c.execute("SELECT idx FROM piece WHERE transfer_id=?", (tid,))}
    return [i for i in range(pieces) if i not in have]


def _reserve_file(path: pathlib.Path, size: int) -> None:
    """Create RELAY/<id>/data at full size with real blocks (fallocate), so a full disk is refused now (507).
    Sparse only where the file system cannot allocate (as resumable.start). O_EXCL: a leftover is never reused."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        try:
            os.posix_fallocate(fd, 0, size)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise Refuse(507, "no_space", "The server is short of space. Try again later.") from None
            if e.errno not in (errno.EOPNOTSUPP, errno.EINVAL):
                raise
            os.ftruncate(fd, size)
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _drop_files(path)
        raise
    os.close(fd)


def _drop_files(path: pathlib.Path) -> None:
    try:
        path.unlink(missing_ok=True)
        path.parent.rmdir()
    except OSError:
        pass


def do_create(person: str, did: str | None, body) -> tuple[int, dict]:
    v = _validate(body)
    tid, size_ct, pieces = v["id"], v["size_ct"], v["pieces"]
    house = devices._household()
    if any(p not in house for p in v["to"]):
        raise Refuse(400, "bad_request", "Someone named is not in the household.")
    root = relay_dir()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    c = _db()
    made = None
    try:
        c.execute("BEGIN IMMEDIATE")      # one writer: quota sums, device set and insert see the same state
        me = _my_device(c, person, did)
        old = c.execute("SELECT * FROM transfer WHERE id=?", (tid,)).fetchone()
        if old is not None:
            c.execute("ROLLBACK")
            if old["sender_person"] == person and old["sender_device"] == did and old["body_hash"] == v["hash"]:
                if old["state"] not in ("uploading", "ready"):
                    raise Refuse(409, "not_uploading", "This send was stopped. Start a new one.")
                return 200, {"id": tid, "resumed": True, "state": old["state"], "pieces": pieces,
                             "missing": _missing(c, tid, pieces), "expires_at": old["expires_at"]}
            _say(f"relay refused create t={tid[:8]} code=id_taken")
            raise Refuse(409, "id_taken", "That transfer id is already used. Start the send again.")
        # the wraps must be exactly the live devices of the named people (not the sending device), at the
        # fingerprints the sender saw: a device added, re-keyed or removed since the sender looked -> 409.
        live = {}
        empty = []
        for p in v["to"]:
            rows = c.execute("SELECT id, fingerprint FROM device WHERE person=? AND revoked IS NULL AND id<>?",
                             (p, did)).fetchall()
            if not rows:
                empty.append(p)
            for r in rows:
                live[r["id"]] = (p, r["fingerprint"])
        if empty:
            c.execute("ROLLBACK")
            raise Refuse(422, "no_devices", "These people have no device set up yet: " + ", ".join(
                house[p] for p in empty) + ".")
        sent = {w["device_id"]: w["device_fp"] for w in v["wraps"]}
        if set(sent) != set(live) or any(live[d][1] != fp for d, fp in sent.items()):
            c.execute("ROLLBACK")
            _say(f"relay refused create t={tid[:8]} code=devices_changed")
            raise Refuse(409, "devices_changed", "Someone's devices changed. Reload and send again.")
        n_open, b_open = c.execute("SELECT count(*), coalesce(sum(size_ct),0) FROM transfer WHERE sender_person=?"
                                   " AND state IN ('uploading','ready')", (person,)).fetchone()
        if n_open >= max_open():
            c.execute("ROLLBACK")
            raise Refuse(429, "quota_count", "Too many sends are still open. Let some finish first.")
        if b_open + size_ct > open_cap():
            c.execute("ROLLBACK")
            raise Refuse(429, "quota_bytes", "Too much of yours is still waiting on the server. Let some finish first.")
        total = c.execute("SELECT coalesce(sum(size_ct),0) FROM transfer WHERE state IN ('uploading','ready')").fetchone()[0]
        if total + size_ct > relay_cap():
            c.execute("ROLLBACK")
            raise Refuse(507, "relay_full", "The relay is full right now. Try again later.")
        if shutil.disk_usage(root).free < size_ct + reserve():
            c.execute("ROLLBACK")
            raise Refuse(507, "no_space", "The server is short of space. Try again later.")
        now = int(time.time())
        c.execute("INSERT INTO transfer(id,sender_person,sender_device,body_hash,alg,size_ct,piece_ct,pieces,manifest,"
                  "state,created,last_activity,expires_at) VALUES(?,?,?,?,?,?,?,?,?,'uploading',?,?,?)",
                  (tid, person, me["id"], v["hash"], ALG, size_ct, CT_PIECE, pieces, v["manifest"], now, now,
                   now + EXPIRE_S))
        c.executemany("INSERT INTO wrap(transfer_id,device_id,person,device_fp,eph_pub,kem_ct,wrapped_key)"
                      " VALUES(?,?,?,?,?,?,?)",
                      [(tid, w["device_id"], live[w["device_id"]][0], w["device_fp"], w["eph_pub"], w["kem_ct"],
                        w["wrapped_key"]) for w in v["wraps"]])
        made = data_path(tid)
        _reserve_file(made, size_ct)      # inside the transaction: ENOSPC rolls the rows back too
        c.execute("COMMIT")
        made = None
        _say(f"relay create t={tid[:8]} d={did[:6]} pieces={pieces} devs={len(v['wraps'])} size={size_ct}")
        return 201, {"id": tid, "state": "uploading", "pieces": pieces, "piece_ct": CT_PIECE,
                     "missing": list(range(pieces)), "expires_at": now + EXPIRE_S}
    except FileExistsError:
        made = None                       # a leftover folder with this id is not ours to delete
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise Refuse(409, "id_taken", "That transfer id is already used. Start the send again.") from None
    except sqlite3.Error as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        _say(f"ERROR relay create: {type(e).__name__}")
        raise Refuse(503, "relay_unavailable", "Sending is not available right now. Try again soon.") from None
    except OSError as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        _say(f"ERROR relay create file: errno={e.errno}")
        raise Refuse(507 if e.errno == errno.ENOSPC else 503, "no_space" if e.errno == errno.ENOSPC else
                     "relay_unavailable", "The server could not prepare space for this send. Try again later.") from None
    finally:
        if c.in_transaction:              # any Refuse raised mid-transaction: nothing of this create is kept
            c.execute("ROLLBACK")
        if made is not None:              # ... and no file without its row
            _drop_files(made)
        c.close()


# ------------------------------------------------------------------ status, pieces, envelope

def _db_errors(fn):
    """sqlite3.Error -> 503 relay_unavailable (audit 2026-11-24: status/piece/envelope reads gave a bare 500)."""
    import functools

    @functools.wraps(fn)
    def run(*a, **k):
        try:
            return fn(*a, **k)
        except sqlite3.Error as e:
            _say(f"ERROR relay {fn.__name__}: {type(e).__name__}")
            raise Refuse(503, "relay_unavailable", "Sending is not available right now. Try again soon.") from None
    return run


def _sender_transfer(c: sqlite3.Connection, person: str, did: str, tid: str) -> sqlite3.Row:
    if not ID_RE.match(tid):
        raise Refuse(404, "not_found", "No such transfer.")
    row = c.execute("SELECT * FROM transfer WHERE id=? AND sender_person=? AND sender_device=?",
                    (tid, person, did)).fetchone()
    if row is None:                       # someone else's id answers exactly like a missing one
        raise Refuse(404, "not_found", "No such transfer.")
    return row


@_db_errors
def do_status(person: str, did: str | None, tid: str) -> dict:
    c = _db()
    try:
        _my_device(c, person, did)
        t = _sender_transfer(c, person, did, tid)
        return {"id": tid, "state": t["state"], "pieces": t["pieces"], "size_ct": t["size_ct"],
                "missing": _missing(c, tid, t["pieces"]) if t["state"] == "uploading" else [],
                "expires_at": t["expires_at"]}
    finally:
        c.close()


@_db_errors
def _piece_pre(person: str, did: str | None, tid: str, n: int) -> tuple[int, int, bytes | None, str]:
    """(expected length, size_ct, sha of a stored copy or None, state) for piece n; Refuse otherwise."""
    c = _db()
    try:
        _my_device(c, person, did)
        t = _sender_transfer(c, person, did, tid)
        if not 0 <= n < t["pieces"]:
            raise Refuse(416, "bad_piece", "No such piece.")
        r = c.execute("SELECT sha256 FROM piece WHERE transfer_id=? AND idx=?", (tid, n)).fetchone()
        return piece_len(t["size_ct"], t["pieces"], n), t["size_ct"], (r[0] if r else None), t["state"]
    finally:
        c.close()


def _write_piece(tid: str, n: int, data: bytes) -> None:
    """pwrite at n * CT_PIECE and fdatasync: the piece row that follows promises these bytes are on disk."""
    fd = os.open(data_path(tid), os.O_WRONLY)
    try:
        if os.pwrite(fd, data, n * CT_PIECE) != len(data):
            raise OSError(errno.EIO, "short write")
        os.fdatasync(fd)
    finally:
        os.close(fd)


def _commit_piece(person: str, did: str, tid: str, n: int, sha: bytes) -> dict:
    c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        t = _sender_transfer(c, person, did, tid)
        if t["state"] != "uploading":
            c.execute("ROLLBACK")
            raise Refuse(409, "not_uploading", "This send is not taking pieces any more.")
        old = c.execute("SELECT sha256 FROM piece WHERE transfer_id=? AND idx=?", (tid, n)).fetchone()
        if old is not None:               # cannot happen under _BUSY; kept so the row is never replaced
            c.execute("ROLLBACK")
            if old[0] == sha:
                return {"piece": n, "already": True, "state": t["state"]}
            raise Refuse(409, "piece_conflict", "This piece arrived before with different bytes.")
        now = int(time.time())
        c.execute("INSERT INTO piece(transfer_id,idx,sha256) VALUES(?,?,?)", (tid, n, sha))
        got = c.execute("SELECT count(*) FROM piece WHERE transfer_id=?", (tid,)).fetchone()[0]
        state = "uploading"
        if got == t["pieces"]:            # atomic completion: the last row and 'ready' commit together
            c.execute("UPDATE transfer SET state='ready', ready_at=?, last_activity=? WHERE id=? AND state='uploading'",
                      (now, now, tid))
            state = "ready"
        else:
            c.execute("UPDATE transfer SET last_activity=? WHERE id=?", (now, tid))
        c.execute("COMMIT")
        if state == "ready":
            _say(f"relay ready t={tid[:8]} pieces={got}")
        return {"piece": n, "received": got, "of": t["pieces"], "state": state}
    except sqlite3.Error as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        _say(f"ERROR relay piece: {type(e).__name__}")
        raise Refuse(503, "relay_unavailable", "Sending is not available right now. Try again soon.") from None
    finally:
        c.close()


@_db_errors
def do_envelope(person: str, did: str | None, tid: str) -> dict:
    """THIS device's wrap, the manifest blob and the sender device's public key. Nothing for anyone else."""
    if not ID_RE.match(tid):
        raise Refuse(404, "not_found", "No such transfer.")
    c = _db()
    try:
        me = _my_device(c, person, did)
        row = c.execute("SELECT t.*, w.eph_pub, w.kem_ct, w.wrapped_key, w.device_fp FROM wrap w JOIN transfer t"
                        " ON t.id=w.transfer_id WHERE w.transfer_id=? AND w.device_id=? AND w.person=?",
                        (tid, me["id"], person)).fetchone()
        if row is None:
            _say(f"relay refused envelope t={tid[:8]} d={me['id'][:6]} code=not_found")
            raise Refuse(404, "not_found", "No such transfer.")
        if row["state"] != "ready":
            raise Refuse(409, "not_ready", "This send has not finished uploading.")
        s = c.execute("SELECT id, person, label, x25519_pub, fingerprint FROM device WHERE id=?",
                      (row["sender_device"],)).fetchone()
        return {"id": tid, "alg": row["alg"], "size_ct": row["size_ct"], "pieces": row["pieces"], "piece_ct": CT_PIECE,
                "created": row["created"], "expires_at": row["expires_at"], "manifest": devices.b64u(row["manifest"]),
                "sender": {"person": s["person"], "device_id": s["id"], "label": s["label"],
                           "x25519_pub": devices.b64u(s["x25519_pub"]), "fingerprint": s["fingerprint"]},
                "wrap": {"device_id": me["id"], "device_fp": row["device_fp"], "eph_pub": devices.b64u(row["eph_pub"]),
                         "kem_ct": devices.b64u(row["kem_ct"]), "wrapped_key": devices.b64u(row["wrapped_key"])}}
    finally:
        c.close()


# ------------------------------------------------------------------ HTTP routes

async def _json_body(request: Request, limit: int):
    if not (request.headers.get("content-type") or "").lower().startswith("application/json"):
        raise Refuse(415, "bad_request", "Send JSON.")
    raw = b""
    async for chunk in request.stream():
        raw += chunk
        if len(raw) > limit:
            raise Refuse(413, "too_big", "That request is too big.")
    try:
        return json.loads(raw)
    except ValueError:
        raise Refuse(400, "bad_request", "Send a JSON object.") from None


@router.post("/relay/api/transfers")
async def create(request: Request):
    try:
        person = guard(request)
        body = await _json_body(request, BODY_MAX)
        status, data = await run_in_threadpool(do_create, person, request.headers.get("x-device-id"), body)
        return _json(data, status)
    except Refuse as e:
        return _answer(e)


@router.get("/relay/api/transfers/{tid}")
async def status(tid: str, request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_status, person, request.headers.get("x-device-id"), tid))
    except Refuse as e:
        return _answer(e)


# (transfer id, piece) being written right now. One uvicorn process, so this set is the truth (as resumable._SEQ_BUSY).
_BUSY: set[tuple[str, int]] = set()


@router.put("/relay/api/transfers/{tid}/pieces/{n}")
async def put_piece(tid: str, n: int, request: Request):
    try:
        person = guard(request)
        did = request.headers.get("x-device-id")
        want = (request.headers.get("x-piece-sha256") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", want):
            raise Refuse(428, "checksum_required", "This piece needs its checksum.")
        expect, _, stored, state = await run_in_threadpool(_piece_pre, person, did, tid, n)
        if state != "uploading" and stored is None:
            raise Refuse(409, "not_uploading", "This send is not taking pieces any more.")
        buf = bytearray()
        async for data in request.stream():
            if len(buf) + len(data) > expect:
                raise Refuse(413, "piece_too_long", "This piece is longer than its place.")
            buf += data
        if len(buf) != expect:
            raise Refuse(400, "piece_short", "This piece was cut short. Send it again.")
        sha = hashlib.sha256(buf).digest()
        if sha.hex() != want:
            raise Refuse(422, "checksum_mismatch", "This piece was damaged on the way. Send it again.")
        if stored is not None:            # a retry: keep the copy already on disk, never rewrite it
            if stored == sha:
                return _json({"piece": n, "already": True, "state": state})
            _say(f"relay refused piece t={tid[:8]} n={n} code=piece_conflict")
            raise Refuse(409, "piece_conflict", "This piece arrived before with different bytes.")
        key = (tid, n)
        if key in _BUSY:
            raise Refuse(409, "busy", "This piece is being written right now. Try again in a moment.")
        _BUSY.add(key)
        try:
            # check again under _BUSY: a twin request may have finished between the first look and here
            _, _, stored, state = await run_in_threadpool(_piece_pre, person, did, tid, n)
            if stored is not None:
                if stored == sha:
                    return _json({"piece": n, "already": True, "state": state})
                raise Refuse(409, "piece_conflict", "This piece arrived before with different bytes.")
            if state != "uploading":
                raise Refuse(409, "not_uploading", "This send is not taking pieces any more.")
            try:
                await asyncio.to_thread(_write_piece, tid, n, bytes(buf))
            except OSError as e:
                _say(f"ERROR relay piece write t={tid[:8]} errno={e.errno}")
                if e.errno == errno.ENOSPC:
                    raise Refuse(507, "no_space", "The server is short of space. Try again later.") from None
                raise Refuse(503, "relay_unavailable", "The piece could not be saved. Send it again.") from None
            return _json(await run_in_threadpool(_commit_piece, person, did, tid, n, sha))
        finally:
            _BUSY.discard(key)
    except Refuse as e:
        return _answer(e)


@router.get("/relay/api/transfers/{tid}/envelope")
async def envelope(tid: str, request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_envelope, person, request.headers.get("x-device-id"), tid))
    except Refuse as e:
        return _answer(e)


# ------------------------------------------------------------------ the send page

import relay_js  # noqa: E402  (page script in a .py so SWAP-PATCH.sh installs it)

_JS_BYTES = relay_js.JS.encode()


def _page(name: str) -> str:
    import html
    t = html.escape(devices.themes.current().get("name", "Novashare"))
    v = devices.vendor_noble_pq
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=robots content="noindex,nofollow">
<meta name=referrer content=no-referrer><title>Send - {t}</title>
<link rel=stylesheet href="/devices/static/devices.css" integrity="{devices._sri(devices.CSS)}">
<script type=module src="/devices/static/{v.NAME}" integrity="{v.SRI}"></script>
<script type=module src="/relay/static/relay.js" integrity="{devices._sri(_JS_BYTES)}"></script>
</head><body><main>
<h1>Send</h1>
<p class=dim>{t} for {html.escape(name)}. The file is locked on this device before it leaves; the server only
ever holds scrambled pieces. Only the people you pick can open it.</p>
<p id=support class=dim role=status>Checking this device...</p>
<div id=alerts role=alert></div>
<section aria-labelledby=h-who><h2 id=h-who>To</h2><ul id=people class=plain></ul></section>
<section aria-labelledby=h-what><h2 id=h-what>What</h2>
<label for=file>File</label><input id=file type=file>
<label for=caption>Note (optional, also locked)</label><input id=caption maxlength=500 autocomplete=off>
<div class=row><button id=send type=button disabled>Lock and send</button></div></section>
<section aria-labelledby=h-progress><h2 id=h-progress>Progress</h2>
<p id=progress class=mono role=status aria-live=polite>Nothing sent yet.</p>
<ul id=pending class=plain></ul></section>
</main></body></html>"""


@router.get("/relay/send", response_class=HTMLResponse)
async def send_page(request: Request):
    try:
        person = guard(request, api=False)
    except Refuse as e:
        return HTMLResponse(f"<!doctype html><title>Send</title><p>{e.words}</p>", status_code=e.status,
                            headers=devices.PAGE_HEADERS)
    return HTMLResponse(_page(devices.portal.NAMES.get(person, person.title())), headers=devices.PAGE_HEADERS)


@router.get("/relay/static/{name}")
async def relay_static(name: str, request: Request):
    try:
        guard(request, api=False)
    except Refuse as e:
        return Response(e.code, status_code=e.status, media_type="text/plain")
    if name == "relay.js":
        return Response(_JS_BYTES, media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})
    return Response("not found", status_code=404, media_type="text/plain")
