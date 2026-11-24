"""devices.py - Novashare person-to-person E2E, Step 1: the device registry (2026-11-24).

Plan: NOVASHARE-E2E-PLAN.md step 1; design NOVASHARE-E2E-DESIGN.md (Step 0, approved); threats
NOVASHARE-E2E-THREATS.md. Step 1 as briefed is narrower than the design doc's step 1: device keys, a registry,
safety numbers and trust-on-first-use only. Not in this step (later steps): the separate e2e origin and its Caddy
block, per-person identity keys and device approval, X-Wing prekeys, signatures, push, the relay itself.

WHAT IT DOES
  Each browser makes its own keys (devices.js, served from here): an X25519 pair in WebCrypto (private key
  non-extractable) and an ML-KEM-768 pair (native WebCrypto ML-KEM if the browser has it, else the vendored
  @noble/post-quantum bundle in vendor_noble_pq.py; that secret sits in IndexedDB wrapped by a non-extractable
  AES-KW key). Only the PUBLIC keys come here. The server stores them per person and hands them to household
  members. It never sees a private key, so a database copy gives nothing away but public keys and labels.

  Trust: the server hands out the keys, so a hacked server could swap one. The client therefore (1) recomputes
  every fingerprint from the raw keys instead of believing the server's, (2) pins what it saw per person/device
  (TOFU) and raises an alert that must be acknowledged on a changed key or a new device, and (3) shows a
  12-digit safety number per pair of people, computed on the client, to compare once in person.

ROUTES (all under /devices; none linked from a public page; signed-in only)
  GET  /devices                        the Devices page (HTML, strict CSP, no inline script)
  GET  /devices/static/<file>          devices.js, devices.css, the vendored ML-KEM bundle (sha256-checked)
  POST /devices/api/register           {label, platform, x25519_pub, mlkem_pub, device_id?} -> 201 new / 200 same
                                       or re-keyed (device_id = one of MY devices: a key change, logged)
  GET  /devices/api/mine               my devices, revoked ones too
  POST /devices/api/mine/{id}/revoke   revoke one of MY devices (repeat = 200)
  GET  /devices/api/people             every household person: active device public keys, person fingerprint,
                                       and the safety number between me and them
  GET  /devices/api/people/{person}    the same for one person (404 if not in the household)

SIX ANSWERS (backend-design:design, 2026-11-24)
  1 Context: HTTP routes in the intake FastAPI app; callers are browser pages that retry on their own; one uvicorn
    process. rescan_held.py imports app every hour, so NOTHING here opens a file at import: the DB opens on the
    first request (_db()).
  2 Data: SQLite devices.db next to the resumable staging (INTAKE_DEVICES_DB overrides). Table device (CHECKs on
    every length; person/id/created fixed by trigger; revoke is one-way; a revoked row's keys are frozen) and
    key_event (ids + fingerprints only). Up to 10 live devices per person.
  3 Failures: 400 bad_request/bad_key, 403 guard codes, 404 not_found (also for someone else's device: existence
    is never confirmed), 409 revoked/device_limit, 413 too_big, 429 rate_limited, 503 devices_unavailable (DB).
    Body: {"error": plain words, "code": stable code}.
  4 Authorization: the person comes ONLY from X-Auth-Email (set by Caddy forward_auth) mapped like app._person;
    X-Auth-Level must be "full" even for reads; API calls must carry Sec-Fetch-Site: same-origin and
    X-Novashare: 1 (a custom header forces a CORS preflight, so another site cannot call it). No drop-key-only,
    service-peer or token door applies here. Every query carries the person predicate.
  5 Idempotence: same keys re-sent = the same device (200); revoke twice = 200; re-key with the same keys = no
    change. Writes run in BEGIN IMMEDIATE, so two registrations cannot both slip under the device limit.
  6 Observability: one say() line per change, device ids cut to 6 characters, fingerprints to 8, no emails and
    no person ids (design decision 9); WARNING-ish lines for refusals; /healthz is not touched (no new failure
    can turn the library path's health red).

SAFETY NUMBER (both sides compute the same; devices.js has the twin of every function below)
  device fingerprint = SHA-256("novashare/device/v1" || lp(person) || lp(device_id) || lp(x25519) || lp(mlkem))
  person fingerprint = SHA-256("novashare/person/v1" || lp(person) || lp(fp_1) || ... )  devices sorted by id
  half(person)       = h0 = "novashare/safety/v1" || lp(person) || pfp; 5200 x h = SHA-512(h || pfp);
                       first 5 bytes as a big-endian number mod 10^6, 6 digits  (Signal's iterated-hash idea)
  pair number        = half(lower id) + half(higher id) -> "dddd dddd dddd" (symmetric by construction)
  lp(x) = 4-byte big-endian length || x. The person fingerprint covers all active devices, so the number changes
  when either person adds, re-keys or revokes a device: that is intended (compare again after an alert).
  Strength: 12 digits are ~40 bits; a key-swapping server must also make both halves match, and a mismatch shows
  up the first time the two people compare. Good enough for a household; the design's identity keys (later step)
  make the number stable across device changes.

STEP 6 (2026-11-24, sweep.py): the page gained RELAY_LINE, Jason's one-line relay summary (server-rendered, numbers
only); nothing else here changed.
STEP 4 (2026-11-24, push.py): this file only gained PUSH_TAG / PUSH_SECTION in the page (the "Notifications" box);
revoke also deletes the device's push subscription, by a trigger push.py adds. Every Step 1 route and rule is unchanged.

OUTCOME: see test_devices.py (API, auth boundaries) and test_devices_browser.py (two browsers, Playwright).
"""
from __future__ import annotations

