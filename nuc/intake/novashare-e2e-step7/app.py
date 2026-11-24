#!/usr/bin/env python3
"""The intake door. n8n hands it a file; it decides and files it.

Jason, 2026-09-11: "can we set up an n8n manager to work with these kind of
files ... sending you through telegram and automating all the process for
me without me doing all the manual labor".

===========================================================================
WHY THIS IS A SERVICE AND NOT AN n8n WORKFLOW.

n8n is doing the part it is genuinely good at: sitting on a Telegram
connection and a mailbox, waking up, and carrying bytes. That is transport,
and hand-writing it would be a waste.

The DECIDING does not belong in there. An n8n workflow is a JSON blob of
node positions and embedded code snippets. You cannot read it in a diff,
you cannot review a change to it, and the reasoning behind "why does a
scanned PDF go to Paperless but a text one might not" has nowhere to live
except a sticky-note node. Every other decision on this stack --
paperless_route.py, paperless_dest.py, paperless_autotag.py -- is
commented Python in git for exactly that reason. This is the same.

So: n8n owns the connection, this owns the judgement, and the boundary
between them is one HTTP POST.

===========================================================================
THE ALLOWLIST IS THE WHOLE SECURITY MODEL, AND IT IS ENFORCED TWICE.

A Telegram bot is reachable by anyone who finds its @name. There is no
"private" bot. So the sender's numeric Telegram ID is checked against
TELEGRAM_ALLOW before anything is read -- by n8n, and AGAIN here, because
"the caller already checked" is how every one of these ends up open.
Jason's rule for the mail pipeline was "i dont want spam being filtered in
just senders in my sso list"; this is that rule, for a different pipe.

Numeric IDs, never @usernames. A Telegram username can be released and
re-registered by somebody else; the numeric ID is permanent.

===========================================================================
NOTHING IS EVER DELETED AND NOTHING IS EVER LOST. A file that cannot be
classified goes to unsorted/ with a sidecar .json saying what was known
about it. A duplicate is recognised by SHA-256 and skipped rather than
written twice under "(1)". Both of those are cheap; the alternative is a
pipeline people stop trusting the first time something vanishes.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
import json
import os
import pathlib
import re
import shutil
import tempfile
import threading
import time
import urllib.parse
import urllib.request

# The module as well as the function: GRANT_FOR_KIND and
# PENDING_ROOT are tables that belong beside the classifier, not
# copies of them here that would drift the first time one changed.
import classify as classify_mod
import portal
import scan  # T0920 virus check

# T0927 slice 2 (audit phase 4 item 4): these had the household's own addresses as built-in defaults, so a copy
# run by someone else would quietly ask OUR servers. Now they must be set (compose.yml), and the app refuses to
# start, naming what is missing, instead of guessing.
REQUIRED_SETTINGS = ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL")
_missing = [name for name in REQUIRED_SETTINGS if not os.environ.get(name, "").strip()]
if _missing:
    raise SystemExit(f"intake: required setting(s) missing: {', '.join(_missing)} (set them in compose.yml)")
from classify import classify
from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

UNSORTED = pathlib.Path(os.environ.get("INTAKE_UNSORTED", "/srv/unsorted"))
LOG = pathlib.Path(os.environ.get("INTAKE_LOG", "/srv/unsorted/intake.log"))
MAX_MB = int(os.environ.get("INTAKE_MAX_MB", "2048"))

# T0925 part 2 (A8, 2026-11-19): temp files live on the SAME disk as the libraries (TMPDIR is set to
# /srv/unsorted/.tmp in the Dockerfile/compose), so filing is a rename, not a 2 GB copy out of the
# container's /tmp. Created here because tempfile silently falls back to /tmp if the folder is missing.
_tmpdir = os.environ.get("TMPDIR")
if _tmpdir:
    try:
        pathlib.Path(_tmpdir).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass

# id -> person. Numeric Telegram IDs, and email addresses for the mail lane.
#   TELEGRAM_ALLOW='{"123456789":"jason","987654321":"casey"}'
ALLOW: dict[str, str] = json.loads(os.environ.get("TELEGRAM_ALLOW", "{}"))
MAIL_ALLOW: dict[str, str] = json.loads(os.environ.get("MAIL_ALLOW", "{}"))

@contextlib.asynccontextmanager
async def _lifespan(_app):
    """Start the hourly sweep of leftovers (T0931 item 13, 2026-11-20); stop it with the app."""
    import resumable
    # 2026-11-23: stale finish locks are cleared HERE, at real server start (was: at import of resumable, which the
    # hourly rescan_held.py import also triggered, deleting live locks). Only uvicorn runs this lifespan.
    resumable._clear_finish_locks()
    task = asyncio.create_task(resumable.sweep_loop())
    if not INTAKE_TOKEN:
        say("WARNING: INTAKE_TOKEN is not set; /intake still accepts the shared drop key (T0931 item 10)")
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(title="intake", lifespan=_lifespan)

# ---------------------------------------------------------------------------
#  WHO MAY TALK TO THIS CONTAINER AT ALL (T0924 A1, 2026-11-19)
#
#  The app trusts X-Auth-Email, which only Caddy's forward_auth may set. The
#  port is published on the LAN (192.168.1.86:8100) because the PC's auth
#  service (/app/file, rust/auth/src/llm.rs) posts here directly. Audit
#  2026-11-19 proved any LAN device could reach it and claim any email
#  (curl from the PC: /healthz 200). So: only known peers get in.
#    127.0.0.1       the container's own healthcheck
#    192.168.1.86   Caddy on the NUC (host network; seen as the NUC's LAN IP)
#    192.168.1.100  the PC (auth service via Docker Desktop NAT)
#  INTAKE_ALLOWED_PEERS overrides it (comma list). Missing env = 127.0.0.1
#  only: fails CLOSED.
#
#  A7: X-Auth-Level "view" may look but not write. The gate already refuses
#  most view-only writes (rust/auth view_blocks); this is the second lock.
#  T0931 item 8 (2026-11-20): the second lock is now an ALLOW-list, not a deny-list. A write needs
#  X-Auth-Level "full" (Caddy copies it from /verify for every signed-in person: "full" or "view").
#  Outcome of the check on the code: the gate always sends one of the two, so no phone is affected; a
#  missing or odd value used to pass as if it were "full". Two explicit exceptions, both for callers that
#  never pass the gate and so have no level:
#    - the PC auth service (/app/file -> POST /upload): a peer listed in INTAKE_SERVICE_PEERS
#      (default 192.168.1.100) that sends the right X-Drop-Key;
#    - n8n -> POST /intake: with INTAKE_TOKEN set, it must send X-Intake-Token (item 10, below).
#
#  SHARED KEY (T0925 part 2, 2026-11-19): an allowed address alone is not
#  enough. Any other process on the NUC host is also "192.168.1.86". Every
#  peer except 127.0.0.1 (the healthcheck) must send X-Drop-Key with the
#  INTAKE_KEY value: Caddy adds it (header_up X-Drop-Key {env.DROP_KEY}),
#  the PC auth service adds it from its own INTAKE_KEY.
#  T0931 item 7 (2026-11-20): INTAKE_KEY unset is no longer "not enforced"; the app REFUSES TO START
#  (the live container has had it set since 11-19, so nothing changes there). Compared with
#  hmac.compare_digest (no timing hint about the value).
#
#  T0931 item 10: /intake is called by n8n, which is not a person and not Caddy. Sharing the drop key
#  with Caddy and the PC meant any of them could post files "from" any Telegram sender. When
#  INTAKE_TOKEN is set, /intake wants X-Intake-Token (and the drop key alone no longer opens it).
#  Unset = the old rule (drop key) so the rollout can go intake first, n8n second; a warning is logged.
# ---------------------------------------------------------------------------
ALLOWED_PEERS = {p.strip() for p in
                 os.environ.get("INTAKE_ALLOWED_PEERS", "127.0.0.1").split(",") if p.strip()}
SERVICE_PEERS = {p.strip() for p in
                 os.environ.get("INTAKE_SERVICE_PEERS", "192.168.1.100").split(",") if p.strip()}
DROP_KEY = os.environ.get("INTAKE_KEY", "").strip().encode()
INTAKE_TOKEN = os.environ.get("INTAKE_TOKEN", "").strip().encode()
if not DROP_KEY:
    raise RuntimeError("INTAKE_KEY is not set: refusing to start (T0931 item 7). Set it in compose.")
# Review 11-23 #7: with no token, any signed-in "full" user could POST /intake with someone else's sender id.
# n8n sends the token (live since T0931 item 10), so an empty one now stops start-up like INTAKE_KEY.
if not INTAKE_TOKEN:
    raise RuntimeError("INTAKE_TOKEN is not set: refusing to start (review 2026-11-23 #7). Set it in compose.")
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
# Review 11-23 #12 (Jason: "yes, a jellyfin grant"): adding a video needs "full" on this auth-service app.
VIDEO_GRANT = os.environ.get("INTAKE_VIDEO_GRANT", "jellyfin").strip()
VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".mov", ".webm", ".avi", ".wmv", ".mpg", ".mpeg", ".ts", ".3gp"}


def _same(sent: str | None, want: bytes) -> bool:
    return bool(want) and hmac.compare_digest((sent or "").strip().encode(), want)


# T0931 /app/file (2026-11-20): a peer entry may also be a container NAME on intake's docker network ("auth-nuc").
# The NUC sign-in service calls intake as http://intake:8100 (the host address is firewalled from containers,
# tested: 000), so intake sees its container address, which changes on every recreate. Names are resolved with
# docker's own DNS and cached for 30 s; only containers on this compose network can answer to them.
_PEER_CACHE: dict = {}


def _peers(entries: set, peer: str = "") -> set:
    """IP addresses for a set of peer entries (IP literals as they are, names resolved, unresolvable ones skipped).

    A peer that is not in the cached set triggers a fresh lookup (at most every 2 s): a container that was just
    recreated has a new address. OUTCOME 2026-11-20 sandbox: with a plain 30 s cache, the healthcheck filled the
    cache before the caller container existed and every call from it got 403 for half a minute.
    """
    import ipaddress
    import socket
    key = frozenset(entries)
    hit = _PEER_CACHE.get(key)
    if hit:
        age = time.monotonic() - hit[0]
        if age < 30 and (peer in hit[1] or age < 2):
            return hit[1]
    out = set()
    for e in entries:
        try:
            out.add(str(ipaddress.ip_address(e)))
        except ValueError:
            try:
                out |= {ai[4][0] for ai in socket.getaddrinfo(e, None, proto=socket.IPPROTO_TCP)}
            except OSError:
                pass                     # name not on the network right now: it simply is not allowed
    _PEER_CACHE[key] = (time.monotonic(), out)
    return out


@app.middleware("http")
async def gatekeeper(request: Request, call_next):
    """Refuse unknown peers (A1), peers without the shared key, and writes that are not "full" (A7/item 8)."""
    peer = request.client.host if request.client else ""
    if peer not in _peers(ALLOWED_PEERS, peer):
        return JSONResponse({"error": "not allowed from this address"}, status_code=403)
    write = request.method not in READ_METHODS
    # Review 11-23 #16: another site's page must not be able to make a signed-in visitor upload or share.
    # Browsers send Sec-Fetch-Site; only "cross-site" is refused (the share sheet sends "none", server calls none).
    if write and (request.headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return JSONResponse({"error": "not allowed from another site"}, status_code=403)
    # Review 11-23 #6: FastAPI spools the whole multipart body to disk BEFORE a handler runs, so the MAX_MB check
    # inside /intake and /upload came after the disk was already used, and /share had none. Refuse by the
    # declared length first. A chunked body (no Content-Length) is capped by Caddy's request_body max_size.
    if write and request.url.path in ("/intake", "/upload", "/share"):
        try:
            declared = int(request.headers.get("content-length") or 0)
        except ValueError:
            return JSONResponse({"error": "bad Content-Length"}, status_code=400)
        if declared > MAX_MB * 1048576 + 16 * 1048576:          # + multipart overhead
            return JSONResponse({"error": f"This file is bigger than the {MAX_MB / 1024:g} GB limit."}, status_code=413)
        import resumable
        try:
            free = shutil.disk_usage(tempfile.gettempdir()).free
        except OSError:
            free = None
        if free is not None and declared and free < declared + resumable.RESERVE:
            return JSONResponse({"error": "The server is short of space. Try again later."}, status_code=507)
    level = (request.headers.get("x-auth-level") or "").strip().lower()
    token_door = write and request.url.path == "/intake" and bool(INTAKE_TOKEN)
    if token_door:                       # n8n: its own token instead of the drop key
        if not _same(request.headers.get("x-intake-token"), INTAKE_TOKEN):
            return JSONResponse({"error": "not allowed without the intake token"}, status_code=403)
        return await call_next(request)
    key_ok = _same(request.headers.get("x-drop-key"), DROP_KEY)
    if peer != "127.0.0.1" and not key_ok:
        return JSONResponse({"error": "not allowed without the service key"}, status_code=403)
    if write and level != "full":
        service = (key_ok and not level and peer != "127.0.0.1"
                   and ((peer in _peers(SERVICE_PEERS, peer) and request.url.path == "/upload")
                        or request.url.path == "/intake"))
        if not service:
            return JSONResponse({"error": "your access here is view-only"}, status_code=403)
    return await call_next(request)


# Step 7 (2026-11-24, audit High 2a, Jason: "burn the old pages"): ONE strict header set for every answer of the
# drop site. A route that set its own CSP (the E2E pages: equal or stricter) keeps it; everything else (the library
# pages, JSON, the manifest, errors, the gatekeeper's own 403s) gets site_assets.SITE_CSP. Added after gatekeeper,
# so it is the outer layer and also covers what gatekeeper answers. OUTCOME: test_harden.py walks every route.
import site_assets  # noqa: E402


@app.middleware("http")
async def site_headers(request: Request, call_next):
    resp = await call_next(request)
    for k, v in site_assets.SITE_HEADERS.items():
        if k not in resp.headers:
            resp.headers[k] = v
    return resp


_LOG_MAX = int(os.environ.get("INTAKE_LOG_MAX_MB", "5")) * 1048576


def _rotate_log() -> None:
    """Keep intake.log from growing for ever: past _LOG_MAX it becomes .1 (.1 -> .2 -> .3, oldest dropped).

    T0931 item 14 (2026-11-20): the log was append-only. Checked with a 6 KB limit in test_t0931b.py.
    """
    try:
        if LOG.stat().st_size < _LOG_MAX:
            return
        for i in (2, 1):
            src = LOG.with_name(f"{LOG.name}.{i}")
            if src.exists():
                os.replace(src, LOG.with_name(f"{LOG.name}.{i + 1}"))
        os.replace(LOG, LOG.with_name(f"{LOG.name}.1"))
    except OSError:
        pass                             # no log yet, or a race with another writer: try again next line


_LOG_LOCK = threading.Lock()


def say(msg: str) -> None:
    """Print one line and append it to intake.log. Never put keys, tokens or file contents in `msg`.

    Race review 11-23 R6: rotate + append run under a thread lock and a flock on intake.log.lock, so two threads
    (or the server and rescan_held) cannot both rotate and lose a generation."""
    import fcntl
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_LOCK, open(LOG.with_name(LOG.name + ".lock"), "a") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            try:
                _rotate_log()
                with LOG.open("a", encoding="utf-8") as f:
                    f.write(line + "\n")
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
    except OSError as e:
        print(f"(log file not written: {e.strerror})", flush=True)   # the line above is still in docker logs


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_name(name: str) -> str:
    """A filename that cannot climb out of the directory it is put in.

    n8n passes through whatever Telegram reports, and Telegram passes
    through whatever the sender's client sent. A name of
    '../../etc/cron.d/x' is not hypothetical, it is the first thing anyone
    tries. basename alone is not enough on a mixed-separator world, so
    both separators are stripped before it.
    """
    name = name.replace("\\", "/").split("/")[-1]
    # T0931 item 9: also DEL, the C1 controls and the Unicode line/paragraph separators (they split a log line
    # or a WHY.txt just like \n does).
    name = re.sub(r"[\x00-\x1f\x7f-\x9f\u2028\u2029]", "", name).strip().lstrip(".")
    name = re.sub(r'[<>:"|?*]', "_", name)
    if name.lower().endswith(".intake.json"):   # R5: an upload must not look like (or overwrite) a note
        name = name[:-len(".intake.json")] + ".intake-json"
    return name[:200] or "unnamed"


_PLACE_LOCK = threading.Lock()          # guards _FOLDER_LOCKS only (race review 11-23 R1)
_FOLDER_LOCKS: dict[str, threading.Lock] = {}
PLACE_WAIT = float(os.environ.get("INTAKE_PLACE_WAIT", "120"))


@contextlib.contextmanager
def _folder_lock(dest_dir: pathlib.Path):
    """One lock per destination folder, for this process's threads AND other processes (rescan_held).

    Race review 11-23 R1: a single global lock was held while a NAS folder was listed and hashed, so one slow or
    hung share stopped every filing. Waits at most PLACE_WAIT seconds for each half, then raises TimeoutError (the
    caller keeps the file in .failed rather than hanging a pool thread)."""
    import fcntl
    import hashlib as _h
    import resumable
    key = str(dest_dir.resolve())
    with _PLACE_LOCK:
        tl = _FOLDER_LOCKS.setdefault(key, threading.Lock())
    if not tl.acquire(timeout=PLACE_WAIT):
        raise TimeoutError(f"busy filing into {dest_dir.name}")
    try:
        resumable.STAGE.mkdir(parents=True, exist_ok=True)
        with open(resumable.STAGE / f".place-{_h.sha1(key.encode()).hexdigest()[:12]}.lock", "a") as lk:
            end = time.monotonic() + PLACE_WAIT
            while True:
                try:
                    fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() > end:
                        raise TimeoutError(f"busy filing into {dest_dir.name} (other process)") from None
                    time.sleep(0.2)
            try:
                yield
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)
    finally:
        tl.release()


class NotMounted(RuntimeError):
    """The NAS share behind a library folder is not mounted (review 11-23 #4): filing would land on local disk."""


def _check_mounted(dest_dir: pathlib.Path) -> None:
    """Raise NotMounted when a sentinel in INTAKE_SENTINELS covers dest_dir and is missing (review 11-23 #4).

    /healthz already used the sentinels (T0931 item 13) but place() did not: with the share unmounted,
    mkdir(parents=True) built the library folder on the local disk and the file vanished behind the mount
    when the NAS came back. A sentinel /srv/media/general/.mounted covers everything under /srv/media/general.
    """
    for s in [x.strip() for x in os.environ.get("INTAKE_SENTINELS", "").split(",") if x.strip()]:
        root = pathlib.PurePosixPath(s).parent
        if (dest_dir == pathlib.Path(root) or pathlib.Path(root) in dest_dir.parents) and not pathlib.Path(s).exists():
            raise NotMounted(f"not mounted: {root.name}")


def place(tmp: pathlib.Path, dest_dir: pathlib.Path, name: str,
          digest: str, meta: dict) -> dict:
    """Move `tmp` into dest_dir as `name` (or name-<digest8>, -2, ...); a byte-identical file there = duplicate.

    Review 11-23 #1: the .part name was fixed ("<name>.part"), so two same-name files filed at the same moment
    (3 phone scans all called scan.pdf) both moved into it and one overwrote the other. Now the bytes go to a
    UNIQUE part first (the slow cross-disk copy, outside the lock), then duplicate check, name choice and the
    rename run under _PLACE_LOCK + flock(STAGE/.place.lock), which also covers the rescan_held process.
    """
    _check_mounted(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Write to a .part in the DESTINATION directory then rename. Kavita and
    # Paperless both watch these folders; a rename within one filesystem is
    # atomic, so neither can ever see a half-written file and try to open
    # it. Copying straight in is how you get a library full of corrupt
    # entries that only appear on slow transfers.
    import secrets as _secrets
    part = dest_dir / f".{name[:150]}.{_secrets.token_hex(6)}.part"
    shutil.move(str(tmp), str(part))
    try:
        with _folder_lock(dest_dir):
            # Already here, under any name? The checksum decides, so a volume
            # re-sent from a phone does not land twice.
            size = part.stat().st_size
            for existing in dest_dir.iterdir():
                if existing == part or existing.name.endswith(".part"):
                    continue
                try:                         # R2: Paperless may consume (delete) a file while we look at it
                    same = existing.is_file() and existing.stat().st_size == size and sha256(existing) == digest
                except OSError:
                    continue
                if same:
                    part.unlink(missing_ok=True)
                    say(f"  duplicate of {existing.name}, skipped")
                    return {"status": "duplicate", "path": str(existing)}
            stem, suffix = pathlib.PurePosixPath(name).stem, pathlib.PurePosixPath(name).suffix
            target = dest_dir / name
            if target.exists():
                target = dest_dir / f"{stem}-{digest[:8]}{suffix}"
            n = 2
            while target.exists():
                target = dest_dir / f"{stem}-{digest[:8]}-{n}{suffix}"
                n += 1
            part.rename(target)
    except BaseException:
        # R2: never leave the bytes as a hidden .part in the library; put them back where the caller keeps them
        if part.exists():
            try:
                shutil.move(str(part), str(tmp))
            except OSError:
                pass
        raise
    return {"status": "filed", "path": str(target)}


def write_sidecar(filed: str | pathlib.Path, data: dict) -> pathlib.Path:
    """Write the note for an unsorted file WITHOUT overwriting anything (race review 11-23 R5): exclusive create;
    if that name is taken, a short random suffix. Returns the note's path."""
    import secrets as _secrets
    p = sidecar_for(filed)
    text = json.dumps(data, indent=2)
    for cand in (p, p.with_name(f"{pathlib.Path(filed).name}.{_secrets.token_hex(3)}.intake.json")):
        try:
            with open(cand, "x", encoding="utf-8") as f:
                f.write(text)
            return cand
        except FileExistsError:
            continue
    raise FileExistsError(str(p))


def sidecar_for(filed: str | pathlib.Path) -> pathlib.Path:
    """The .intake.json note next to an unsorted file: '<full name>.intake.json' (review 11-23 #13; was
    with_suffix, so bill.pdf and bill.docx shared bill.intake.json and the second overwrote the first)."""
    p = pathlib.Path(filed)
    return p.with_name(p.name + ".intake.json")


@app.get("/healthz")
def healthz():
    """Healthy only if uploads could actually land (T0925 A14, 2026-11-19).

    Before, this answered ok whatever the state, so Docker showed "healthy" with a full or read-only disk.
    Now it writes and deletes a probe file in the staging folder and checks free space against the
    reserve the upload start uses; either failing answers 503, which the compose healthcheck counts.
    """
    import resumable
    problems = []
    try:
        resumable.STAGE.mkdir(parents=True, exist_ok=True)
        import secrets as _secrets
        probe = resumable.STAGE / f".healthz-{_secrets.token_hex(4)}"   # R8: overlapping checks must not collide
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        problems.append(f"staging not writable: {e.strerror or 'error'}")
    try:
        free_mb = shutil.disk_usage(resumable.STAGE).free // 1048576
        if free_mb * 1048576 < resumable.RESERVE:
            problems.append(f"only {free_mb} MB free")
    except OSError as e:
        free_mb = -1
        problems.append(f"no disk info: {e.strerror or e}")
    # T0931 item 13 (2026-11-20): (a) INTAKE_SENTINELS = comma list of files that exist only while a NAS share is
    # mounted (e.g. /srv/books/.mounted); a missing one means "unmounted, files would land on the local disk"
    # -> 503. (b) how many files wait in .failed for the owner (information only, not a failure).
    for sentinel in [x.strip() for x in os.environ.get("INTAKE_SENTINELS", "").split(",") if x.strip()]:
        if not pathlib.Path(sentinel).exists():
            problems.append(f"not mounted: {pathlib.PurePosixPath(sentinel).parent.name or sentinel}")
    try:
        failed = sum(1 for _ in resumable.FAILED.iterdir()) if resumable.FAILED.is_dir() else 0
    except OSError:
        failed = -1
    # T0962 point 2 (2026-11-21): with the scanner down every upload is HELD, not filed, so "uploads could
    # land" is false: 503 with a plain reason. "held" counts quarantine folders (information only).
    clamav = "off"
    if scan.ENABLED:
        clamav = "ok" if scan.ping() else "down"
        if clamav == "down":
            problems.append("virus check not answering (uploads are held, not filed)")
    body = {"ok": not problems, "free_mb": free_mb, "problems": problems, "failed": failed,
            "allow_telegram": len(ALLOW), "allow_mail": len(MAIL_ALLOW), "clamav": clamav,
            "held": scan.held_count()}
    return JSONResponse(body, status_code=200 if not problems else 503)


@app.post("/intake")
async def intake(
    file: UploadFile = File(...),  # noqa: B008  -- FastAPI's own parameter pattern (T0931 2026-11-20)
    sender: str = Form(...),
    source: str = Form("telegram"),
    caption: str | None = Form(None),
):
    """Receive one file. Decide. File it. Say what happened and why."""
    table = ALLOW if source == "telegram" else MAIL_ALLOW
    who = table.get(str(sender).strip().lower())
    if who is None:
        # Logged, because a stranger finding the bot is worth knowing about,
        # and refused with the same 403 whether the sender is unknown or
        # merely unmapped -- there is nothing to learn from the difference.
        say(f"REFUSED {source} sender={sender!r} file={file.filename!r} "
            f"-- not in the allowlist")
        raise HTTPException(403, "sender not allowed")

    name = safe_name(file.filename or "unnamed")
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="intake-"))
    tmp = tmp_dir / name
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_MB * 1048576:
                    raise HTTPException(413, f"This file is bigger than the {MAX_MB / 1024:g} GB limit.")
                out.write(chunk)

        # A3: hashing, the model call and the move run in a worker thread, so one big file does not
        # freeze every other phone's upload on the single event loop.
        return await run_in_threadpool(_intake_sync, tmp, name, size, who, sender, source, caption)
    except HTTPException:
        raise                            # refused (too big, not allowed): nothing worth keeping
    except Exception as e:
        # T0931 (re-audit A5): a crash while filing used to delete the file in `finally`; keep it instead.
        from resumable import keep_failed
        keep_failed(tmp_dir, who, name, [{"name": name, "status": "error", "detail": f"{type(e).__name__}: {e}"}])
        say(f"KEPT {source} file={name!r} after {type(e).__name__}")
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _virus_gate(tmp: pathlib.Path, name: str, digest: str, held_as: str, door: str) -> None:
    """T0927 slice 3: the ONE virus check every door runs before anything is classified or filed (T0920, T0962).
    Infected -> held in .quarantine + 422; scanner down -> held + 503 (kept safe, never filed unchecked, never
    lost); over the stream limit -> "skipped", filed as before. CLAMD_HOST unset = scanning off. `held_as` is what
    the WHY.txt records (a door and a person id, never an email: T0962 point 5)."""
    if not scan.ENABLED:
        return
    state, detail = scan.scan(tmp)
    # Review 11-23 #5: "skipped" (over the scan limit) is allowed for videos only. A 96 MB PDF or .docx (padding
    # is easy) went to Paperless or a shelf unchecked. Held as "too-big" (rescan_held re-checks only "error",
    # so it stays held for Jason to look at).
    if state == "skipped" and pathlib.PurePosixPath(name).suffix.lower() not in VIDEO_EXT:
        scan.hold(tmp, name, held_as, "too-big", detail, digest)
        say(f"HELD {door}: {name} -- too big for the virus check and not a video")
        raise HTTPException(422, "This file is too big for the virus check, so it was held for Jason to look at.")
    if state in ("infected", "error"):
        scan.hold(tmp, name, held_as, state, detail, digest)
        say(f"HELD {door}: {name} -- virus check {state}")
        if state == "infected":
            raise HTTPException(422, "This file looks unsafe (virus check), so it was held back and not filed.")
        raise HTTPException(503, "The virus check is not answering, so the file was kept safe and not filed yet.")


