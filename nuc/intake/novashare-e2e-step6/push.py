"""push.py - Novashare person-to-person E2E, Step 4: web push notifications (2026-11-24).

Plan: NOVASHARE-E2E-PLAN.md step 4 ("VAPID keys, one subscription per device, payload without names; the phone gets
the notification; Accept works from it"). Threats: NOVASHARE-E2E-THREATS.md #6 (opaque push) and "VAPID key theft:
fake notifications only, no decryption". Builds on steps 1-3 (devices.py, relay.py, receive.py; all live 2026-11-24).

WHAT IT DOES
  A device that the person registered (Step 1) can subscribe to push after a tap on "Notify me on this device" (push_js.py,
  on the Devices and Inbox pages). The browser's subscription (endpoint + p256dh + auth) is stored for THAT device only.
  When a send to the person becomes ready, every subscribed receiving device gets a push; when a receiving device saves or
  declines it, the sender's subscribed devices get one. The payload is encrypted to the browser (RFC 8291 aes128gcm, by
  pywebpush) and holds no names, captions, file names or sizes: {"v":1,"kind":"new","id":<transfer id>,"body":"You have
  a new Novashare item"}. The service worker (receive_js.SW) shows a fixed text per kind; Accept opens
  /relay/inbox#<id>, where the page unlocks and decrypts as in Step 3. The push service (Google, Mozilla, Apple,
  Microsoft) therefore learns only that a device got some message, and when.

QUEUE: an outbox table, filled by SQLite triggers in the SAME transaction as the event (a transfer turning 'ready' in
  relay._commit_piece; a delivery turning 'downloaded' / 'declined' in receive.do_move). relay.py and receive.py are not
  changed. One daemon thread (started with the app, never at import: rescan_held.py imports app hourly) drains it, so
  no request ever waits for a push service.

VAPID: VAPID_PRIVATE_KEY (base64url of the raw 32-byte P-256 scalar) comes from Proton Pass through ~/bin/dc and
  secrets.refs; it is never logged or served. The public key is derived from it and served at /relay/api/push/key. Each
  subscription remembers which key it was made with (kid = first 16 hex of sha256(public key)); after a key change the
  old subscriptions are dropped (the push service would refuse them) and the page subscribes again. No key = push off
  (routes answer enabled:false / 503 push_off; nothing else changes).

ROUTES (all devices.guard: signed in, X-Auth-Level full, Sec-Fetch-Site same-origin, X-Novashare: 1; (D) = the caller's
own live device in X-Device-Id; no drop-key-only, token or service-peer door)
  GET    /relay/api/push/key        {enabled, public_key}            (public key only)
  GET    /relay/api/push/sub (D)    {subscribed, current_key}         THIS device's subscription state
  PUT    /relay/api/push/sub (D)    {endpoint, keys:{p256dh, auth}}   subscribe / replace THIS device's subscription
  DELETE /relay/api/push/sub (D)    unsubscribe THIS device (repeat = 200)
  POST   /relay/api/push/test (D)   one test push to THIS device (5 per 10 minutes)
  GET    /relay/push/push.js        the client script (SRI in the Devices and Inbox pages)

SIX ANSWERS (backend-design:design + security-discipline, 2026-11-24)
  1 Context: routes in the intake FastAPI app plus one worker thread in the same uvicorn process. Load: a household,
    a few pushes per send; one thread and a 2 s poll of an indexed table are plenty.
  2 Data: push_sub (one row per device: PRIMARY KEY device_id; endpoint UNIQUE) and push_out (the outbox) in devices.db.
    Invariants: a subscription exists only for a live device of its person (revoke trigger deletes it and its queued
    pushes); an outbox row names a device and a kind, never a name.
  3 Failures: 400 bad_request, 403 guard / not_your_device, 409 endpoint_taken (another person's device holds that
    endpoint), 413 too_big, 429 rate_limited, 503 push_off / relay_unavailable. Push service answers: 2xx sent;
    404/410 the subscription is gone -> deleted; 429/5xx/network -> ONE retry after 30 s (or Retry-After up to 10 min),
    then dropped; other 4xx -> dropped and logged. A queued push older than 1 day is dropped.
  4 Authorization: subscribe/unsubscribe/test only for the caller's own live device (relay._my_device, person + device
    predicate in every query). Endpoints must be https on a known push service (fcm.googleapis.com,
    updates.push.services.mozilla.com, *.push.apple.com, *.notify.windows.com): the server POSTs to it, so anything else
    would be an SSRF door. INTAKE_PUSH_TEST_ORIGINS (sandbox only) adds exact extra origins and is logged at start.
  5 Idempotence: PUT of the same subscription = the same row; DELETE twice = 200; the outbox row is deleted only after
    the push service answered, so a crash re-sends (the notification tag makes a repeat replace, not add).
  6 Observability: one say() line per subscribe / unsubscribe / push result: kind, transfer id cut to 8, device id to 6,
    the push service HOST only (the endpoint path is a capability URL and is never logged), HTTP status.

iPhone / iPad: web push works only in a Home Screen web app (iOS / iPadOS 16.4+). In Safari tabs PushManager is absent;
the page says so (push_js.py) and README.md documents it.

STEP 6 (2026-11-24, sweep.py: sender cancel): a fourth kind, 'withdrawn' ("A Novashare item was withdrawn"), queued
by the trigger push_on_cancel in the SAME transaction as a ready send turning 'cancelled', for every subscribed live
device whose delivery was still pending or accepted. Same payload rule: kind, id, fixed text; no names. The outbox
table's CHECK gained the kind: _migrate() rebuilds push_out once (copy, drop, rename, in one transaction; the
triggers that write to it name it and keep working). OUTCOME: test_sweep.py.

OUTCOME: test_push.py (server rules, in the patched image) and test_push_e2e.py (sandbox: real uvicorn + a mock push
service; the captured payload is decrypted with the test subscription's keys and checked).
"""
from __future__ import annotations