import base64
import collections
import functools
import hashlib
import json
import os
import pathlib
import re
import secrets
import sqlite3
import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.concurrency import run_in_threadpool

import portal
import themes
import vendor_noble_pq

router = APIRouter()

X25519_LEN, MLKEM_PUB_LEN = 32, 1184
MAX_LIVE = int(os.environ.get("INTAKE_DEVICES_MAX", "10"))
REG_PER_HOUR = int(os.environ.get("INTAKE_DEVICES_REG_PER_HOUR", "20"))
BODY_MAX = 16384                       # a register body is about 1.7 KB (1184-byte key in base64url)
SAFETY_ITER = 5200
ID_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")
PLATFORM_RE = re.compile(r"[^A-Za-z0-9 ._()/-]")

# Small-order X25519 points (RFC 7748 section 6.1 / the libsodium blocklist), top bit masked as X25519 itself does.
# A public key from this set makes every shared secret predictable (all zero), so it is refused at register.
_P = 2 ** 255 - 19
_SMALL_ORDER = {
    0, 1, _P - 1, _P, _P + 1,
    325606250916557431795983626356110631294008115727848805560023387167927233504,
    39382357235489614581723060781553021112529911719440698176882885853963445705823,
}


# ------------------------------------------------------------------ errors and the request guard

class Refuse(Exception):
    """A refusal with a stable code; turned into {"error", "code"} JSON by _answer."""

    def __init__(self, status: int, code: str, words: str):
        super().__init__(code)
        self.status, self.code, self.words = status, code, words


def _say(msg: str) -> None:
    """app.say if the app is loaded (it is, under uvicorn and the tests), else print. Never emails or keys."""
    import sys
    app = sys.modules.get("app")
    (app.say if app is not None and hasattr(app, "say") else print)(msg)


def _json(data, status: int = 200) -> JSONResponse:
    return JSONResponse(data, status_code=status, headers={"Cache-Control": "no-store"})


def _answer(err: Refuse) -> JSONResponse:
    return _json({"error": err.words, "code": err.code}, err.status)


def _person_for(email: str | None) -> str | None:
    """The same mapping as app._person (portal.WHO, from drop.toml). Kept here so this module never imports app."""
    if not email:
        return None
    return portal.WHO.get(email.strip().lower())