def _place_or_unsorted(tmp: pathlib.Path, name: str, digest: str, verdict: dict, sidecar: dict) -> dict:
    """T0927 slice 3: the ONE placement step. Into the verdict's library, or into unsorted WITH a .intake.json
    sidecar (the reason it could not be placed is the only thing that makes the pile actionable later)."""
    if verdict["dest"]:
        res = place(tmp, pathlib.Path(verdict["dest"]), name, digest, verdict)
    else:
        res = place(tmp, UNSORTED, name, digest, verdict)
        if res["status"] == "filed":     # review 11-23 #13: a duplicate keeps the original's note
            write_sidecar(res["path"],     # R5: exclusive create, never overwrites another note
                          {**verdict, **sidecar, "sha256": digest, "at": time.strftime("%Y-%m-%d %H:%M:%S")})
    say(f"  {res['status']}: {res['path']}")
    return res


def _intake_sync(tmp: pathlib.Path, name: str, size: int, who: str, sender: str,
                 source: str, caption: str | None) -> dict:
    """The blocking half of /intake: hash, virus check, classify, place (runs in a thread, T0925 A3)."""
    digest = sha256(tmp)
    # T0962 (2026-11-21): this door had no virus check (T0920 put it in route_file, which /intake does not use).
    # T0927 slice 3: both doors now call the same _virus_gate, so they cannot drift apart again.
    _virus_gate(tmp, name, digest, f"{source}:{who}", f"{source} from {who}")
    verdict = classify(str(tmp), name, caption, who)
    say(f"{source} from {who}: {name} ({size/1048576:.1f}MB) "
        f"-> {verdict['kind']} :: {verdict['why']}")

    res = _place_or_unsorted(tmp, name, digest, verdict, {"sender": sender, "source": source, "caption": caption})
    return {**res, **verdict, "size": size, "sha256": digest}


