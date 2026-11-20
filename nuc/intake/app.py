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

import hashlib
import hmac
import json
import os
import pathlib
import re
import shutil
import urllib.request
import urllib.parse
import tempfile
import time

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

# The module as well as the function: GRANT_FOR_KIND and
# PENDING_ROOT are tables that belong beside the classifier, not
# copies of them here that would drift the first time one changed.
import classify as classify_mod
from classify import PEOPLE, SHELF, classify
import portal

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

app = FastAPI(title="intake")

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
#
#  SHARED KEY (T0925 part 2, 2026-11-19): an allowed address alone is not
#  enough. Any other process on the NUC host is also "192.168.1.86". When
#  INTAKE_KEY is set, every peer except 127.0.0.1 (the healthcheck) must send
#  X-Drop-Key with that value: Caddy adds it (header_up X-Drop-Key
#  {env.DROP_KEY}), the PC auth service adds it from its own INTAKE_KEY.
#  Unset = not enforced, so the rollout can go Caddy + PC first, intake last.
#  Compared with hmac.compare_digest (no timing hint about the value).
# ---------------------------------------------------------------------------
ALLOWED_PEERS = {p.strip() for p in
                 os.environ.get("INTAKE_ALLOWED_PEERS", "127.0.0.1").split(",") if p.strip()}
DROP_KEY = os.environ.get("INTAKE_KEY", "").strip().encode()
READ_METHODS = {"GET", "HEAD", "OPTIONS"}
VIDEO_EXT = {".mp4", ".m4v", ".mkv", ".mov", ".webm", ".avi", ".wmv", ".mpg", ".mpeg", ".ts", ".3gp"}


@app.middleware("http")
async def gatekeeper(request: Request, call_next):
    """Refuse unknown peers (A1), peers without the shared key, and writes by view-only users (A7)."""
    peer = request.client.host if request.client else ""
    if peer not in ALLOWED_PEERS:
        return JSONResponse({"error": "not allowed from this address"}, status_code=403)
    if DROP_KEY and peer != "127.0.0.1":
        sent = (request.headers.get("x-drop-key") or "").strip().encode()
        if not hmac.compare_digest(sent, DROP_KEY):
            return JSONResponse({"error": "not allowed without the service key"}, status_code=403)
    level = (request.headers.get("x-auth-level") or "").strip().lower()
    if level == "view" and request.method not in READ_METHODS:
        return JSONResponse({"error": "your access here is view-only"}, status_code=403)
    return await call_next(request)