def _household() -> dict[str, str]:
    """person id -> display name, for the people who can sign in (have at least one email in drop.toml)."""
    return {p: portal.NAMES.get(p, p.title()) for p in sorted(set(portal.WHO.values()))}


def guard(request: Request, api: bool = True) -> str:
    """The signed-in person for this request, or Refuse. The only way a handler learns who is asking.

    api=True (every /devices/api route): Sec-Fetch-Site must be exactly same-origin (missing = refused, fails
    closed) and X-Novashare: 1 must be present. The page itself (api=False) may be a fresh navigation (none)."""
    h = request.headers
    if api:
        if (h.get("sec-fetch-site") or "").lower() != "same-origin":
            raise Refuse(403, "bad_origin", "This can only be used from the Novashare Devices page.")
        if h.get("x-novashare") != "1":
            raise Refuse(403, "bad_origin", "This can only be used from the Novashare Devices page.")
    if (h.get("x-auth-level") or "").strip().lower() != "full":
        raise Refuse(403, "not_full", "Your access here is view-only.")
    person = _person_for(h.get("x-auth-email"))
    if not person:
        raise Refuse(403, "not_in_household", "This sign-in is not one of the household's people.")
    return person


# ------------------------------------------------------------------ key checks and fingerprints

def b64u_dec(s, want: int, what: str) -> bytes:
    """Strict base64url (no padding) of exactly `want` bytes, else Refuse 400 bad_key."""
    if not isinstance(s, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", s):
        raise Refuse(400, "bad_key", f"The {what} key is not base64url.")
    try:
        raw = base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    except ValueError:
        raise Refuse(400, "bad_key", f"The {what} key is not base64url.") from None
    if len(raw) != want or b64u(raw) != s:          # the second test refuses non-canonical encodings
        raise Refuse(400, "bad_key", f"The {what} key must be {want} bytes.")
    return raw


def b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def x25519_ok(pub: bytes) -> bool:
    """False for the small-order points (shared secret would be fixed), compared with bit 255 masked."""
    return int.from_bytes(pub, "little") & ((1 << 255) - 1) not in _SMALL_ORDER


def mlkem_ok(pub: bytes) -> bool:
    """FIPS 203 section 7.2 encapsulation-key check: every 12-bit coefficient of the 3 polynomials is below q=3329."""
    t = pub[:1152]
    for i in range(0, 1152, 3):
        b0, b1, b2 = t[i], t[i + 1], t[i + 2]
        if (b0 | (b1 & 0x0F) << 8) >= 3329 or (b1 >> 4 | b2 << 4) >= 3329:
            return False
    return True


def _lp(b: bytes) -> bytes:
    return len(b).to_bytes(4, "big") + b


def device_fp(person: str, device_id: str, x: bytes, m: bytes) -> str:
    return hashlib.sha256(b"novashare/device/v1" + _lp(person.encode()) + _lp(device_id.encode())
                          + _lp(x) + _lp(m)).hexdigest()


def person_fp(person: str, devices: list[dict]) -> str | None:
    """Over the person's ACTIVE devices sorted by id; None when they have none (no safety number then)."""
    if not devices:
        return None
    h = hashlib.sha256(b"novashare/person/v1" + _lp(person.encode()))
    for d in sorted(devices, key=lambda d: d["id"]):
        h.update(_lp(bytes.fromhex(d["fingerprint"])))
    return h.hexdigest()


@functools.lru_cache(maxsize=512)   # audit 11-24: pure, and 5200 SHA-512 rounds per person per /people call
def _half(person: str, pfp: str) -> str:
    raw = bytes.fromhex(pfp)
    h = b"novashare/safety/v1" + _lp(person.encode()) + raw
    for _ in range(SAFETY_ITER):
        h = hashlib.sha512(h + raw).digest()
    return f"{int.from_bytes(h[:5], 'big') % 1_000_000:06d}"


def safety_number(a: str, a_fp: str | None, b: str, b_fp: str | None) -> str | None:
    """12 digits in 3 groups for the pair (a, b); the same whichever side asks. None if either has no device."""
    if not a_fp or not b_fp:
        return None
    (p1, f1), (p2, f2) = sorted([(a, a_fp), (b, b_fp)])
    d = _half(p1, f1) + _half(p2, f2)
    return f"{d[:4]} {d[4:8]} {d[8:]}"


# ------------------------------------------------------------------ storage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS device(
  id TEXT PRIMARY KEY CHECK(length(id)=22),
  person TEXT NOT NULL CHECK(length(person) BETWEEN 1 AND 32),
  label TEXT NOT NULL CHECK(length(label) BETWEEN 1 AND 40),
  platform TEXT NOT NULL DEFAULT '' CHECK(length(platform)<=40),
  x25519_pub BLOB NOT NULL CHECK(length(x25519_pub)=32),
  mlkem_pub BLOB NOT NULL CHECK(length(mlkem_pub)=1184),
  fingerprint TEXT NOT NULL CHECK(length(fingerprint)=64),
  created INTEGER NOT NULL, last_seen INTEGER NOT NULL, revoked INTEGER);
