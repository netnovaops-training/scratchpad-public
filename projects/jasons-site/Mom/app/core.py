"""Shared plumbing for the Tech Help app: config, storage, backend calls.

Deliberately self-contained. This app shares NOTHING with the awards portal
except the GPU on the desktop -- separate process, separate database, separate
users, separate rules. A bad edit here cannot reach the other app.
"""
import hashlib
import json
import os
import re
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Optional

import httpx


def _b(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


# ============================================================ configuration ==

BASE_DIR = Path(os.getenv("MOM_BASE_DIR", "/opt/momhelp"))
DATA_DIR = Path(os.getenv("MOM_DATA_DIR", "/var/lib/momhelp"))
UPLOAD_DIR = DATA_DIR / "audio"
# Illustrations drawn by imagine (see draw() below), served straight back as
# static files -- a plain directory mount rather than a route, the same way
# /static already is.
GENERATED_DIR = DATA_DIR / "generated"
DB_PATH = DATA_DIR / "momhelp.db"
KNOWLEDGE_DIR = Path(os.getenv("MOM_KNOWLEDGE_DIR", str(BASE_DIR / "knowledge")))

SECRET_KEY = os.getenv("MOM_SECRET_KEY", "")
SITE_NAME = os.getenv("MOM_SITE_NAME", "Tech Help")

# WHO IS AN ADMIN HERE.
#
# This app no longer has a login of its own -- Caddy's forward_auth signs
# everybody in before the request arrives, and hands us the address it verified
# in X-Auth-Email. See the long note above current_user() in main.py.
#
# So "who is an admin" can no longer be a column somebody sets by hand; it has
# to be derived from that address. It reads ADMIN_OWNERS, the same list the
# Rust admin already treats as yours, so there is ONE answer to "who is the
# owner" on this machine rather than three that drift apart.
OWNERS = {
    e.strip().lower()
    for e in os.getenv("MOM_OWNERS",
                       os.getenv("ADMIN_OWNERS",
                                 "person4@example.com,person1@example.com")).split(",")
    if e.strip()
}

# Signed in, seen everywhere, but cannot send a message or touch a ticket --
# read_only in user_for_email() below. Same fallback shape as OWNERS: one
# shared ADMIN_GUESTS list rather than a per-container copy to keep in sync.
GUESTS = {
    e.strip().lower()
    for e in os.getenv("MOM_GUESTS", os.getenv("ADMIN_GUESTS", "")).split(",")
    if e.strip()
}
ROOT_PATH = os.getenv("MOM_ROOT_PATH", "/mama")      # served under a sub-path
COOKIE_PATH = os.getenv("MOM_COOKIE_PATH", "") or (ROOT_PATH or "/")
BIND_PORT = _i("MOM_BIND_PORT", 8001)
SESSION_MAX_AGE = _i("MOM_SESSION_MAX_AGE", 60 * 60 * 24 * 30)  # 30 days: she should not
                                                                 # be signing in constantly
MAX_UPLOAD_MB = _i("MOM_MAX_UPLOAD_MB", 60)
# A phone video runs bigger than a photo or a voice note fast -- a separate,
# larger ceiling rather than making everyone else's limit generous enough
# to cover it too.
MAX_VIDEO_MB = _i("MOM_MAX_VIDEO_MB", 300)
MAX_FAILED_LOGINS = _i("MOM_MAX_FAILED_LOGINS", 10)
LOCKOUT_SECONDS = _i("MOM_LOCKOUT_SECONDS", 600)

HELPER_PHONE = os.getenv("MOM_HELPER_PHONE", "")     # shown on the panic button
HELPER_NAME = os.getenv("MOM_HELPER_NAME", "Jason")

# =========================================================== THE GPU MACHINE ==
#
# READ THE NAME CAREFULLY. These describe JASON'S desktop -- the one with the
# 4070, sitting on the LAN next to the Pi. They have nothing whatsoever to do
# with Mom's computer.
#
# The "MOM_" prefix is just this app's namespace for every setting it owns, the
# same way MOM_SMTP_USER is not Mom's email account. MOM_GPU_HOST is the clearer
# spelling and takes precedence; MOM_DESKTOP_IP still works so existing .env
# files keep running.
#
# Mom needs NO configuration of any kind. She is a visitor to the website. She
# opens https://example.org/Mom/ in a browser, signs in, and that is
# the entire setup on her side. Her IP address is irrelevant and it does not
# matter that it changes -- no more than your IP matters when you visit any
# other website. She could be on hotel wifi in a different state and nothing
# about this changes.
GPU_HOST = os.getenv("MOM_GPU_HOST", "") or os.getenv("MOM_DESKTOP_IP", "")
GPU_MAC = os.getenv("MOM_GPU_MAC", "") or os.getenv("MOM_DESKTOP_MAC", "")

# Kept as aliases so nothing else in the codebase has to change at once.
DESKTOP_IP = GPU_HOST
DESKTOP_MAC = GPU_MAC
DESKTOP_BROADCAST = (os.getenv("MOM_GPU_BROADCAST", "")
                     or os.getenv("MOM_DESKTOP_BROADCAST", "255.255.255.255"))
WOL_PORTS = [int(p) for p in os.getenv("MOM_WOL_PORTS", "7,9").split(",") if p.strip()]
OLLAMA_PORT = _i("MOM_OLLAMA_PORT", 11434)
WHISPER_PORT = _i("MOM_WHISPER_PORT", 9000)
OLLAMA_MODEL = os.getenv("MOM_OLLAMA_MODEL", "gemma4:12b-it-qat")
OLLAMA_NUM_CTX = _i("MOM_OLLAMA_NUM_CTX", 32768)
OLLAMA_THINK = _b("MOM_OLLAMA_THINK", "0")
OLLAMA_TIMEOUT = _i("MOM_OLLAMA_TIMEOUT", 300)
WHISPER_TIMEOUT = _i("MOM_WHISPER_TIMEOUT", 600)
AUTO_WAKE = _b("MOM_AUTO_WAKE", "1")
BOOT_WAIT_SECONDS = _i("MOM_BOOT_WAIT_SECONDS", 240)

# How many clarifying questions the assistant may ask, at most, before it
# has to commit to a real answer with whatever it has -- see main.py's
# _triage_question(). "As many as possible" still has to end somewhere: two
# extra round trips on a 7B model is real time spent waiting, the same
# reasoning tools.py's own MAX_ROUNDS is bounded for.
TRIAGE_ROUNDS = _i("MOM_TRIAGE_ROUNDS", 2)

# How many past turns to replay to the model. Older adults tell long stories;
# keeping the whole thread would blow the context budget on a 12 GB card.
HISTORY_TURNS = _i("MOM_HISTORY_TURNS", 12)

AUDIO_EXT = {".webm", ".ogg", ".m4a", ".mp3", ".wav", ".aac", ".opus", ".mp4", ".amr", ".3gp"}
# A video she attaches to a ticket -- for Jason's own eyes only (see
# main.py's admin-only video route), never fed to any model. No AI here
# reads video at all, so this is pure storage, same as a photo she mails
# you would be.
VIDEO_EXT = {".mp4", ".mov", ".webm", ".3gp", ".mkv", ".avi"}


def ollama_url(p: str = "") -> str:
    return f"http://{DESKTOP_IP}:{OLLAMA_PORT}{p}"


def whisper_url(p: str = "") -> str:
    return f"http://{DESKTOP_IP}:{WHISPER_PORT}{p}"


# A full URL, not host+port -- a separate service on the Docker network,
# not the desktop's own Ollama/Whisper pair above.
IMAGINE_URL = os.getenv("MOM_IMAGINE_URL", "").rstrip("/")
IMAGINE_TIMEOUT = _i("MOM_IMAGINE_TIMEOUT", 60)


def ensure_dirs() -> None:
    for d in (DATA_DIR, UPLOAD_DIR, GENERATED_DIR):
        d.mkdir(parents=True, exist_ok=True)


# ================================================================= storage ==

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username     TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    pw_salt      TEXT NOT NULL,
    pw_hash      TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'user',
    created_at   REAL NOT NULL,
    must_change  INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS login_attempts (
    key TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0,
    locked_until REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS threads (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL,
    title      TEXT NOT NULL DEFAULT 'New question',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    resolved   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_threads_user ON threads(username, updated_at DESC);
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id  INTEGER NOT NULL,
    created_at REAL NOT NULL,
    role       TEXT NOT NULL,           -- user | assistant | system
    content    TEXT NOT NULL,
    audio_path TEXT NOT NULL DEFAULT '',
    flags      TEXT NOT NULL DEFAULT '[]',
    kb_version TEXT NOT NULL DEFAULT '',
    model      TEXT NOT NULL DEFAULT '',
    FOREIGN KEY(thread_id) REFERENCES threads(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_thread ON messages(thread_id, id);
CREATE TABLE IF NOT EXISTS memory (
    username   TEXT NOT NULL,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL,
    PRIMARY KEY (username, key)
);
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    username   TEXT NOT NULL,
    thread_id  INTEGER,
    kind       TEXT NOT NULL,
    detail     TEXT NOT NULL,
    seen       INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_alerts_ts ON alerts(ts DESC);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
    level TEXT NOT NULL, source TEXT NOT NULL, message TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts DESC);

CREATE TABLE IF NOT EXISTS faq (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ticket_id   INTEGER,
    ticket_no   TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT 'other',
    question    TEXT NOT NULL,
    answer      TEXT NOT NULL,
    published   INTEGER NOT NULL DEFAULT 1,
    edited      INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_faq_pub ON faq(published, updated_at DESC);

CREATE TABLE IF NOT EXISTS outbox (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at REAL NOT NULL,
    subject    TEXT NOT NULL,
    body_md    TEXT NOT NULL,
    to_addrs   TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'queued',
    attempts   INTEGER NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    sent_at    REAL,
    ticket_id  INTEGER,
    file_path  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_outbox ON outbox(status, created_at DESC);

-- Full-text search over closed tickets and FAQ entries. Kept as a plain table
-- rather than FTS5 so the same database works on any SQLite build, including
-- the minimal ones in some ARM container images.
CREATE TABLE IF NOT EXISTS search_index (
    kind    TEXT NOT NULL,          -- ticket | faq
    ref_id  INTEGER NOT NULL,
    text    TEXT NOT NULL,
    updated REAL NOT NULL,
    PRIMARY KEY (kind, ref_id)
);
"""

# Columns added to `threads` after the first release. SQLite has no
# "ADD COLUMN IF NOT EXISTS", so this is applied by migrate() below.
# Timezones. She is in Nevada and the Pi is in New York, three hours apart.
#
# Without this, every timestamp she sees is rendered in the SERVER's timezone.
# She reports a problem at 9am her time, the ticket says noon, and when she
# looks at her own history nothing lines up with her memory of the day. Worse,
# the assistant reads those timestamps too, so "you asked about this yesterday
# evening" would be wrong by a third of a day.
#
# Everything is stored as a UTC epoch, which is unambiguous. Only the DISPLAY
# is converted, and it is converted per person.
DEFAULT_TZ = os.getenv("MOM_DEFAULT_TZ", "America/New_York")

USER_COLUMNS = [
    ("tz", "TEXT NOT NULL DEFAULT ''"),
]

THREAD_COLUMNS = [
    ("ticket_no",  "TEXT NOT NULL DEFAULT ''"),
    ("category",   "TEXT NOT NULL DEFAULT 'other'"),
    ("status",     "TEXT NOT NULL DEFAULT 'open'"),   # open|pending|resolved|closed|escalated
    ("summary",    "TEXT NOT NULL DEFAULT ''"),
    ("resolution", "TEXT NOT NULL DEFAULT ''"),
    ("steps",      "TEXT NOT NULL DEFAULT '[]'"),
    ("emailed_at", "REAL"),
    ("closed_at",  "REAL"),
]

MESSAGE_COLUMNS = [
    # A generated illustration, filename only (relative to GENERATED_DIR,
    # served at /generated/<name>) -- see core.draw() and main.py's
    # admin_ticket_draw()/ask_for_a_picture() routes. Empty means no
    # picture, same "empty string, not NULL" shape as audio_path.
    ("image_path", "TEXT NOT NULL DEFAULT ''"),
    # A video she attached, full path on disk (same storage shape as
    # audio_path, not a bare filename like image_path -- there is no public
    # mount for this one; see main.py's admin-only video route). For her
    # own user messages only. Jason's eyes only, by design.
    ("video_path", "TEXT NOT NULL DEFAULT ''"),
]


def conn() -> sqlite3.Connection:
    c = getattr(_local, "c", None)
    if c is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        _local.c = c
    return c


@contextmanager
def tx():
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise


def init_db() -> None:
    with tx() as c:
        c.executescript(SCHEMA)
    migrate()


def migrate() -> None:
    """Add columns that did not exist in earlier versions. Safe to re-run."""
    c = conn()
    for table, cols in (("threads", THREAD_COLUMNS), ("users", USER_COLUMNS),
                       ("messages", MESSAGE_COLUMNS)):
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, decl in cols:
            if name not in have:
                with tx() as t:
                    t.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                log("info", "migrate", f"{table}.{name} added")


# ------------------------------------------------------------- timezones ----

def tz_for(username: str = ""):
    """The ZoneInfo to render this person's timestamps in.

    Falls back to the app default rather than the server's local time, because
    the server's local time is an accident of where the Pi happens to sit and is
    right for nobody in particular.
    """
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    name = ""
    if username:
        try:
            r = conn().execute("SELECT tz FROM users WHERE username=?",
                               (username,)).fetchone()
            name = (r["tz"] if r else "") or ""
        except Exception:
            name = ""
    for candidate in (name, DEFAULT_TZ, "UTC"):
        if not candidate:
            continue
        try:
            return ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError, KeyError):
            # A slim container without tzdata installed lands here. Falling
            # through to UTC is wrong-but-consistent; a crash would take the
            # whole page down over a cosmetic detail.
            continue
    return None


def set_tz(username: str, tz_name: str) -> bool:
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(tz_name)
    except Exception:
        return False
    with tx() as c:
        c.execute("UPDATE users SET tz=? WHERE username=?", (tz_name, username))
    log("info", "user", f"{username} timezone set to {tz_name}")
    return True


def local_time(ts: float, username: str = "", fmt: str = "%d %b, %-I:%M %p") -> str:
    if not ts:
        return "-"
    from datetime import datetime as _dt, timezone as _tz
    d = _dt.fromtimestamp(ts, _tz.utc)
    zone = tz_for(username)
    if zone is not None:
        d = d.astimezone(zone)
    try:
        return d.strftime(fmt)
    except ValueError:            # %-I is not portable to every libc
        return d.strftime(fmt.replace("%-I", "%I")).replace(" 0", " ")


def hash_password(pw: str, salt: Optional[str] = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32)
    return salt, dk.hex()


def verify_password(pw: str, salt: str, expected: str) -> bool:
    try:
        return secrets.compare_digest(hash_password(pw, salt)[1], expected)
    except Exception:
        return False


# She will be typing this on a phone, in Nevada, without anyone next to her. An
# email address is something she already knows by heart; "momhelp_user_01" is
# not. So a username may be either a plain handle or a full email address.
USERNAME_RE = re.compile(
    r"^(?:[a-z0-9._-]{2,32}|[a-z0-9._%+-]{1,64}@[a-z0-9.-]{1,190}\.[a-z]{2,24})$")


def normalize_username(username: str) -> str:
    return (username or "").strip().lower()


def valid_username(username: str) -> bool:
    return bool(USERNAME_RE.match(normalize_username(username)))


def create_user(username: str, pw: str, display_name: str = "", role: str = "user",
                must_change: bool = False) -> None:
    """Create or reset an account.

    Columns are named explicitly rather than using positional VALUES. The
    positional form breaks silently-but-fatally the moment a migration adds a
    column -- which is exactly what happened when `tz` was added -- and the
    failure surfaces as a confusing arity error a long way from the cause.
    """
    salt, h = hash_password(pw)
    username = normalize_username(username)
    with tx() as c:
        # Preserve the timezone across a password reset. Re-running adduser to
        # fix a forgotten password should not silently move her back to New York.
        row = c.execute("SELECT tz FROM users WHERE username=?", (username,)).fetchone()
        tz = (row["tz"] if row else "") or ""
        c.execute(
            "INSERT INTO users(username, display_name, pw_salt, pw_hash, role, "
            "created_at, must_change, tz) VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name, "
            "pw_salt=excluded.pw_salt, pw_hash=excluded.pw_hash, role=excluded.role, "
            "must_change=excluded.must_change",
            (username, display_name or username, salt, h, role,
             time.time(), 1 if must_change else 0, tz))


def set_password(username: str, pw: str) -> None:
    salt, h = hash_password(pw)
    with tx() as c:
        c.execute("UPDATE users SET pw_salt=?, pw_hash=?, must_change=0 WHERE username=?",
                  (salt, h, username.lower().strip()))


def user_for_email(email: str):
    """The account for a signed-in address, made on first sight.

    WHY THIS EXISTS AT ALL
    ----------------------
    This app used to keep its own usernames and passwords. It no longer does --
    Caddy's forward_auth authenticates every request before it reaches us, and
    the address it verified arrives in X-Auth-Email. But the rest of the app is
    built on a `username` foreign key: threads, messages, memory, tickets and
    the timezone all hang off it. Ripping that out would touch every table for
    no gain.

    So the address IS the username. This turns "somebody signed in" into a row,
    creating it the first time and returning it every time after. Nothing is
    ever created for an address that has not already been through the auth
    service, because this is only reachable from behind forward_auth.

    THE PASSWORD COLUMNS ARE FILLED WITH RANDOM BYTES, NOT LEFT EMPTY.
    Nothing checks them any more -- there is no login route to check them with.
    Random rather than blank so that if a password route is ever added back by
    mistake, no account is sitting there with a guessable or empty credential
    waiting for it. An unusable hash fails closed; an empty one might not.
    """
    username = normalize_username(email)
    if not username:
        return None
    role = "admin" if username in OWNERS else ("read_only" if username in GUESTS else "user")

    row = get_user(username)
    if row is not None:
        # The owners/guests lists are the truth on every request, so changing
        # somebody's tier is a line in .env and a restart, not a database
        # edit -- and a stale row can never leave an ex-owner as an admin, or
        # an ex-guest able to write once they are off that list.
        if row["role"] != role:
            with tx() as c:
                c.execute("UPDATE users SET role=? WHERE username=?", (role, username))
            row = get_user(username)
        return row

    salt, h = hash_password(secrets.token_urlsafe(32))
    with tx() as c:
        c.execute(
            "INSERT INTO users(username, display_name, pw_salt, pw_hash, role, "
            "created_at, must_change, tz) VALUES (?,?,?,?,?,?,0,'') "
            "ON CONFLICT(username) DO NOTHING",
            (username, username.split("@")[0], salt, h, role, time.time()))
    return get_user(username)


def get_user(username: str):
    return conn().execute("SELECT * FROM users WHERE username=?",
                          (username.lower().strip(),)).fetchone()


def list_users():
    return conn().execute("SELECT * FROM users ORDER BY created_at").fetchall()


def is_locked(key: str) -> float:
    r = conn().execute("SELECT locked_until FROM login_attempts WHERE key=?", (key,)).fetchone()
    return max(0.0, r["locked_until"] - time.time()) if r else 0.0


def record_failure(key: str) -> None:
    with tx() as c:
        c.execute("INSERT INTO login_attempts(key,failures,locked_until) VALUES (?,1,0) "
                  "ON CONFLICT(key) DO UPDATE SET failures=failures+1", (key,))
        r = c.execute("SELECT failures FROM login_attempts WHERE key=?", (key,)).fetchone()
        if r and r["failures"] >= MAX_FAILED_LOGINS:
            c.execute("UPDATE login_attempts SET locked_until=?, failures=0 WHERE key=?",
                      (time.time() + LOCKOUT_SECONDS, key))


def clear_failures(key: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM login_attempts WHERE key=?", (key,))


# --- threads and messages ----------------------------------------------------

def create_thread(username: str, title: str = "New question") -> int:
    now = time.time()
    with tx() as c:
        return int(c.execute(
            "INSERT INTO threads(username,title,created_at,updated_at) VALUES (?,?,?,?)",
            (username, title[:80], now, now)).lastrowid)


def get_thread(tid: int):
    return conn().execute("SELECT * FROM threads WHERE id=?", (tid,)).fetchone()


def list_threads(username: str, limit: int = 40):
    return conn().execute(
        "SELECT * FROM threads WHERE username=? ORDER BY updated_at DESC LIMIT ?",
        (username, limit)).fetchall()


def touch_thread(tid: int, title: Optional[str] = None, resolved: Optional[bool] = None) -> None:
    sets, vals = ["updated_at=?"], [time.time()]
    if title:
        sets.append("title=?"); vals.append(title[:80])
    if resolved is not None:
        sets.append("resolved=?"); vals.append(1 if resolved else 0)
    vals.append(tid)
    with tx() as c:
        c.execute(f"UPDATE threads SET {','.join(sets)} WHERE id=?", vals)


def add_message(thread_id: int, role: str, content: str, audio_path: str = "",
                flags: Optional[list] = None, kb_version: str = "", model: str = "",
                video_path: str = "") -> int:
    with tx() as c:
        mid = c.execute(
            "INSERT INTO messages(thread_id,created_at,role,content,audio_path,flags,"
            "kb_version,model,video_path) VALUES (?,?,?,?,?,?,?,?,?)",
            (thread_id, time.time(), role, content, audio_path,
             json.dumps(flags or []), kb_version, model, video_path)).lastrowid
    touch_thread(thread_id)
    return int(mid)


def list_messages(thread_id: int, limit: int = 200):
    return conn().execute(
        "SELECT * FROM messages WHERE thread_id=? ORDER BY id LIMIT ?",
        (thread_id, limit)).fetchall()


def get_message(mid: int):
    return conn().execute("SELECT * FROM messages WHERE id=?", (mid,)).fetchone()


def set_message_image(mid: int, filename: str) -> None:
    with tx() as c:
        c.execute("UPDATE messages SET image_path=? WHERE id=?", (filename, mid))


# --- memory: this is how the assistant "grows" with her ---------------------

def remember(username: str, key: str, value: str) -> None:
    with tx() as c:
        c.execute("INSERT INTO memory(username,key,value,updated_at) VALUES (?,?,?,?) "
                  "ON CONFLICT(username,key) DO UPDATE SET value=excluded.value, "
                  "updated_at=excluded.updated_at",
                  (username, key.strip()[:60], value.strip()[:300], time.time()))


def recall(username: str) -> list:
    return conn().execute(
        "SELECT key,value FROM memory WHERE username=? ORDER BY updated_at DESC LIMIT 40",
        (username,)).fetchall()


def forget(username: str, key: str) -> None:
    with tx() as c:
        c.execute("DELETE FROM memory WHERE username=? AND key=?", (username, key))


# --- alerts: things you should personally know about ------------------------

def raise_alert(username: str, kind: str, detail: str, thread_id: Optional[int] = None) -> None:
    with tx() as c:
        c.execute("INSERT INTO alerts(ts,username,thread_id,kind,detail) VALUES (?,?,?,?,?)",
                  (time.time(), username, thread_id, kind, detail[:2000]))
    log("warn", "safety", f"{kind}: {detail[:200]}")


def list_alerts(limit: int = 100, unseen_only: bool = False):
    q = "SELECT * FROM alerts {} ORDER BY ts DESC LIMIT ?".format(
        "WHERE seen=0" if unseen_only else "")
    return conn().execute(q, (limit,)).fetchall()


def mark_alerts_seen() -> None:
    with tx() as c:
        c.execute("UPDATE alerts SET seen=1 WHERE seen=0")


def unseen_alert_count() -> int:
    return conn().execute("SELECT COUNT(*) n FROM alerts WHERE seen=0").fetchone()["n"]


def log(level: str, source: str, message: str) -> None:
    try:
        with tx() as c:
            c.execute("INSERT INTO events(ts,level,source,message) VALUES (?,?,?,?)",
                      (time.time(), level, source, message[:4000]))
    except Exception:
        pass


def recent_events(limit: int = 150):
    return conn().execute("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()


def prune(days: int = 60) -> None:
    cutoff = time.time() - days * 86400
    with tx() as c:
        c.execute("DELETE FROM events WHERE ts < ?", (cutoff,))


def jload(s: str, default: Any) -> Any:
    try:
        return json.loads(s) if s else default
    except Exception:
        return default


# ================================================= the desktop (GPU backend) ==

def build_magic_packet(mac: str) -> bytes:
    clean = re.sub(r"[^0-9A-Fa-f]", "", mac or "")
    if len(clean) != 12:
        raise ValueError(f"Invalid MAC address: {mac!r}")
    return b"\xff" * 6 + bytes.fromhex(clean) * 16


def send_wol() -> bool:
    if not DESKTOP_MAC:
        return False
    try:
        pkt = build_magic_packet(DESKTOP_MAC)
    except ValueError as e:
        log("error", "wol", str(e))
        return False
    # Three targets, because this runs inside a Docker container and that
    # changes what actually reaches the wire:
    #
    #   directed broadcast (192.168.1.255)
    #       The one that matters. It is a routable destination, so it leaves the
    #       docker bridge, gets forwarded by the host, and is delivered as a
    #       broadcast on the LAN. Set MOM_DESKTOP_BROADCAST to this.
    #
    #   limited broadcast (255.255.255.255)
    #       What most WoL examples use, and what does NOT work from a bridged
    #       container: it is never forwarded off the bridge, so the packet dies
    #       inside Docker having reached nothing. Kept only for the case where
    #       this module is run directly on the host.
    #
    #   unicast to the last known IP
    #       Free to try. Works whenever the switch or mesh node still holds the
    #       ARP entry for a machine that has only just gone to sleep, which is
    #       common for the first several minutes.
    #
    # Magic packets are layer 2 and do not cross subnets, so all of this only
    # works because the Pi and the desktop are both on 192.168.1.0/24.
    targets = [t for t in (DESKTOP_BROADCAST, "255.255.255.255", DESKTOP_IP) if t]
    reached = []
    for target in dict.fromkeys(targets):          # de-duplicate, keep order
        for port in WOL_PORTS:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.settimeout(3)
                s.sendto(pkt, (target, port))
                s.close()
                reached.append(f"{target}:{port}")
            except Exception as e:  # noqa: BLE001
                log("warn", "wol", f"{target}:{port} — {e}")
    if reached:
        log("info", "wol", f"magic packet sent to {', '.join(reached)}")
    else:
        log("error", "wol", "every send failed — no packet left the container")
    return bool(reached)


def _probe(url: str, timeout: float = 3.0) -> bool:
    try:
        return httpx.get(url, timeout=timeout).status_code < 500
    except Exception:
        return False


def backend_status() -> dict:
    if not DESKTOP_IP:
        return {"ollama": False, "whisper": False, "ready": False}
    o = _probe(ollama_url("/api/tags"))
    w = _probe(whisper_url("/health"))
    return {"ollama": o, "whisper": w, "ready": o and w}


_wake_lock = threading.Lock()
_waking_since = {"t": 0.0}


def ensure_awake(timeout: Optional[int] = None) -> dict:
    """Wake the desktop and wait for both services. Safe to call concurrently."""
    st = backend_status()
    if st["ready"] or not AUTO_WAKE:
        return st
    with _wake_lock:
        st = backend_status()
        if st["ready"]:
            return st
        _waking_since["t"] = time.time()
        send_wol()
        deadline = time.time() + (timeout or BOOT_WAIT_SECONDS)
        last = time.time()
        while time.time() < deadline:
            time.sleep(4)
            st = backend_status()
            if st["ready"]:
                _waking_since["t"] = 0.0
                return st
            if time.time() - last > 30:
                send_wol()
                last = time.time()
        _waking_since["t"] = 0.0
        log("error", "wol", "desktop did not wake in time")
        return st


def waking_elapsed() -> int:
    return int(time.time() - _waking_since["t"]) if _waking_since["t"] else 0


# ============================================================ the AI backend ==

def knowledge_files() -> list[Path]:
    if not KNOWLEDGE_DIR.exists():
        return []
    return sorted(p for p in KNOWLEDGE_DIR.glob("*.md") if p.is_file())


def load_knowledge() -> tuple[str, str]:
    parts = []
    for p in knowledge_files():
        try:
            parts.append(f"\n\n<!-- {p.name} -->\n" + p.read_text(encoding="utf-8"))
        except Exception as e:
            log("warn", "knowledge", f"{p.name}: {e}")
    text = "".join(parts).strip()
    ver = hashlib.sha256(text.encode()).hexdigest()[:12] if text else "empty"
    return text, ver


def transcribe(path: Path) -> str:
    with open(path, "rb") as fh:
        r = httpx.post(whisper_url("/v1/audio/transcriptions"),
                       files={"file": (path.name, fh, "application/octet-stream")},
                       data={"response_format": "json", "language": "en"},
                       timeout=WHISPER_TIMEOUT)
    r.raise_for_status()
    try:
        return (r.json().get("text") or "").strip()
    except Exception:
        return r.text.strip()


class ImagineError(Exception):
    """MOM_IMAGINE_URL is not set, or the call itself failed. Callers decide
    what "no picture today" looks like on the page -- this only ever says
    why, it never guesses at a fallback."""


def draw(prompt: str, negative: str = "") -> bytes:
    """A simple illustration of `prompt`, from the same SDXL-turbo service
    Kitchen already uses for recipe steps (Kitchen/app/art.py -- same
    /draw route, same raw-PNG-bytes response, mirrored here rather than
    reinvented). Raises ImagineError rather than returning None -- nothing
    here is on a path a person is blocked waiting for, so the caller
    decides how loud "the picture didn't generate" should be."""
    if not IMAGINE_URL:
        raise ImagineError("MOM_IMAGINE_URL is not set.")
    try:
        r = httpx.post(f"{IMAGINE_URL}/draw",
                       json={"prompt": prompt, "negative": negative, "seed": 0},
                       timeout=IMAGINE_TIMEOUT)
    except httpx.RequestError as e:
        raise ImagineError(f"Could not reach imagine ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise ImagineError(f"imagine said {r.status_code}: {r.text[:200]}")
    if r.content[:8] != b"\x89PNG\r\n\x1a\n":
        raise ImagineError("imagine answered but it was not a PNG.")
    return r.content


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.I)


def strip_thinking(t: str) -> str:
    t = _THINK.sub("", t)
    if "<think>" in t.lower():
        i = t.lower().rfind("</think>")
        t = t[i + 8:] if i != -1 else t.split("<think>")[0]
    return t


def chat(messages: list[dict], temperature: float = 0.3,
         model: Optional[str] = None, num_ctx: Optional[int] = None) -> str:
    body = {
        "model": model or OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "think": OLLAMA_THINK,
        "options": {"num_ctx": num_ctx or OLLAMA_NUM_CTX, "temperature": temperature},
    }
    r = httpx.post(ollama_url("/api/chat"), json=body, timeout=OLLAMA_TIMEOUT)
    if r.status_code == 400 and "think" in r.text.lower():
        body.pop("think", None)
        r = httpx.post(ollama_url("/api/chat"), json=body, timeout=OLLAMA_TIMEOUT)
    r.raise_for_status()
    return strip_thinking((r.json().get("message") or {}).get("content", "")).strip()


# ---------------------------------------------------------------------------
#  Talking to the router instead of straight to Ollama
# ---------------------------------------------------------------------------
#  WHY THIS EXISTS
#
#  This app was the only one on the machine that spoke to Ollama directly.
#  Everything else -- the phone line, Hermes, the recipe importer, the
#  knowledge base -- goes through LiteLLM and asks for a JOB ("fast", "chat",
#  "vision") rather than naming a model. That indirection is the whole point of
#  having a router: swap what answers a job and every app changes at once.
#
#  Being the exception cost three things:
#    * a model change had to be made in two places, and drifted
#    * this app could not use anything the router adds -- including the cloud
#      escalation, which is why /mama could not reach Claude
#    * nothing it did showed up in the router's own metrics
#
#  So: when MOM_ROUTER_URL is set, calls go through the router by job name.
#  Unset, they go straight to Ollama exactly as before. The direct path is kept
#  rather than deleted because it is the one that still works when LiteLLM is
#  down, and "the phone line is up but Mama cannot answer" is a bad trade.
ROUTER_URL = os.getenv("MOM_ROUTER_URL", "").rstrip("/")
ROUTER_KEY = os.getenv("LITELLM_MASTER_KEY", "")


def via_router(messages: list[dict], job: str, temperature: float = 0.3,
               tools: Optional[list] = None) -> dict:
    """One call to LiteLLM, in the OpenAI shape. Returns the message dict.

    Raises on anything unexpected. Every caller falls back to the direct
    Ollama path, so a router that is down or confused costs a wasted request
    rather than an answer.
    """
    body = {"model": job, "messages": messages, "temperature": temperature}
    if tools:
        body["tools"] = tools
    headers = {"Content-Type": "application/json"}
    if ROUTER_KEY:
        headers["Authorization"] = f"Bearer {ROUTER_KEY}"
    r = httpx.post(f"{ROUTER_URL}/v1/chat/completions", json=body,
                   headers=headers, timeout=OLLAMA_TIMEOUT)
    r.raise_for_status()
    return (r.json().get("choices") or [{}])[0].get("message") or {}


def chat_tools(messages: list[dict], model: Optional[str] = None,
               temperature: float = 0.3, tools: Optional[list] = None,
               num_ctx: Optional[int] = None) -> dict:
    """chat(), but the model may ask to call a tool.

    Returns the whole MESSAGE rather than its text, because the interesting
    part is `tool_calls` and a caller that only gets a string cannot see them.

    Raises on anything unexpected, deliberately: the loop in router._with_tools
    catches it and falls back to a plain call. An Ollama too old to understand
    `tools` should cost a wasted request, not an error page.
    """
    body = {
        "model": model or OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        # OFF for a tool loop, whatever the global setting says. A thinking
        # model spends its budget reasoning and then returns empty content --
        # which in a loop reads as "no tool calls and nothing to say", so the
        # loop exits early with an empty answer.
        "think": False,
        "options": {"num_ctx": num_ctx or OLLAMA_NUM_CTX,
                    "temperature": temperature},
    }
    if tools:
        body["tools"] = tools
    r = httpx.post(ollama_url("/api/chat"), json=body, timeout=OLLAMA_TIMEOUT)
    if r.status_code == 400 and "think" in r.text.lower():
        body.pop("think", None)
        r = httpx.post(ollama_url("/api/chat"), json=body, timeout=OLLAMA_TIMEOUT)
    r.raise_for_status()
    return r.json().get("message") or {}


def chat_raw(messages: list[dict], model: Optional[str] = None,
             temperature: float = 0.3, num_ctx: Optional[int] = None) -> str:
    """chat(), but the messages may carry images.

    Ollama takes images as base64 strings in an "images" list on the message,
    which is why this exists separately: the normal chat() path strips anything
    it does not recognise, and silently dropping the image would produce a
    confident answer about a photo the model never saw.

    Vision models are also given a smaller context on purpose. Each image is
    worth hundreds of tokens once tiled, and this runs on a card that is already
    close to full -- a long history plus two photos is how a request that worked
    yesterday starts spilling to the CPU today.
    """
    body = {
        "model": model or OLLAMA_MODEL,
        "messages": messages,
        "stream": False,
        "think": OLLAMA_THINK,
        "options": {"num_ctx": num_ctx or min(OLLAMA_NUM_CTX, 8192),
                    "temperature": temperature},
    }
    r = httpx.post(ollama_url("/api/chat"), json=body, timeout=OLLAMA_TIMEOUT)
    if r.status_code == 400 and "think" in r.text.lower():
        body.pop("think", None)
        r = httpx.post(ollama_url("/api/chat"), json=body, timeout=OLLAMA_TIMEOUT)
    r.raise_for_status()
    return strip_thinking((r.json().get("message") or {}).get("content", "")).strip()


def available_models() -> list[str]:
    try:
        r = httpx.get(ollama_url("/api/tags"), timeout=5)
        r.raise_for_status()
        return sorted(m["name"] for m in r.json().get("models", []))
    except Exception:
        return []