def say(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


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
    name = re.sub(r"[\x00-\x1f]", "", name).strip().lstrip(".")
    name = re.sub(r'[<>:"|?*]', "_", name)
    return name[:200] or "unnamed"


def place(tmp: pathlib.Path, dest_dir: pathlib.Path, name: str,
          digest: str, meta: dict) -> dict:
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Already here, under any name? The checksum decides, so a volume
    # re-sent from a phone does not land twice.
    for existing in dest_dir.iterdir():
        if existing.is_file() and existing.stat().st_size == tmp.stat().st_size:
            if sha256(existing) == digest:
                tmp.unlink(missing_ok=True)
                say(f"  duplicate of {existing.name}, skipped")
                return {"status": "duplicate", "path": str(existing)}

    target = dest_dir / name
    if target.exists():
        target = dest_dir / f"{target.stem}-{digest[:8]}{target.suffix}"

    # Write to a .part in the DESTINATION directory then rename. Kavita and
    # Paperless both watch these folders; a rename within one filesystem is
    # atomic, so neither can ever see a half-written file and try to open
    # it. Copying straight in is how you get a library full of corrupt
    # entries that only appear on slow transfers.
    part = target.with_suffix(target.suffix + ".part")
    shutil.move(str(tmp), str(part))
    part.rename(target)
    return {"status": "filed", "path": str(target)}


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
        probe = resumable.STAGE / ".healthz"
        probe.write_text("ok")
        probe.unlink()
    except OSError as e:
        problems.append(f"staging not writable: {e.strerror or e}")
    try:
        free_mb = shutil.disk_usage(resumable.STAGE).free // 1048576
        if free_mb * 1048576 < resumable.RESERVE:
            problems.append(f"only {free_mb} MB free")
    except OSError as e:
        free_mb = -1
        problems.append(f"no disk info: {e.strerror or e}")
    body = {"ok": not problems, "free_mb": free_mb, "problems": problems,
            "allow_telegram": len(ALLOW), "allow_mail": len(MAIL_ALLOW)}
    return JSONResponse(body, status_code=200 if not problems else 503)


@app.post("/intake")
async def intake(
    file: UploadFile = File(...),
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


def _intake_sync(tmp: pathlib.Path, name: str, size: int, who: str, sender: str,
                 source: str, caption: str | None) -> dict:
    """The blocking half of /intake: hash, classify, place (runs in a thread, T0925 A3)."""
    digest = sha256(tmp)
    verdict = classify(str(tmp), name, caption, who)
    say(f"{source} from {who}: {name} ({size/1048576:.1f}MB) "
        f"-> {verdict['kind']} :: {verdict['why']}")

    if verdict["dest"]:
        res = place(tmp, pathlib.Path(verdict["dest"]), name, digest, verdict)
    else:
        # Unsorted, WITH a sidecar. The reason it could not be placed is
        # the only thing that makes the pile actionable later.
        res = place(tmp, UNSORTED, name, digest, verdict)
        side = pathlib.Path(res["path"]).with_suffix(".intake.json")
        side.write_text(json.dumps(
            {**verdict, "sender": sender, "source": source,
             "caption": caption, "sha256": digest,
             "at": time.strftime("%Y-%m-%d %H:%M:%S")},
            indent=2), encoding="utf-8")

    say(f"  {res['status']}: {res['path']}")
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
AUTH_LEVELS_URL = os.environ.get(
    "AUTH_LEVELS_URL", "https://example.org/auth/levels")
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
    url = "{}?email={}&apps={}".format(
        AUTH_LEVELS_URL, urllib.parse.quote(email), urllib.parse.quote(app))
    req = urllib.request.Request(url)
    req.add_header("X-Service-Key", SERVICE_KEY)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        say("level check failed for {} on {}: {}".format(email, app, e))
        return None
    got = (body.get("levels") or {}).get(app)
    return got if got in ("view", "full") else None


@app.post("/upload")
async def upload(
    file: UploadFile = File(...),
    hint: str | None = Form(None),
    shelf: str | None = Form(None),
    x_auth_email: str | None = Header(default=None),
):
    """Same routing as /intake, but the person comes from the gate."""
    email = (x_auth_email or "").strip().lower()
    who = _person(email)
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
            "why": f"uploaded by {who or email} to the {sub} library",
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
                       .format(email, lvl or "no", grant),
            }

    say(f"drop from {email}: {name} ({size/1048576:.1f}MB) "
        f"-> {verdict['kind']} :: {verdict['why']}")

    if verdict["dest"]:
        res = place(tmp, pathlib.Path(verdict["dest"]), name, digest, verdict)
    else:
        res = place(tmp, UNSORTED, name, digest, verdict)
        side = pathlib.Path(res["path"]).with_suffix(".intake.json")
        side.write_text(json.dumps(
            {**verdict, "uploader": email, "source": "drop",
             "sha256": digest,
             "at": time.strftime("%Y-%m-%d %H:%M:%S")},
            indent=2), encoding="utf-8")

    say(f"  {res['status']}: {res['path']}")
    return {**res, **verdict, "size": size}


# Phone share sheet -> Drop (T0872, 2026-11-18): /manifest.webmanifest, /sw.js, /icon.svg, POST /share.
from share import router as share_router  # noqa: E402
app.include_router(share_router)

# Atomic Share resumable sends (T0884, 2026-11-18): /up/* pieces + /sending page with progress and resume.
from resumable import router as resumable_router  # noqa: E402
from atomic_ui import router as atomic_ui_router  # noqa: E402
app.include_router(resumable_router)
app.include_router(atomic_ui_router)