CREATE INDEX IF NOT EXISTS device_live ON device(person) WHERE revoked IS NULL;
CREATE TRIGGER IF NOT EXISTS device_fixed BEFORE UPDATE OF id, person, created ON device
  WHEN NEW.id IS NOT OLD.id OR NEW.person IS NOT OLD.person OR NEW.created IS NOT OLD.created
  BEGIN SELECT RAISE(ABORT, 'device id, person and created never change'); END;
CREATE TRIGGER IF NOT EXISTS device_revoke_once BEFORE UPDATE OF revoked ON device
  WHEN OLD.revoked IS NOT NULL AND NEW.revoked IS NOT OLD.revoked
  BEGIN SELECT RAISE(ABORT, 'a revoked device stays revoked'); END;
CREATE TRIGGER IF NOT EXISTS device_revoked_frozen BEFORE UPDATE OF x25519_pub, mlkem_pub, fingerprint ON device
  WHEN OLD.revoked IS NOT NULL
  BEGIN SELECT RAISE(ABORT, 'a revoked device keeps its keys'); END;
CREATE TABLE IF NOT EXISTS key_event(
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('registered','key_changed','revoked')),
  device_id TEXT NOT NULL, old_fp TEXT, new_fp TEXT);
PRAGMA user_version=1;
"""
_INIT_LOCK = threading.Lock()
_READY: set[str] = set()


def db_path() -> pathlib.Path:
    """INTAKE_DEVICES_DB, else devices.db beside the resumable staging (same disk, never under a library)."""
    env = os.environ.get("INTAKE_DEVICES_DB", "").strip()
    if env:
        return pathlib.Path(env)
    import resumable                    # constants only; resumable has no import-time side effects (2026-11-23)
    return resumable.STAGE / "devices.db"


def _db() -> sqlite3.Connection:
    """A fresh connection (autocommit; callers BEGIN IMMEDIATE for writes). Creates the schema on first use only."""
    path = db_path()
    try:
        if str(path) not in _READY:
            with _INIT_LOCK:
                if str(path) not in _READY:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    c = sqlite3.connect(path, timeout=5, isolation_level=None)
                    c.execute("PRAGMA journal_mode=WAL")
                    c.executescript(_SCHEMA)
                    c.close()
                    os.chmod(path, 0o600)
                    _READY.add(str(path))
        c = sqlite3.connect(path, timeout=5, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA synchronous=FULL")
        return c
    except (sqlite3.Error, OSError) as e:
        _say(f"ERROR devices db: {type(e).__name__}")
        raise Refuse(503, "devices_unavailable", "The device list is not available right now. Try again soon.") from None


def _public(row: sqlite3.Row, own: bool = False) -> dict:
    d = {"id": row["id"], "label": row["label"], "platform": row["platform"],
         "x25519_pub": b64u(row["x25519_pub"]), "mlkem_pub": b64u(row["mlkem_pub"]),
         "fingerprint": row["fingerprint"], "created": row["created"]}
    if own:
        d.update(last_seen=row["last_seen"], revoked=row["revoked"])
    return d


def _active(c: sqlite3.Connection, person: str) -> list[dict]:
    rows = c.execute("SELECT * FROM device WHERE person=? AND revoked IS NULL ORDER BY id", (person,)).fetchall()
    return [_public(r) for r in rows]


# ------------------------------------------------------------------ operations (run in the thread pool)

_RATE: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_RATE_LOCK = threading.Lock()


def _rate(person: str) -> None:
    now = time.monotonic()
    with _RATE_LOCK:
        q = _RATE[person]
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= REG_PER_HOUR:
            raise Refuse(429, "rate_limited", "Too many device changes this hour. Try again later.")
        q.append(now)


def _clean_label(v, field: str, required: bool) -> str:
    if v is None and not required:
        return ""
    if not isinstance(v, str):
        raise Refuse(400, "bad_request", f"The {field} must be text.")
    v = " ".join(v.split())                              # no control characters, newlines or runs of spaces
    if field == "platform":
        v = PLATFORM_RE.sub("", v)
    if (required and not v) or len(v) > 40:
        raise Refuse(400, "bad_request", f"The {field} must be 1-40 characters." if required
                     else f"The {field} must be at most 40 characters.")
    return v


def do_register(person: str, body: dict) -> tuple[int, dict]:
    """New device (201), the same keys again (200), or a re-key of one of MY devices (200, key_changed)."""
    if not isinstance(body, dict):
        raise Refuse(400, "bad_request", "Send a JSON object.")
    label = _clean_label(body.get("label"), "label", True)
    platform = _clean_label(body.get("platform"), "platform", False)
    x = b64u_dec(body.get("x25519_pub"), X25519_LEN, "X25519")
    m = b64u_dec(body.get("mlkem_pub"), MLKEM_PUB_LEN, "ML-KEM-768")
    if not x25519_ok(x):
        raise Refuse(400, "bad_key", "That X25519 key is not usable.")
    if not mlkem_ok(m):
        raise Refuse(400, "bad_key", "That ML-KEM-768 key is not valid (FIPS 203 check).")
    want = body.get("device_id")
    if want is not None and (not isinstance(want, str) or not ID_RE.match(want)):
        raise Refuse(404, "not_found", "No such device of yours.")
    _rate(person)
    now = int(time.time())
    c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        if want:
            row = c.execute("SELECT * FROM device WHERE id=? AND person=?", (want, person)).fetchone()
            if row is None:                              # someone else's id looks exactly like a missing one
                c.execute("ROLLBACK")
                _say(f"devices refused re-key d={want[:6]} code=not_found")
                raise Refuse(404, "not_found", "No such device of yours.")
            if row["revoked"] is not None:
                c.execute("ROLLBACK")
                raise Refuse(409, "revoked", "This device was removed. Register it again as a new device.")
            fp = device_fp(person, want, x, m)
            changed = fp != row["fingerprint"]
            c.execute("UPDATE device SET x25519_pub=?, mlkem_pub=?, fingerprint=?, label=?, platform=?, last_seen=?"
                      " WHERE id=?", (x, m, fp, label, platform, now, want))
            if changed:
                c.execute("INSERT INTO key_event(ts,kind,device_id,old_fp,new_fp) VALUES(?,?,?,?,?)",
                          (now, "key_changed", want, row["fingerprint"], fp))
            c.execute("COMMIT")
            if changed:
                _say(f"devices KEY CHANGE d={want[:6]} old={row['fingerprint'][:8]} new={fp[:8]}")
            return 200, {"device_id": want, "fingerprint": fp, "key_changed": changed}
        same = c.execute("SELECT * FROM device WHERE person=? AND x25519_pub=? AND mlkem_pub=? AND revoked IS NULL",
                         (person, x, m)).fetchone()
        if same is not None:                             # a retried register: the same device, nothing new
            c.execute("UPDATE device SET last_seen=? WHERE id=?", (now, same["id"]))
            c.execute("COMMIT")
            return 200, {"device_id": same["id"], "fingerprint": same["fingerprint"], "key_changed": False}
        live = c.execute("SELECT count(*) FROM device WHERE person=? AND revoked IS NULL", (person,)).fetchone()[0]
        if live >= MAX_LIVE:
            c.execute("ROLLBACK")
            raise Refuse(409, "device_limit", f"You already have {MAX_LIVE} devices. Remove one first.")
        did = secrets.token_urlsafe(16)                  # 22 characters, CSPRNG, server-issued
        fp = device_fp(person, did, x, m)
        c.execute("INSERT INTO device(id,person,label,platform,x25519_pub,mlkem_pub,fingerprint,created,last_seen)"
                  " VALUES(?,?,?,?,?,?,?,?,?)", (did, person, label, platform, x, m, fp, now, now))
        c.execute("INSERT INTO key_event(ts,kind,device_id,new_fp) VALUES(?,?,?,?)", (now, "registered", did, fp))
        c.execute("COMMIT")
        _say(f"devices registered d={did[:6]} fp={fp[:8]}")
        return 201, {"device_id": did, "fingerprint": fp, "key_changed": False}
    except sqlite3.Error as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        _say(f"ERROR devices register: {type(e).__name__}")
        raise Refuse(503, "devices_unavailable", "The device list is not available right now. Try again soon.") from None
    finally:
        c.close()


def do_mine(person: str, seen_id: str | None) -> dict:
    c = _db()
    try:
        now = int(time.time())
        if seen_id and ID_RE.match(seen_id):            # last_seen of the calling device, at most once a minute
            c.execute("UPDATE device SET last_seen=? WHERE id=? AND person=? AND revoked IS NULL AND last_seen<?",
                      (now, seen_id, person, now - 60))
        rows = c.execute("SELECT * FROM device WHERE person=? ORDER BY created, id", (person,)).fetchall()
        return {"person": person, "devices": [_public(r, own=True) for r in rows]}
    except sqlite3.Error as e:
        _say(f"ERROR devices mine: {type(e).__name__}")
        raise Refuse(503, "devices_unavailable", "The device list is not available right now. Try again soon.") from None
    finally:
        c.close()


def do_revoke(person: str, device_id: str) -> dict:
    if not ID_RE.match(device_id):
        raise Refuse(404, "not_found", "No such device of yours.")
    c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute("SELECT revoked, fingerprint FROM device WHERE id=? AND person=?",
                        (device_id, person)).fetchone()
        if row is None:
            c.execute("ROLLBACK")
            _say(f"devices refused revoke d={device_id[:6]} code=not_found")
            raise Refuse(404, "not_found", "No such device of yours.")
        if row["revoked"] is None:
            now = int(time.time())
            c.execute("UPDATE device SET revoked=? WHERE id=?", (now, device_id))
            c.execute("INSERT INTO key_event(ts,kind,device_id,old_fp) VALUES(?,?,?,?)",
                      (now, "revoked", device_id, row["fingerprint"]))
            c.execute("COMMIT")
            _say(f"devices revoked d={device_id[:6]}")
        else:
            c.execute("ROLLBACK")                        # already revoked: the same answer, nothing written
        return {"device_id": device_id, "state": "revoked"}
    except sqlite3.Error as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        _say(f"ERROR devices revoke: {type(e).__name__}")
        raise Refuse(503, "devices_unavailable", "The device list is not available right now. Try again soon.") from None
    finally:
        c.close()


def do_people(me: str, only: str | None = None) -> dict:
    """Household people with their ACTIVE devices' public keys, and the safety number between me and each."""
    house = _household()
    if only is not None and only not in house:
        raise Refuse(404, "not_found", "No such person in the household.")
    c = _db()
    try:
        mine = _active(c, me)
        my_fp = person_fp(me, mine)
        out = []
        for pid, name in house.items():
            if only is not None and pid != only:
                continue
            devs = mine if pid == me else _active(c, pid)
            pfp = person_fp(pid, devs)
            out.append({"id": pid, "name": name, "me": pid == me, "fingerprint": pfp, "devices": devs,
                        "safety_number": None if pid == me else safety_number(me, my_fp, pid, pfp)})
        return {"me": me, "people": out}
    except sqlite3.Error as e:
        _say(f"ERROR devices people: {type(e).__name__}")
        raise Refuse(503, "devices_unavailable", "The device list is not available right now. Try again soon.") from None
    finally:
        c.close()