# ---------------------------------------------------------------------------
#  THE DROP PAGE. Identity comes from the gate, never from the form.
#
#  Caddy runs forward_auth in front of this hostname and copies the verified
#  address into X-Auth-Email. That header is the ONLY thing that decides
#  whose file this is -- there is deliberately no person selector, because a
#  selector is a control that can be left on its last value, and the failure
#  that produces is somebody's medical record filed under a teenager.
#
#  WHICH MEANS THIS MUST NEVER BE REACHABLE EXCEPT THROUGH CADDY. It trusts
#  a header, so anything that can set that header is anyone it names. The
#  container binds to the NUC's LAN address, the Caddy site strips any
#  inbound X-Auth-Email before forward_auth sets its own, and there is no
#  route in from outside that skips the gate.
# ---------------------------------------------------------------------------


def _person(email: str | None) -> str | None:
    if not email:
        return None
    return portal.WHO.get(email.strip().lower())


@app.get("/", response_class=HTMLResponse)
async def drop_page(x_auth_email: str | None = Header(default=None)):
    email = (x_auth_email or "").strip().lower()
    if not email:
        # Only reachable if the gate is missing or misconfigured. Refuse
        # rather than render a page that would file things as nobody.
        raise HTTPException(401, "no identity from the gate")
    return portal.page(email, _person(email))


