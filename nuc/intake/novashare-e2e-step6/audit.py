"""audit.py - the Novashare relay audit log, without content (E2E Step 6, 2026-11-24).

One JSON line per relay event in AUDIT_DIR/audit.jsonl (INTAKE_RELAY_AUDIT_DIR, default <relay dir's parent>/
.novashare-audit: beside the relay store, never inside it, so the sweep's orphan scan never sees it).

WHAT A LINE HOLDS (and nothing else): ts (unix seconds), ev (event name), t (transfer id cut to 8 characters),
size (ciphertext bytes), n (a count: pieces, devices, items), dev (a device id HASHED: HMAC-SHA256 with a random
32-byte key kept in AUDIT_DIR/key, mode 600, first 12 hex), code (a refusal code). Never a person id, name, email,
file name, caption, key or manifest byte: event() only accepts those fields, and every value is checked for its shape.

ROTATION: at ROTATE_BYTES (1 MiB) audit.jsonl becomes audit.jsonl.1 (… up to .5; the oldest falls off). The sweep
also deletes rotated files older than 90 days (prune()). Rotate + append run under a thread lock and a flock, as
app.say does (two threads, or the app and a script, can never both rotate).

READABLE BY JASON ONLY: the folder is mode 700, the files 600 (the container user); the only HTTP reader is
GET /relay/api/audit in sweep.py, which answers portal.ADMIN alone (anyone else: 404).

Writing never raises into a request: a failed write is one ERROR line in intake.log (without the event).
OUTCOME: test_sweep.py (shape, hashing, rotation, no names / emails / file names in the file, mode 600).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import re
import threading
import time

ROTATE_BYTES = 1048576
KEEP = 5
KEEP_DAYS = 90
_LOCK = threading.Lock()
_KEY: dict[str, bytes] = {}
_EV_RE = re.compile(r"^[a-z_]{2,24}$")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{22}$")


def audit_dir() -> pathlib.Path:
    env = os.environ.get("INTAKE_RELAY_AUDIT_DIR", "").strip()
    if env:
        return pathlib.Path(env)
    import relay
    return relay.relay_dir().parent / ".novashare-audit"


def _say(msg: str) -> None:
    import devices
    devices._say(msg)


def _key(d: pathlib.Path) -> bytes:
    """The per-install HMAC key for device ids (made once, mode 600). Callers hold _LOCK."""
    k = _KEY.get(str(d))
    if k:
        return k
    p = d / "key"
    try:
        fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, os.urandom(32))
        finally:
            os.close(fd)
    except FileExistsError:
        pass
    k = p.read_bytes()
    if len(k) != 32:
        raise OSError("audit key has the wrong size")
    _KEY[str(d)] = k
    return k


def dev_hash(did: str, d: pathlib.Path | None = None) -> str:
    """12 hex of HMAC-SHA256(key, device id): the same device gives the same tag in this log, nothing more."""
    d = d or audit_dir()
    with _LOCK:
        d.mkdir(mode=0o700, parents=True, exist_ok=True)
        return hmac.new(_key(d), did.encode(), hashlib.sha256).hexdigest()[:12]


def line(ev: str, tid: str | None = None, size: int | None = None, n: int | None = None,
         dev: str | None = None, code: str | None = None, now: int | None = None) -> dict:
    """The record for one event, every field shape-checked (ValueError otherwise). Device id hashed here."""
    if not _EV_RE.match(ev):
        raise ValueError("event")
    rec: dict = {"ts": int(now if now is not None else time.time()), "ev": ev}
    if tid is not None:
        if not _ID_RE.match(tid):
            raise ValueError("tid")
        rec["t"] = tid[:8]
    for k, v in (("size", size), ("n", n)):
        if v is not None:
            if not isinstance(v, int) or isinstance(v, bool) or v < 0:
                raise ValueError(k)
            rec[k] = v
    if dev is not None:
        if not _ID_RE.match(dev):
            raise ValueError("dev")
        rec["dev"] = dev_hash(dev)
    if code is not None:
        if not _EV_RE.match(code):
            raise ValueError("code")
        rec["code"] = code
    return rec


def _rotate(p: pathlib.Path) -> None:
    for i in range(KEEP - 1, 0, -1):
        src = p.with_name(f"{p.name}.{i}")
        if src.exists():
            os.replace(src, p.with_name(f"{p.name}.{i + 1}"))
    os.replace(p, p.with_name(p.name + ".1"))


def event(ev: str, **kw) -> None:
    """Append one event. Never raises (a bad field or a failed write = one ERROR line in intake.log)."""
    import fcntl
    try:
        rec = line(ev, **kw)
        d = audit_dir()
        data = (json.dumps(rec, separators=(",", ":")) + "\n").encode()
        with _LOCK:
            d.mkdir(mode=0o700, parents=True, exist_ok=True)
            p = d / "audit.jsonl"
            with open(d / "audit.lock", "a") as lk:
                os.chmod(d / "audit.lock", 0o600)
                fcntl.flock(lk, fcntl.LOCK_EX)
                try:
                    if p.exists() and p.stat().st_size + len(data) > ROTATE_BYTES:
                        _rotate(p)
                    fd = os.open(p, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                    try:
                        os.write(fd, data)
                    finally:
                        os.close(fd)
                finally:
                    fcntl.flock(lk, fcntl.LOCK_UN)
    except (OSError, ValueError) as e:
        _say(f"ERROR relay audit: {type(e).__name__}")


def tail(limit: int = 200) -> list[dict]:
    """The newest `limit` events (current file, then .1 if needed), oldest first. For the admin route only."""
    d = audit_dir()
    out: list[dict] = []
    for name in ("audit.jsonl", "audit.jsonl.1"):
        p = d / name
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        recs = []
        for ln in lines:
            try:
                recs.append(json.loads(ln))
            except ValueError:
                continue
        out = recs + out
        if len(out) >= limit:
            break
    return out[-limit:]


def prune(now: int | None = None) -> int:
    """Delete rotated files (audit.jsonl.N, fixed names only) older than KEEP_DAYS. Returns how many."""
    now = int(now if now is not None else time.time())
    d = audit_dir()
    n = 0
    with _LOCK:
        for i in range(1, KEEP + 1):
            p = d / f"audit.jsonl.{i}"
            try:
                if now - p.stat().st_mtime > KEEP_DAYS * 86400:
                    p.unlink()
                    n += 1
            except FileNotFoundError:
                continue
    return n