# ------------------------------------------------------------------ HTTP routes

@router.post("/devices/api/register")
async def register(request: Request):
    try:
        person = guard(request)
        if not (request.headers.get("content-type") or "").lower().startswith("application/json"):
            raise Refuse(415, "bad_request", "Send JSON.")
        raw = await request.body()
        if len(raw) > BODY_MAX:
            raise Refuse(413, "too_big", "That request is too big.")
        try:
            body = json.loads(raw)
        except ValueError:
            raise Refuse(400, "bad_request", "Send a JSON object.") from None
        status, data = await run_in_threadpool(do_register, person, body)
        return _json(data, status)
    except Refuse as e:
        return _answer(e)


@router.get("/devices/api/mine")
async def mine(request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_mine, person, request.headers.get("x-device-id")))
    except Refuse as e:
        return _answer(e)


@router.post("/devices/api/mine/{device_id}/revoke")
async def revoke(device_id: str, request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_revoke, person, device_id))
    except Refuse as e:
        return _answer(e)


@router.get("/devices/api/people")
async def people(request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_people, person))
    except Refuse as e:
        return _answer(e)


@router.get("/devices/api/people/{other}")
async def one_person(other: str, request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_people, person, other))
    except Refuse as e:
        return _answer(e)


# ------------------------------------------------------------------ the page and its files