# ---------------------------------------------------------------------------
#  permission: may this person WRITE to the library their file belongs in?
# ---------------------------------------------------------------------------
AUTH_LEVELS_URL = os.environ.get("AUTH_LEVELS_URL", "")  # required (T0927 slice 2), checked at the top
SERVICE_KEY = os.environ.get("SERVICE_KEY", "")


def level_for(email: str, app: str) -> str | None:
    """None, "view" or "full" -- straight from the auth service.

    WHY NOT X-Auth-Level. That header says how much of the DROP PAGE this
    person gets, which is the right answer to "may they upload at all" and
    the wrong one to "may they add a recipe". A recipe grant lives on a
    different app, and the only thing that knows is the auth service.

    FAILS CLOSED, TWICE OVER. No service key, no answer, a timeout or a
    malformed reply all return None -- which this module treats as "may
    read, may not write". A permission check that defaults open when the
    network hiccups is not a permission check.
    """
    if not SERVICE_KEY or not email or not app:
        return None
    url = f"{AUTH_LEVELS_URL}?email={urllib.parse.quote(email)}&apps={urllib.parse.quote(app)}"
    req = urllib.request.Request(url)
    req.add_header("X-Service-Key", SERVICE_KEY)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        # T0931 item 14: the exception text can hold the URL; log only its type
        say(f"level check failed for {_person(email) or 'unmapped'} on {app}: {type(e).__name__}")  # 11-23 #14
        return None
    got = (body.get("levels") or {}).get(app)
    return got if got in ("view", "full") else None


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),  # noqa: B008  -- FastAPI's own parameter pattern (T0931 2026-11-20)
    hint: str | None = Form(None),
    shelf: str | None = Form(None),
    x_auth_email: str | None = Header(default=None),
):
    """Same routing as /intake, but the person comes from the gate."""
    email = (x_auth_email or "").strip().lower()
    if not email:
        raise HTTPException(401, "no identity from the gate")

    name = safe_name(file.filename or "unnamed")
    tmp_dir = pathlib.Path(tempfile.mkdtemp(prefix="drop-"))
    tmp = tmp_dir / name
    size = 0
    try:
        with tmp.open("wb") as out:
            while chunk := await file.read(1 << 20):
                size += len(chunk)
                if size > MAX_MB * 1048576:
                    raise HTTPException(413, f"This file is bigger than the {MAX_MB / 1024:g} GB limit.")
                out.write(chunk)
        # A3: the blocking work (hash, model, grant lookup, move) runs in a worker thread.
        return JSONResponse(await run_in_threadpool(route_file, tmp, name, size, email, hint, shelf))
    except HTTPException:
        raise                            # refused (too big, held, not allowed): handled, nothing to keep here
    except Exception as e:
        # Review 11-23 #3: this door had no keep_failed, so a NAS hiccup or a bug deleted the bytes in `finally`.
        from resumable import keep_failed
        keep_failed(tmp_dir, email, name, [{"name": name, "status": "error", "detail": f"{type(e).__name__}: {e}"}])
        say(f"KEPT drop file from {_person(email) or 'unmapped'} after {type(e).__name__}")
        raise
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def route_file(tmp: pathlib.Path, name: str, size: int, email: str,
               hint: str | None = None, shelf: str | None = None) -> dict:
    """Classify, check the write grant and place ONE file that is already on disk. Blocking: call it in a
    thread (run_in_threadpool). `tmp` is MOVED into its library (a rename when on the same disk).

    T0925 part 2 (A3/A8, 2026-11-19): this was the body of /upload. Split out so the resumable finish and
    the share door hand it the file they already hold, instead of copying it into a second temp folder
    through an UploadFile (the 3-copies-per-file finding). Raises HTTPException like /upload did.
    """
    who = _person(email)
    digest = sha256(tmp)

    # T0920 (2026-11-21): virus check BEFORE anything is classified or filed (scan.py; clamd over the private
    # docker network). Infected -> held in .quarantine with a WHY.txt; scanner down -> also held (kept safe,
    # never filed unchecked, never lost); over the stream limit -> "skipped", filed as before (scan.py SIZE).
    # CLAMD_HOST unset = scanning off (old behaviour).
    # T0962 point 5 (Jason 11-21): the note keeps the person id, not the email address.
    # T0927 slice 3: the shared gate (same as /intake).
    _virus_gate(tmp, name, digest, f"drop:{who or 'unknown'}", f"drop from {who or 'unknown'}")
    if name.lower().endswith(".torrent"):   # 2026-11-23: a .torrent goes to qBittorrent (torrent.py), not unsorted
        import torrent
        return {**torrent.handle_file(tmp, name, email), "size": size}

    # A VIDEO IS NOT CLASSIFIED, IT IS PLACED. The classifier's job is
    # working out what an unknown file is; the page already said. And
    # "private" is a destination the uploader chose, which no amount of
    # reading the file could tell us.
    if hint == "video":
        # A9 (2026-11-19 audit): "video" skipped the classifier AND any type check, so an
        # .html or .exe could be filed into the media library. Only real video containers now.
        if pathlib.PurePosixPath(name).suffix.lower() not in VIDEO_EXT:
            raise HTTPException(415, "Only video files can go in the video library.")
        if shelf == "private" and email not in portal.PRIVATE_OK:
            # Checked here as well as hidden in the UI. A control that
            # is merely not rendered is not a permission.
            raise HTTPException(403, "You are not allowed to put files in the private library.")
        sub = "private" if shelf == "private" else "general"
        verdict = {
            "kind": "video", "person": who,
            "dest": f"/srv/media/{sub}", "unpack": False,
            "why": f"uploaded by {who or 'unmapped'} to the {sub} library",   # 11-23 #14: no email in notes
        }
    else:
        verdict = classify(str(tmp), name, None, who)

    # ---- may they WRITE to the library this belongs in? -------------
    #
    # The type is inferred; the permission is read off a fact, which is
    # the same asymmetry classify.py already applies to the PERSON.
    # Someone with `recipe:view` can search the recipes and cannot add
    # one -- the file is held for them rather than refused, because a
    # rejected upload just means they send it again or ask somebody to
    # do it for them, and neither leaves a record.
    grant = classify_mod.GRANT_FOR_KIND.get(verdict["kind"])
    if verdict["kind"] == "video" and not grant:
        grant = VIDEO_GRANT or None       # review 11-23 #12: videos had no grant check at all
    if grant:
        lvl = level_for(email, grant)
        if lvl != "full":
            held = pathlib.Path(classify_mod.PENDING_ROOT) / (
                who or "unassigned") / verdict["kind"]
            verdict = {
                **verdict,
                "dest": str(held),
                "held": True,
                "level": lvl or "none",
                "why": verdict["why"] + " | held: {} has {} access to "
                       "{}, so it is not filed into the shared library"
                       .format(who or "unmapped", lvl or "no", grant),   # 11-23 #14
            }

    say(f"drop from {who or 'unmapped'}: {name} ({size/1048576:.1f}MB) "   # 11-23 #14: person id, not email
        f"-> {verdict['kind']} :: {verdict['why']}")

    res = _place_or_unsorted(tmp, name, digest, verdict, {"uploader": email, "source": "drop"})
    return {**res, **verdict, "size": size}