import collections
import contextlib
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

import devices
import receive
import relay
from devices import Refuse, _answer, _json, guard

router = APIRouter()

PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.apple.com", "notify.windows.com")
ENDPOINT_MAX = 1024
BODY_MAX = 4096
POLL_S = 2.0
RETRY_S = 30
RETRY_MAX_S = 600
STALE_S = 86400
TIMEOUT_S = 10
TTL_S = 86400
TEST_PER_10MIN = 5
TEXT = {"new": "You have a new Novashare item", "delivered": "Your Novashare item was delivered",
        "test": "Notifications work on this device", "withdrawn": "A Novashare item was withdrawn"}
KINDS = "('new','delivered','test','withdrawn')"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS push_sub(
  device_id TEXT PRIMARY KEY CHECK(length(device_id)=22) REFERENCES device(id) ON DELETE CASCADE,
  person TEXT NOT NULL,
  endpoint TEXT NOT NULL UNIQUE CHECK(length(endpoint) BETWEEN 12 AND 1024),
  p256dh BLOB NOT NULL CHECK(length(p256dh)=65), auth BLOB NOT NULL CHECK(length(auth)=16),
  kid TEXT NOT NULL CHECK(length(kid)=16), created INTEGER NOT NULL, updated INTEGER NOT NULL) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS push_out(
  id INTEGER PRIMARY KEY, device_id TEXT NOT NULL CHECK(length(device_id)=22),
  kind TEXT NOT NULL CHECK(kind IN ('new','delivered','test','withdrawn')),
  transfer_id TEXT CHECK(transfer_id IS NULL OR length(transfer_id)=22),
  result TEXT CHECK(result IS NULL OR result IN ('saved','declined')),
  created INTEGER NOT NULL, tries INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS push_out_due ON push_out(next_at);
-- revoke (devices.do_revoke): the device's subscription and queued pushes go in the same transaction
CREATE TRIGGER IF NOT EXISTS push_sub_revoked AFTER UPDATE OF revoked ON device WHEN NEW.revoked IS NOT NULL
  BEGIN DELETE FROM push_sub WHERE device_id=NEW.id; DELETE FROM push_out WHERE device_id=NEW.id; END;
-- a send turned ready (relay._commit_piece): one 'new' push per subscribed, live, still-pending receiving device
CREATE TRIGGER IF NOT EXISTS push_on_ready AFTER UPDATE OF state ON transfer
  WHEN OLD.state='uploading' AND NEW.state='ready'
  BEGIN INSERT INTO push_out(device_id, kind, transfer_id, created, next_at)
    SELECT s.device_id, 'new', NEW.id, CAST(strftime('%s','now') AS INTEGER), CAST(strftime('%s','now') AS INTEGER)
    FROM delivery d JOIN push_sub s ON s.device_id=d.device_id AND s.person=d.person
    JOIN device v ON v.id=d.device_id AND v.revoked IS NULL
    WHERE d.transfer_id=NEW.id AND d.state='pending'; END;
-- a live receiving device saved or declined (receive.do_move): one 'delivered' push per subscribed sender device.
-- The bulk 'declined' that receive._finish_if_done writes for REMOVED devices does not count (device check).
CREATE TRIGGER IF NOT EXISTS push_on_delivered AFTER UPDATE OF state ON delivery
  WHEN OLD.state IN ('pending','accepted') AND NEW.state IN ('downloaded','declined')
    AND EXISTS(SELECT 1 FROM device WHERE id=NEW.device_id AND revoked IS NULL)
  BEGIN INSERT INTO push_out(device_id, kind, transfer_id, result, created, next_at)
    SELECT s.device_id, 'delivered', NEW.transfer_id, CASE NEW.state WHEN 'downloaded' THEN 'saved' ELSE 'declined' END,
           CAST(strftime('%s','now') AS INTEGER), CAST(strftime('%s','now') AS INTEGER)
    FROM transfer t JOIN push_sub s ON s.person=t.sender_person JOIN device v ON v.id=s.device_id AND v.revoked IS NULL
    WHERE t.id=NEW.transfer_id; END;
"""
# Step 6: a ready send withdrawn by its sender -> one 'withdrawn' push per subscribed, live device still waiting on it.
# Created after _migrate() (it names the new kind, which the old CHECK would refuse at insert time).
_SCHEMA6 = """
CREATE TRIGGER IF NOT EXISTS push_on_cancel AFTER UPDATE OF state ON transfer
  WHEN OLD.state='ready' AND NEW.state='cancelled'
  BEGIN INSERT INTO push_out(device_id, kind, transfer_id, created, next_at)
    SELECT s.device_id, 'withdrawn', NEW.id, CAST(strftime('%s','now') AS INTEGER), CAST(strftime('%s','now') AS INTEGER)
    FROM delivery d JOIN push_sub s ON s.device_id=d.device_id AND s.person=d.person
    JOIN device v ON v.id=d.device_id AND v.revoked IS NULL
    WHERE d.transfer_id=NEW.id AND d.state IN ('pending','accepted'); END;
"""
_READY: set[str] = set()


def _migrate(c: sqlite3.Connection) -> None:
    """Once: push_out's kind CHECK without 'withdrawn' (made by Step 4) -> rebuilt with it, rows kept, one transaction.
    legacy_alter_table=ON: the rename must not try to rewrite the Step 4 triggers that insert into push_out (they
    name the table, so they work on the rebuilt one). Idempotent: does nothing once the CHECK has the kind."""
    sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='push_out'").fetchone()
    if sql is None or "'withdrawn'" in sql[0]:
        return
    c.execute("PRAGMA legacy_alter_table=ON")
    try:
        c.execute("BEGIN IMMEDIATE")
        sql = c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='push_out'").fetchone()
        if "'withdrawn'" not in sql[0]:   # checked again under the write lock: two first uses cannot both rebuild
            c.execute("CREATE TABLE push_out_new(id INTEGER PRIMARY KEY, device_id TEXT NOT NULL"
                      " CHECK(length(device_id)=22), kind TEXT NOT NULL CHECK(kind IN " + KINDS + "),"
                      " transfer_id TEXT CHECK(transfer_id IS NULL OR length(transfer_id)=22),"
                      " result TEXT CHECK(result IS NULL OR result IN ('saved','declined')),"
                      " created INTEGER NOT NULL, tries INTEGER NOT NULL DEFAULT 0, next_at INTEGER NOT NULL)")
            c.execute("INSERT INTO push_out_new SELECT id, device_id, kind, transfer_id, result, created, tries, next_at"
                      " FROM push_out")
            c.execute("DROP TABLE push_out")
            c.execute("ALTER TABLE push_out_new RENAME TO push_out")
            c.execute("CREATE INDEX IF NOT EXISTS push_out_due ON push_out(next_at)")
            _say("push outbox: kind 'withdrawn' added (one-time rebuild)")
        c.execute("COMMIT")
    except sqlite3.Error:
        if c.in_transaction:
            c.execute("ROLLBACK")
        raise
    finally:
        c.execute("PRAGMA legacy_alter_table=OFF")


def _say(msg: str) -> None:
    devices._say(msg)


def _db() -> sqlite3.Connection:
    """relay._db() (devices + relay schema, WAL, foreign keys) plus the push tables and triggers on first use."""
    c = relay._db()
    key = str(devices.db_path())
    try:
        if key not in _READY:
            with devices._INIT_LOCK:
                if key not in _READY:
                    c.executescript(_SCHEMA)
                    _migrate(c)
                    c.executescript(_SCHEMA6)
                    _READY.add(key)
        return c
    except sqlite3.Error as e:
        c.close()
        _say(f"ERROR push db: {type(e).__name__}")
        raise Refuse(503, "relay_unavailable", "Notifications are not available right now. Try again soon.") from None


# ------------------------------------------------------------------ the VAPID key (never logged, never served)

class _Key:
    vapid = None          # py_vapid.Vapid02, or None = push off
    public = ""           # base64url uncompressed P-256 point (65 bytes): the browser's applicationServerKey
    kid = ""              # first 16 hex of sha256(public point)
    why = "not loaded"


KEY = _Key()
_KEY_LOCK = threading.Lock()


def load_key() -> bool:
    """Read VAPID_PRIVATE_KEY once (or again after a change). Stray whitespace / CR are stripped. True = push on."""
    with _KEY_LOCK:
        raw = "".join((os.environ.get("VAPID_PRIVATE_KEY") or "").split())
        if not raw:
            KEY.vapid, KEY.public, KEY.kid, KEY.why = None, "", "", "VAPID_PRIVATE_KEY is not set"
            return False
        try:
            from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
            from py_vapid import Vapid02
            if not re.fullmatch(r"[A-Za-z0-9_-]{43}", raw):
                raise ValueError("shape")
            v = Vapid02.from_raw(raw.encode())   # RFC 8292 "vapid t=..., k=..." headers
            pub = v.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
        except Exception as e:  # noqa: BLE001  (any import / parse problem = push off, the key text is never shown)
            KEY.vapid, KEY.public, KEY.kid, KEY.why = None, "", "", f"VAPID key not usable ({type(e).__name__})"
            return False
        KEY.vapid, KEY.public = v, devices.b64u(pub)
        KEY.kid, KEY.why = hashlib.sha256(pub).hexdigest()[:16], ""
        return True


def subject() -> str:
    return os.environ.get("NOVASHARE_PUSH_SUBJECT", "").strip() or "https://drop.home.example.net"


def _test_origins() -> set[str]:
    return {o.strip().rstrip("/").lower() for o in os.environ.get("INTAKE_PUSH_TEST_ORIGINS", "").split(",") if o.strip()}


def endpoint_ok(ep: str) -> bool:
    """https on a known push service host (default port), or an exact sandbox origin from INTAKE_PUSH_TEST_ORIGINS."""
    try:
        u = urlsplit(ep)
        host = (u.hostname or "").lower()
        port = u.port
    except ValueError:
        return False
    if u.username or u.password or u.fragment or not host or any(ch in ep for ch in " \r\n\t\\"):
        return False
    origin = f"{u.scheme}://{u.netloc}".lower()
    if origin in _test_origins():
        return True
    if u.scheme != "https" or port not in (None, 443):
        return False
    return any(host == h or host.endswith("." + h) for h in PUSH_HOSTS)


def _host(ep: str) -> str:
    try:
        return (urlsplit(ep).hostname or "?").lower()
    except ValueError:
        return "?"


# ------------------------------------------------------------------ operations (thread pool)

def _p256_ok(raw: bytes) -> bool:
    from cryptography.hazmat.primitives.asymmetric import ec
    try:
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), raw)
        return raw[0] == 4
    except ValueError:
        return False


def _validate(body) -> tuple[str, bytes, bytes]:
    if not isinstance(body, dict) or not isinstance(body.get("keys"), dict):
        raise Refuse(400, "bad_request", "Send the browser's push subscription.")
    ep = body.get("endpoint")
    if not isinstance(ep, str) or not 12 <= len(ep) <= ENDPOINT_MAX or not endpoint_ok(ep):
        raise Refuse(400, "bad_request", "That push address is not one Novashare can use.")
    try:
        p256 = devices.b64u_dec(body["keys"].get("p256dh"), 65, "p256dh")
        auth = devices.b64u_dec(body["keys"].get("auth"), 16, "auth")
    except Refuse:
        raise Refuse(400, "bad_request", "The push keys have the wrong shape.") from None
    if not _p256_ok(p256):
        raise Refuse(400, "bad_request", "The push key is not a P-256 point.")
    return ep, p256, auth


def do_subscribe(person: str, did: str | None, body) -> dict:
    if KEY.vapid is None:
        raise Refuse(503, "push_off", "Notifications are switched off on the server.")
    ep, p256, auth = _validate(body)
    c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        me = relay._my_device(c, person, did)
        other = c.execute("SELECT device_id, person FROM push_sub WHERE endpoint=?", (ep,)).fetchone()
        if other is not None and other["device_id"] != me["id"]:
            if other["person"] != person:          # never move or drop another person's row
                c.execute("ROLLBACK")
                _say(f"push refused subscribe d={me['id'][:6]} code=endpoint_taken")
                raise Refuse(409, "endpoint_taken", "This browser is already set up for someone else's notifications.")
            c.execute("DELETE FROM push_sub WHERE device_id=? AND person=?", (other["device_id"], person))
        now = int(time.time())
        c.execute("INSERT INTO push_sub(device_id,person,endpoint,p256dh,auth,kid,created,updated) VALUES(?,?,?,?,?,?,?,?)"
                  " ON CONFLICT(device_id) DO UPDATE SET endpoint=excluded.endpoint, p256dh=excluded.p256dh,"
                  " auth=excluded.auth, kid=excluded.kid, updated=excluded.updated",
                  (me["id"], person, ep, p256, auth, KEY.kid, now, now))
        c.execute("COMMIT")
        _say(f"push subscribed d={me['id'][:6]} via={_host(ep)}")
        return {"subscribed": True, "current_key": True}
    except sqlite3.Error as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        _say(f"ERROR push subscribe: {type(e).__name__}")
        raise Refuse(503, "relay_unavailable", "Notifications are not available right now. Try again soon.") from None
    finally:
        if c.in_transaction:
            c.execute("ROLLBACK")
        c.close()


def do_unsubscribe(person: str, did: str | None) -> dict:
    c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        me = relay._my_device(c, person, did)
        n = c.execute("DELETE FROM push_sub WHERE device_id=? AND person=?", (me["id"], person)).rowcount
        c.execute("DELETE FROM push_out WHERE device_id=?", (me["id"],))
        c.execute("COMMIT")
        if n:
            _say(f"push unsubscribed d={me['id'][:6]}")
        return {"subscribed": False}
    except sqlite3.Error as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        _say(f"ERROR push unsubscribe: {type(e).__name__}")
        raise Refuse(503, "relay_unavailable", "Notifications are not available right now. Try again soon.") from None
    finally:
        if c.in_transaction:
            c.execute("ROLLBACK")
        c.close()


@relay._db_errors
def do_state(person: str, did: str | None) -> dict:
    c = _db()
    try:
        me = relay._my_device(c, person, did)
        r = c.execute("SELECT kid FROM push_sub WHERE device_id=? AND person=?", (me["id"], person)).fetchone()
        return {"subscribed": r is not None, "current_key": bool(r is not None and KEY.kid and r["kid"] == KEY.kid),
                "enabled": KEY.vapid is not None}
    finally:
        c.close()


_TESTS: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_TESTS_LOCK = threading.Lock()


def do_test(person: str, did: str | None) -> dict:
    if KEY.vapid is None:
        raise Refuse(503, "push_off", "Notifications are switched off on the server.")
    c = _db()
    try:
        c.execute("BEGIN IMMEDIATE")
        me = relay._my_device(c, person, did)
        if c.execute("SELECT 1 FROM push_sub WHERE device_id=? AND person=?", (me["id"], person)).fetchone() is None:
            c.execute("ROLLBACK")
            raise Refuse(409, "not_subscribed", "Turn notifications on for this device first.")
        now = time.monotonic()
        with _TESTS_LOCK:
            q = _TESTS[me["id"]]
            while q and now - q[0] > 600:
                q.popleft()
            if len(q) >= TEST_PER_10MIN:
                c.execute("ROLLBACK")
                raise Refuse(429, "rate_limited", "That is enough tests for now. Try again in a few minutes.")
            q.append(now)
        t = int(time.time())
        c.execute("INSERT INTO push_out(device_id,kind,created,next_at) VALUES(?,?,?,?)", (me["id"], "test", t, t))
        c.execute("COMMIT")
        WORKER.wake()
        return {"queued": True}
    except sqlite3.Error as e:
        if c.in_transaction:
            c.execute("ROLLBACK")
        _say(f"ERROR push test: {type(e).__name__}")
        raise Refuse(503, "relay_unavailable", "Notifications are not available right now. Try again soon.") from None
    finally:
        if c.in_transaction:
            c.execute("ROLLBACK")
        c.close()


# ------------------------------------------------------------------ the worker (outbox -> push services)

def payload(kind: str, tid: str | None, result: str | None = None) -> bytes:
    """The ONLY thing a push carries: kind, transfer id, fixed text (and saved/declined for 'delivered'). No names."""
    d = {"v": 1, "kind": kind, "body": TEXT[kind]}
    if tid:
        d["id"] = tid
    if kind == "delivered":
        d["result"] = result if result in ("saved", "declined") else "saved"
        if d["result"] == "declined":
            d["body"] = "Your Novashare item was declined"
    return json.dumps(d, separators=(",", ":")).encode()


def _post(sub: sqlite3.Row, data: bytes, urgency: str) -> tuple[int | None, int]:
    """(HTTP status or None for a network failure, Retry-After seconds or 0). Never raises; never logs the endpoint."""
    try:
        from pywebpush import WebPushException, webpush
    except ImportError:
        return None, 0
    import requests
    info = {"endpoint": sub["endpoint"], "keys": {"p256dh": devices.b64u(sub["p256dh"]), "auth": devices.b64u(sub["auth"])}}
    resp = None
    # audit 2026-11-24: no redirects (an allowlisted host must not bounce the POST somewhere else: SSRF) and no proxy /
    # netrc from the environment. A redirect raises TooManyRedirects = a network failure, retried once, then dropped.
    sess = requests.Session()
    sess.max_redirects = 0
    sess.trust_env = False
    try:
        resp = webpush(info, data=data, vapid_private_key=KEY.vapid, vapid_claims={"sub": subject()}, ttl=TTL_S,
                       timeout=TIMEOUT_S, headers={"Urgency": urgency}, content_encoding="aes128gcm",
                       requests_session=sess)
    except WebPushException as e:
        resp = e.response
    except Exception:  # noqa: BLE001  (requests / socket / TLS errors: a network failure, retried once)
        return None, 0
    finally:
        sess.close()
    if resp is None:
        return None, 0
    ra = 0
    try:
        ra = int((resp.headers or {}).get("Retry-After", "0"))
    except (TypeError, ValueError):
        ra = 0
    return resp.status_code, max(0, min(ra, RETRY_MAX_S))


def drain_once(limit: int = 20) -> int:
    """Send every due outbox row (at most `limit`). Returns how many rows were handled. Safe to call from tests."""
    now = int(time.time())
    c = _db()
    try:
        c.execute("DELETE FROM push_out WHERE created < ?", (now - STALE_S,))
        rows = c.execute("SELECT o.id, o.device_id, o.kind, o.transfer_id, o.result, o.tries, s.endpoint, s.p256dh,"
                         " s.auth, s.kid FROM push_out o LEFT JOIN push_sub s ON s.device_id=o.device_id"
                         " LEFT JOIN device v ON v.id=o.device_id AND v.revoked IS NULL"
                         " WHERE o.next_at<=? AND (s.device_id IS NULL OR v.id IS NOT NULL) ORDER BY o.id LIMIT ?",
                         (now, limit)).fetchall()
    finally:
        c.close()
    for r in rows:
        _handle(r)
    return len(rows)


def _write(sql: str, args: tuple) -> None:
    c = _db()
    try:
        c.execute(sql, args)
    finally:
        c.close()


def _handle(r: sqlite3.Row) -> None:
    tag = f"kind={r['kind']} t={(r['transfer_id'] or '-')[:8]} d={r['device_id'][:6]}"
    if r["endpoint"] is None or KEY.vapid is None:          # unsubscribed / revoked since, or push switched off
        _write("DELETE FROM push_out WHERE id=?", (r["id"],))
        return
    c = _db()                                                # audit 2026-11-24: still the same subscription right now?
    try:                                                     # (an unsubscribe / revoke after the drain query wins)
        still = c.execute("SELECT 1 FROM push_sub s JOIN device v ON v.id=s.device_id AND v.revoked IS NULL"
                          " WHERE s.device_id=? AND s.endpoint=?", (r["device_id"], r["endpoint"])).fetchone()
    finally:
        c.close()
    if still is None:
        _write("DELETE FROM push_out WHERE id=?", (r["id"],))
        return
    if r["kid"] != KEY.kid:                                  # made with an older VAPID key: the service would refuse
        _write("DELETE FROM push_sub WHERE device_id=? AND kid=?", (r["device_id"], r["kid"]))
        _write("DELETE FROM push_out WHERE device_id=?", (r["device_id"],))
        _say(f"push dropped old-key subscription d={r['device_id'][:6]}")
        return
    status, retry_after = _post(r, payload(r["kind"], r["transfer_id"], r["result"]),
                                "high" if r["kind"] == "new" else "normal")
    host = _host(r["endpoint"])
    if status is not None and 200 <= status < 300:
        _write("DELETE FROM push_out WHERE id=?", (r["id"],))
        _say(f"push sent {tag} via={host} status={status}")
    elif status in (404, 410):                               # the browser unsubscribed or the subscription expired
        _write("DELETE FROM push_sub WHERE device_id=? AND endpoint=?", (r["device_id"], r["endpoint"]))
        _write("DELETE FROM push_out WHERE device_id=?", (r["device_id"],))
        _say(f"push gone {tag} via={host} status={status}: subscription deleted")
    elif status is None or status == 429 or status >= 500:
        if r["tries"] < 1:                                   # one retry with backoff, then give up
            _write("UPDATE push_out SET tries=tries+1, next_at=? WHERE id=?",
                   (int(time.time()) + max(RETRY_S, retry_after), r["id"]))
            _say(f"push retry {tag} via={host} status={status or 'network'}")
        else:
            _write("DELETE FROM push_out WHERE id=?", (r["id"],))
            _say(f"WARNING push gave up {tag} via={host} status={status or 'network'}")
    else:
        _write("DELETE FROM push_out WHERE id=?", (r["id"],))
        _say(f"WARNING push refused {tag} via={host} status={status}")


class _Worker:
    def __init__(self):
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None

    def wake(self) -> None:
        self._wake.set()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        on = load_key()
        try:
            c = _db()                                   # the triggers must exist before the first send turns ready
            c.close()
        except Refuse:
            _say("ERROR push: the database is not available; push worker not started")
            return
        if _test_origins():
            _say("WARNING push: INTAKE_PUSH_TEST_ORIGINS is set (sandbox only)")
        _say("push on" if on else f"push off: {KEY.why}")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="novashare-push", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=TIMEOUT_S + 5)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            n = 0
            try:
                n = drain_once()
            except Exception as e:  # noqa: BLE001  (the loop must survive anything; logged without detail)
                _say(f"ERROR push worker: {type(e).__name__}")
            if n == 0:
                self._wake.wait(POLL_S)
                self._wake.clear()


WORKER = _Worker()


def wrap_lifespan(prev):
    """app.py's lifespan plus the push worker (started after the app's own startup, stopped before its shutdown)."""
    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with prev(app) as state:
            WORKER.start()
            try:
                yield state
            finally:
                WORKER.stop()
    return lifespan


# ------------------------------------------------------------------ HTTP routes

def _dev(request: Request) -> str | None:
    return request.headers.get("x-device-id")


@router.get("/relay/api/push/key")
async def key_api(request: Request):
    try:
        guard(request)
        return _json({"enabled": KEY.vapid is not None, "public_key": KEY.public or None})
    except Refuse as e:
        return _answer(e)


@router.get("/relay/api/push/sub")
async def sub_state(request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_state, person, _dev(request)))
    except Refuse as e:
        return _answer(e)


@router.put("/relay/api/push/sub")
async def subscribe(request: Request):
    try:
        person = guard(request)
        body = await relay._json_body(request, BODY_MAX)
        return _json(await run_in_threadpool(do_subscribe, person, _dev(request), body))
    except Refuse as e:
        return _answer(e)


@router.delete("/relay/api/push/sub")
async def unsubscribe(request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_unsubscribe, person, _dev(request)))
    except Refuse as e:
        return _answer(e)


@router.post("/relay/api/push/test")
async def test_push(request: Request):
    try:
        person = guard(request)
        return _json(await run_in_threadpool(do_test, person, _dev(request)))
    except Refuse as e:
        return _answer(e)


import push_js  # noqa: E402  (client script in a .py so SWAP-PATCH.sh installs it)

JS = push_js.JS.encode()
SRI = devices._sri(JS)
TAG = f'<script type=module src="/relay/push/push.js" integrity="{SRI}"></script>'
SECTION = ('<section aria-labelledby=h-push><h2 id=h-push>Notifications</h2>'
           '<div id=push-box class=card><p class=dim>Checking notifications...</p></div></section>')

# the Devices and Inbox pages show the Notifications box (both read these at request time)
devices.PUSH_TAG = receive.PUSH_TAG = TAG
devices.PUSH_SECTION = receive.PUSH_SECTION = SECTION

@router.get("/relay/push/push.js")
async def push_script(request: Request):
    try:
        guard(request, api=False)
    except Refuse as e:
        return Response(e.code, status_code=e.status, media_type="text/plain")
    return Response(JS, media_type="text/javascript", headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})