# Strict CSP: no inline script or style, nothing from another origin, Trusted Types required (the page only
# uses textContent / createElement, so no policy is needed and any innerHTML-style sink throws).
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'; require-trusted-types-for 'script'")
PAGE_HEADERS = {"Content-Security-Policy": CSP, "Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY",
                "Cross-Origin-Opener-Policy": "same-origin", "X-Robots-Tag": "noindex, nofollow"}

VENDOR = vendor_noble_pq.data()
VENDOR_OK = hashlib.sha256(VENDOR).hexdigest() == vendor_noble_pq.SHA256   # False = never served (503)

import devices_js  # noqa: E402  (the page script lives in a .py so SWAP-PATCH.sh installs it with the rest)

DEVICES_JS = devices_js.JS


def _sri(data: bytes) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode()


def _css() -> str:
    """Black / purple / orange: the theme's portal colours, or Novashare's horizon palette if the theme has none."""
    t = themes.current()
    root = t.get("portal_root") or themes.HORIZON["portal_root"]
    return (":root{" + root + "}\n" + """
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:720px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.6rem;margin:0 0 4px}
h2{font-size:1.15rem;margin:28px 0 8px}
p{margin:6px 0}
.dim{color:var(--dim)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;margin:10px 0}
.row{display:flex;flex-wrap:wrap;gap:8px 12px;align-items:center;justify-content:space-between}
.mono{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:.95rem;overflow-wrap:anywhere}
.safety{font-family:ui-monospace,SFMono-Regular,Consolas,monospace;font-size:1.35rem;letter-spacing:.08em;color:var(--accent)}
button{font:inherit;min-height:44px;padding:8px 16px;border-radius:10px;border:1px solid var(--accent);
  background:var(--accent);color:var(--on-accent);cursor:pointer}
button.quiet{background:transparent;color:var(--accent)}
button.warn{background:var(--warn);border-color:var(--warn);color:#07050d}
button:focus-visible,input:focus-visible{outline:3px solid var(--warn);outline-offset:2px}
input{font:inherit;min-height:44px;padding:8px 12px;border-radius:10px;border:1px solid var(--line);
  background:var(--bg);color:var(--ink);width:min(100%,320px)}
label{display:block;margin:8px 0 4px}
.alert{border:2px solid var(--warn);background:var(--warn-soft)}
.alert strong{color:var(--warn)}
.bad{color:var(--bad)}
.ok{color:var(--ok)}
.tag{font-size:.85rem;border:1px solid var(--line);border-radius:999px;padding:1px 10px;color:var(--dim)}
ul.plain{list-style:none;padding:0;margin:0}
""")