# Phone share sheet -> Drop (T0872, 2026-11-18): /manifest.webmanifest, /sw.js, /icon.svg, POST /share.
from share import router as share_router

# Step 7 (2026-11-24): /assets/<file>, the drop pages' style and script files (no inline script or style anywhere).
app.include_router(site_assets.router)

app.include_router(share_router)

# Atomic Share resumable sends (T0884, 2026-11-18): /up/* pieces + /sending page with progress and resume.
from atomic_ui import router as atomic_ui_router
from resumable import router as resumable_router

app.include_router(resumable_router)
app.include_router(atomic_ui_router)

# 2026-11-23: the gateway routes page (/routes, /routes.json): where each share goes + live health
from gateway import router as gateway_router

app.include_router(gateway_router)

# Novashare E2E step 1 (2026-11-24): the device registry, /devices and /devices/api/* (devices.py). New routes only.
from devices import router as devices_router  # noqa: E402

app.include_router(devices_router)

# Novashare E2E step 2 (2026-11-24): the encrypted send, /relay/send and /relay/api/* (relay.py). New routes only.
from relay import router as relay_router  # noqa: E402

app.include_router(relay_router)

# Abyss albums keyring (2026-11-24): /keyring, the page Abyss /album/ asks to wrap/unwrap album keys (abyss_keyring.py). New routes only.
from abyss_keyring import router as keyring_router  # noqa: E402