CSS = _css().encode()
_JS_BYTES = DEVICES_JS.encode()

# Step 4 (push.py, 2026-11-24): push.py sets these at import to its script tag (with SRI) and its "Notifications" box.
# Empty = the page is exactly Step 1's.
PUSH_TAG = ""
PUSH_SECTION = ""
# Step 6 (sweep.py, 2026-11-24): sweep.py sets this to a function(person) -> HTML for the admin's "Relay" line (live
# transfers, bytes held, last sweep); it answers "" for everyone else. None = the page is exactly Step 4's.
RELAY_LINE = None


def _page(name: str, extra: str = "") -> str:
    import html
    t = html.escape(themes.current().get("name", "Novashare"))
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><meta name=robots content="noindex,nofollow">
<meta name=referrer content=no-referrer><title>Devices - {t}</title>
<link rel=stylesheet href="/devices/static/devices.css" integrity="{_sri(CSS)}">
<script type=module src="/devices/static/{vendor_noble_pq.NAME}" integrity="{vendor_noble_pq.SRI}"></script>
<script type=module src="/devices/static/devices.js" integrity="{_sri(_JS_BYTES)}"></script>
{PUSH_TAG}
</head><body><main>
<h1>Devices</h1>
<p class=dim>{t} for {html.escape(name)}. Each device makes its own keys; only the public half leaves it.</p>
<p id=support class=dim role=status>Checking this browser...</p>
<section aria-labelledby=h-alerts><h2 id=h-alerts hidden>Key alerts</h2><div id=alerts role=alert></div></section>
<section aria-labelledby=h-this><h2 id=h-this>This device</h2><div id=this class=card><p class=dim>Loading...</p></div></section>
<section aria-labelledby=h-mine><h2 id=h-mine>My devices</h2><ul id=mine class=plain></ul></section>
<section aria-labelledby=h-people><h2 id=h-people>Household</h2>
<p class=dim>Compare the safety number with each person once, face to face. If it matches, nobody swapped a key.</p>
<ul id=people class=plain></ul></section>
{PUSH_SECTION}
{extra}</main></body></html>"""


@router.get("/devices", response_class=HTMLResponse)
async def devices_page(request: Request):
    try:
        person = guard(request, api=False)
    except Refuse as e:
        return HTMLResponse(f"<!doctype html><title>Devices</title><p>{e.words}</p>", status_code=e.status,
                            headers=PAGE_HEADERS)
    extra = ""
    if RELAY_LINE is not None:
        try:
            extra = RELAY_LINE(person)
        except Exception as e:  # noqa: BLE001  (a stats problem never breaks the Devices page)
            _say(f"ERROR devices relay line: {type(e).__name__}")
    return HTMLResponse(_page(portal.NAMES.get(person, person.title()), extra), headers=PAGE_HEADERS)


@router.get("/devices/static/{name}")
async def devices_static(name: str, request: Request):
    """Only the three known files; signed-in household people only (same rule as the page)."""
    try:
        guard(request, api=False)
    except Refuse as e:
        return Response(e.code, status_code=e.status, media_type="text/plain")
    hdr = {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
    if name == "devices.js" and _JS_BYTES:
        return Response(_JS_BYTES, media_type="text/javascript", headers=hdr)
    if name == "devices.css":
        return Response(CSS, media_type="text/css", headers=hdr)
    if name == vendor_noble_pq.NAME:
        if not VENDOR_OK:
            _say("ERROR devices: vendored ML-KEM bundle failed its sha256 check; not served")
            return Response("integrity check failed", status_code=503, media_type="text/plain")
        return Response(VENDOR, media_type="text/javascript", headers=hdr)
    return Response("not found", status_code=404, media_type="text/plain")