app.include_router(keyring_router)

# Novashare E2E step 3 (2026-11-24): receive, /relay/inbox and /relay/api/inbox, accept, decline, complete, pieces (receive.py). New routes only.
from receive import router as receive_router  # noqa: E402

app.include_router(receive_router)

# Novashare E2E step 4 (2026-11-24): web push, /relay/api/push/* and /relay/push/push.js (push.py). New routes, plus one worker thread
# that starts after app.py's own lifespan startup and stops before its shutdown (rescan_held.py's import starts nothing).
import push as _novashare_push  # noqa: E402

app.include_router(_novashare_push.router)
app.router.lifespan_context = _novashare_push.wrap_lifespan(app.router.lifespan_context)

# Novashare E2E step 5 (2026-11-24): the share picker, /relay/share and /relay/api/pick* (pick.py). New routes only; the drop's
# service worker (atomic_ui.py) opens it for a share when the theme is horizon (INTAKE_SHARE_PICKER=0 turns it off).
from pick import router as pick_router  # noqa: E402

app.include_router(pick_router)

# Novashare E2E step 6 (2026-11-24): expiry sweep, sender cancel, quotas, audit log (sweep.py, audit.py). New routes
# (/relay/api/transfers/<id>/cancel, /relay/api/relay-stats and /relay/api/audit for the admin) plus one worker thread
# that sweeps the relay every 10 minutes, started after the lifespan startup above (rescan_held.py's import starts nothing).
import sweep as _novashare_sweep  # noqa: E402

app.include_router(_novashare_sweep.router)
app.router.lifespan_context = _novashare_sweep.wrap_lifespan(app.router.lifespan_context)
