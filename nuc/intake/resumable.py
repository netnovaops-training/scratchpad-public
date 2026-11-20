#!/usr/bin/env python3
"""Resumable uploads for Atomic Share: big files survive a dropped signal (T0884 step 1, 2026-11-18).

Jason, 2026-11-18: "a progress bar and a resume feature of bandwidth loss or signal drop".

WHY
Before this, a shared file travelled in ONE request (POST /share). If the phone lost signal at 90% of a
1 GB video, everything was lost and the share had to start again. Now the phone sends the file in 4 MB
pieces; the server remembers how many bytes it already has, so after a drop the phone asks "where was I?"
and carries on from there.

PROTOCOL (all behind the same SSO gate as the rest of drop.; the person is X-Auth-Email from Caddy)
  POST /up/start            {"name", "size", "hint"?, "shelf"?}  -> {"id", "offset": 0, "chunk"}
        hint "video" + shelf "general"/"private" = the Drop page's video card (T0926 U3, 2026-11-19).
  GET  /up/{id}                                       -> {"id", "offset", "size"}   (resume point)
  PUT  /up/{id}?offset=N    body = raw bytes           -> {"offset": N + len(body)}
        409 {"offset": real} when N is not where the server is (a retried piece); the phone jumps there.
        Header X-Piece-Sha256 (optional, 2026-11-19): SHA-256 of the piece, made by the browser's own Web
        Crypto (no extra download). The server hashes what arrived; a mismatch drops the piece and answers
        422 {"offset": N, "checksum": true}, so the page sends that piece again. Like TCP's checksum, per piece.
  POST /up/{id}/finish                                -> [results]  (same verdicts as /share)
  POST /up/{id}/cancel                                -> {"ok": true}

SAFETY
- An upload id is 128 random bits AND bound to the uploader's email: another signed-in person who learns
  the id still gets 404, so nobody can append to or finish someone else's file.
- Size is declared up front and capped at INTAKE_MAX_MB; a piece that would go past it is refused (413).
- The staging folder holds only "<id>.part" + "<id>.json"; names from the phone are never used as paths.
- Unfinished uploads older than STALE_HOURS are cleared on the next start (no cron needed).
- Finishing hands the file to share._file()/_unzip_and_file(): the SAME classifier, grants, shelves and
  de-duplication as every other upload. This adds a door, not a second pipeline (same rule as share.py).

OUTCOME: see the T0884 Review line (sandbox test: 3 drops mid-file, resumed, sha256 equal).
"""
from __future__ import annotations

import asyncio
import errno
import fcntl
import hashlib
import json
import os
import pathlib
import secrets
import shutil
import tempfile
import time
import zipfile

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

router = APIRouter()

STAGE = pathlib.Path(os.environ.get("INTAKE_STAGE", "/srv/unsorted/.atomic-staging"))
MAX_MB = int(os.environ.get("INTAKE_MAX_MB", "2048"))
CHUNK = 4 * 1048576          # what the phone is told to send; the server accepts any piece size up to 16 MB
MAX_PIECE = 16 * 1048576
STALE_HOURS = 48
# T0925 (2026-11-19 audit A5/A11): files that could not be filed are KEPT here, never deleted; and a start
# is refused while free space would drop under this reserve (a full disk used to eat the upload).
FAILED = pathlib.Path(os.environ.get("INTAKE_FAILED", str(STAGE.parent / ".failed")))
RESERVE = int(os.environ.get("INTAKE_RESERVE_MB", "1024")) * 1048576
LOCK_STALE = 3600            # a finish lock older than this is from a crashed/restarted container
MAX_OPEN = int(os.environ.get("INTAKE_MAX_OPEN", "20"))   # unfinished uploads one person may hold at once


def _write_atomic(path: pathlib.Path, text: str) -> None:
    """Write via a temp file + rename, so a crash never leaves half a JSON file.

    T0931 (2026-11-20 re-audit): meta and result files were written in place; a crash mid-write made
    json.loads fail on every retry (500 forever for that upload).
    """
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def keep_failed(folder: pathlib.Path, email: str, name: str, results: list, dest: str | None = None) -> bool:
    """Move whatever is still in `folder` to FAILED/<dest> with a WHY.txt; True if anything was kept.

    T0931 (re-audit A5): /up/finish already kept unfiled bytes, but /share and /intake still deleted them in
    their `finally`. All three doors now call this before cleaning up.
    """
    if not folder.is_dir() or not any(p.is_file() for p in folder.rglob("*")):
        return False
    keep = FAILED / (dest or f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}")
    keep.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(folder), keep)
    (keep / "WHY.txt").write_text(json.dumps({"email": email, "name": name, "results": results}, indent=1))
    return True


def _clear_finish_locks() -> None:
    """Remove finish locks left by a restart. One uvicorn process, so no finish can be running at import.

    T0931 (re-audit): a restart mid-finish left <uid>.finishing behind and the phone was told "working"
    for up to LOCK_STALE (1 h).
    """
    try:
        for p in STAGE.glob("*.finishing"):
            p.unlink(missing_ok=True)
    except OSError:
        pass


_clear_finish_locks()


async def _flock(fd: int, kind: int, wait: float = 30.0) -> bool:
    """Take an flock without blocking the event loop (retry every 50 ms); False after `wait` seconds."""
    end = time.monotonic() + wait
    while True:
        try:
            fcntl.flock(fd, kind | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() > end:
                return False
            await asyncio.sleep(0.05)


def _who(x_auth_email: str | None) -> str:
    email = (x_auth_email or "").strip().lower()
    if not email:
        raise HTTPException(401, "no identity from the gate")
    return email


def _paths(uid: str) -> tuple[pathlib.Path, pathlib.Path]:
    if not uid.replace("-", "").replace("_", "").isalnum() or len(uid) > 40:
        raise HTTPException(404, "no such upload")
    return STAGE / f"{uid}.part", STAGE / f"{uid}.json"


def _load(uid: str, email: str) -> tuple[dict, pathlib.Path, pathlib.Path]:
    part, meta_p = _paths(uid)
    try:
        meta = json.loads(meta_p.read_text())
    except (OSError, ValueError):
        raise HTTPException(404, "no such upload")
    if meta.get("email") != email:      # same answer as "missing": do not confirm someone else's id exists
        raise HTTPException(404, "no such upload")
    return meta, part, meta_p


def _extra(uid: str) -> tuple[pathlib.Path, pathlib.Path, pathlib.Path]:
    """Result file, finish lock and work folder of one upload (all inside STAGE, same disk as the .part)."""
    return STAGE / f"{uid}.result.json", STAGE / f"{uid}.finishing", STAGE / f"{uid}.work"


def _sweep() -> None:
    """Drop uploads nobody came back for (48 h), including their kept results and empty work folders."""
    cutoff = time.time() - STALE_HOURS * 3600
    for p in STAGE.glob("*.json"):
        if p.name.endswith(".result.json"):
            continue
        try:
            if p.stat().st_mtime < cutoff:
                uid = p.stem
                res_p, lock_p, work = _extra(uid)
                p.with_suffix(".part").unlink(missing_ok=True)
                _got(uid).unlink(missing_ok=True)
                res_p.unlink(missing_ok=True)
                lock_p.unlink(missing_ok=True)
                if work.is_dir() and not any(work.rglob("*")):
                    work.rmdir()
                p.unlink(missing_ok=True)
        except OSError:
            pass


@router.post("/up/start")
async def start(request: Request, x_auth_email: str | None = Header(default=None)):
    email = _who(x_auth_email)
    body = await request.json()
    name = pathlib.PurePosixPath(str(body.get("name") or "shared")).name[:200] or "shared"
    if name in (".", ".."):              # A10: PurePosixPath('..').name == '..' -> target was the folder itself
        name = "shared"
    size = int(body.get("size") or 0)
    if size < 0 or size > MAX_MB * 1048576:
        raise HTTPException(413, f"This file is bigger than the {MAX_MB / 1024:g} GB limit.")
    hint = body.get("hint") if body.get("hint") in ("video",) else None
    shelf = body.get("shelf") if body.get("shelf") in ("general", "private") else None
    STAGE.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(STAGE).free < size + RESERVE:   # A11: refuse up front, not after 2 GB arrived
        raise HTTPException(507, "The server is short of space. Try again later.")
    _sweep()
    if _open_count(email) >= MAX_OPEN:   # T0931: one person cannot fill the disk with half-sent uploads
        raise HTTPException(429, "Too many unfinished uploads. Let some finish, then try again.")
    uid = secrets.token_urlsafe(16)
    part, meta_p = _paths(uid)
    parallel = bool(body.get("parallel"))
    chunk = (BIG_CHUNK if size >= BIG_FILE else CHUNK) if parallel else CHUNK
    if parallel:
        # T0931 (re-audit A11): truncate() made a SPARSE file that reserved nothing, so several big starts all
        # passed the free-space check and the disk filled mid-upload (ENOSPC -> 500, upload lost). fallocate
        # reserves the blocks now, so a full disk is refused here with 507. Falls back to sparse only where
        # the file system cannot allocate (EOPNOTSUPP/EINVAL).
        fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        try:
            if size:
                os.posix_fallocate(fd, 0, size)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                os.close(fd)
                part.unlink(missing_ok=True)
                raise HTTPException(507, "The server is short of space. Try again later.")
            if e.errno not in (errno.EOPNOTSUPP, errno.EINVAL):
                os.close(fd)
                part.unlink(missing_ok=True)
                raise
            os.ftruncate(fd, size)
        os.close(fd)
        _got(uid).touch()
    else:
        part.touch()
    _write_atomic(meta_p, json.dumps({"email": email, "name": name, "size": size, "started": time.time(),
                                      "hint": hint, "shelf": shelf, "parallel": parallel, "chunk": chunk}))
    return {"id": uid, "offset": 0, "chunk": chunk, "parallel": parallel}


# ---------------------------------------------------------------------------------------------------------
#  PARALLEL PIECES, ANY ORDER (T0928, 2026-11-19). Jason: "can be [sent] out of order and rebuild in order,
#  but in larger sizes". When /up/start is asked for {"parallel": true}, the .part file is made at its full
#  size at once and each piece n is written straight into its place (n * chunk), so pieces may arrive in any
#  order, several at a time. <uid>.got lists the piece numbers that arrived whole and checksum-clean; a
#  piece cut off mid-way is simply not listed and gets sent again. Pieces are 16 MB for files of 256 MB and
#  up (4 MB below). The old one-after-another PUT /up/{id}?offset= stays for pages that do not ask.
#  Free alternative considered: tus (tusd + tus-js-client) does parallel parts but has no per-piece
#  checksum in the browser client, so this stays our own (see T0928).
# ---------------------------------------------------------------------------------------------------------
BIG_CHUNK = 16 * 1048576
BIG_FILE = 256 * 1048576


def _got(uid: str) -> pathlib.Path:
    return STAGE / f"{uid}.got"


def _open_count(email: str) -> int:
    """Unfinished uploads (a .part still waiting) that belong to this person."""
    n = 0
    for p in STAGE.glob("*.json"):
        if p.name.endswith(".result.json") or not p.with_suffix(".part").exists():
            continue
        try:
            n += json.loads(p.read_text()).get("email") == email
        except (OSError, ValueError):
            pass
    return n


def _pieces(meta: dict) -> int:
    return max(1, -(-meta["size"] // meta["chunk"]))


def _have(uid: str) -> set[int]:
    try:
        return {int(x) for x in _got(uid).read_text().split()}
    except (OSError, ValueError):
        return set()


@router.get("/up/{uid}")
def status(uid: str, x_auth_email: str | None = Header(default=None)):
    meta, part, _ = _load(uid, _who(x_auth_email))
    if not part.exists():                # finished or finishing: all bytes are here, the phone should ask /finish
        return {"id": uid, "offset": meta["size"], "size": meta["size"], "missing": []}
    if meta.get("parallel"):
        have, n, c = _have(uid), _pieces(meta), meta["chunk"]
        missing = [i for i in range(n) if i not in have]
        got_bytes = sum(min(c, meta["size"] - i * c) for i in have if i < n)
        return {"id": uid, "offset": got_bytes, "size": meta["size"], "chunk": c, "missing": missing}
    return {"id": uid, "offset": part.stat().st_size, "size": meta["size"]}


@router.put("/up/{uid}/piece/{n}")
async def piece_at(uid: str, n: int, request: Request, x_auth_email: str | None = Header(default=None)):
    """Write piece n into its place. Any order; a piece already in is acknowledged and not written again.

    T0931 (2026-11-20 re-audit, finding 1): bytes used to be written straight into the file and only then
    checked, so a damaged or cut-short RE-SEND of a piece that had already arrived whole overwrote good data
    while the piece stayed listed as in -> a corrupt file was filed. Now the piece (16 MB at most) is held in
    memory, its length and checksum are checked, and only a clean piece is written, in one pwrite.
    Finding 2: a piece still being written while /finish renamed the file kept writing into the filed file.
    Now writers hold a shared flock on the .part and finish takes an exclusive one after creating its lock
    file, so a write is either fully done before finish looks, or refused (409) after.
    """
    meta, part, meta_p = _load(uid, _who(x_auth_email))
    if not meta.get("parallel") or not part.exists():
        raise HTTPException(409, "this upload does not take numbered pieces")
    c = meta["chunk"]
    if n < 0 or n >= _pieces(meta):
        raise HTTPException(416, "no such piece")
    expect = min(c, meta["size"] - n * c)
    want = (request.headers.get("x-piece-sha256") or "").strip().lower()
    buf = bytearray()
    async for data in request.stream():
        if len(buf) + len(data) > expect:
            raise HTTPException(413, "piece longer than its place")
        buf += data
    if len(buf) != expect:               # cut off: not listed, the page sends it again
        return JSONResponse({"piece": n, "error": "This piece was cut short. Sending it again."}, status_code=400)
    if want and hashlib.sha256(buf).hexdigest() != want:
        return JSONResponse({"piece": n, "checksum": True}, status_code=422)
    if n in _have(uid):                  # a retry of a piece that is already in whole: keep the good copy
        os.utime(meta_p)
        return {"piece": n, "already": True}
    _, lock_p, _ = _extra(uid)
    try:
        fd = os.open(part, os.O_WRONLY)
    except FileNotFoundError:            # finish already took the file
        return JSONResponse({"piece": n, "working": True}, status_code=409)
    try:
        if not await _flock(fd, fcntl.LOCK_SH):
            return JSONResponse({"piece": n, "working": True}, status_code=409)
        if lock_p.exists():              # finish has started: never write into a file being filed
            return JSONResponse({"piece": n, "working": True}, status_code=409)
        try:
            os.pwrite(fd, bytes(buf), n * c)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise HTTPException(507, "The server is short of space. Try again later.")
            raise
        with open(_got(uid), "a") as g:  # O_APPEND: whole short lines, safe with several pieces at once
            g.write(f"{n}\n")
    finally:
        os.close(fd)                     # closing drops the flock
    os.utime(meta_p)
    return {"piece": n}


@router.put("/up/{uid}")
async def piece(uid: str, offset: int, request: Request, x_auth_email: str | None = Header(default=None)):
    meta, part, meta_p = _load(uid, _who(x_auth_email))
    have = part.stat().st_size
    if offset != have:                   # a piece re-sent after a drop, or one that got ahead: say where we are
        return JSONResponse({"offset": have}, status_code=409)
    want = (request.headers.get("x-piece-sha256") or "").strip().lower()
    h = hashlib.sha256() if want else None
    written = 0
    with part.open("ab") as out:
        async for data in request.stream():
            written += len(data)
            if written > MAX_PIECE or have + written > meta["size"]:
                out.truncate(have)       # never keep half of a refused piece
                raise HTTPException(413, "piece too big or past the declared size")
            out.write(data)
            if h:
                h.update(data)
        if h and h.hexdigest() != want:  # damaged on the way: drop it, the page sends it again
            out.truncate(have)
            return JSONResponse({"offset": have, "checksum": True}, status_code=422)
    os.utime(meta_p)                     # keeps an active upload out of the stale sweep
    return {"offset": have + written}


@router.post("/up/{uid}/finish")
async def finish(uid: str, x_auth_email: str | None = Header(default=None)):
    """File a complete upload. Safe to call again: a retry gets the stored answer, never a second filing.

    T0925 (2026-11-19 audit A4/A5): before, the meta was deleted FIRST and filing ran in /tmp; a phone that
    timed out (120 s) and retried got 404 and re-sent the whole file, and any filing error rmtree'd the
    bytes. Now: a lock stops a second finish running at the same time (it answers 409 "working" and the
    phone waits), the answer is kept as <uid>.result.json for retries, the meta stays until the 48 h sweep,
    and anything not filed is moved to FAILED with a note instead of being deleted.
    """
    email = _who(x_auth_email)
    meta, part, meta_p = _load(uid, email)
    res_p, lock_p, work = _extra(uid)
    if res_p.exists():
        return JSONResponse(json.loads(res_p.read_text()))
    try:
        os.close(os.open(lock_p, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except FileExistsError:
        if time.time() - lock_p.stat().st_mtime < LOCK_STALE:
            return JSONResponse({"offset": meta["size"], "working": True}, status_code=409)
        lock_p.touch()                   # left by a restart mid-finish: take it over
    name = meta["name"]
    work.mkdir(exist_ok=True)
    target = work / name
    if part.exists() and meta.get("parallel"):          # T0928: every numbered piece must be in
        # T0931: wait for pieces already being written (they hold LOCK_SH); new ones see lock_p and stop.
        pfd = os.open(part, os.O_RDONLY)
        try:
            if not await _flock(pfd, fcntl.LOCK_EX):
                lock_p.unlink(missing_ok=True)
                return JSONResponse({"offset": meta["size"], "working": True}, status_code=409)
            missing = [i for i in range(_pieces(meta)) if i not in _have(uid)]
            if missing:
                lock_p.unlink(missing_ok=True)
                return JSONResponse({"offset": 0, "missing": missing[:50], "error": "not all pieces arrived yet"},
                                    status_code=409)
            part.rename(target)          # still holding LOCK_EX: no piece can land after this
        finally:
            os.close(pfd)
    if part.exists():
        have = part.stat().st_size
        if have != meta["size"]:
            lock_p.unlink(missing_ok=True)
            return JSONResponse({"offset": have, "error": "not all bytes arrived yet"}, status_code=409)
        part.rename(target)              # same disk: a rename, not a 2 GB copy into /tmp
    elif not target.exists():
        lock_p.unlink(missing_ok=True)
        raise HTTPException(404, "no such upload")
    import share  # late import: share imports app, app includes this router
    hint, shelf = meta.get("hint"), meta.get("shelf")
    try:
        if (hint is None and name.lower().endswith(".zip") and zipfile.is_zipfile(target)
                and not share._is_comic_zip(target)):
            results = await share._unzip_and_file(target, email, work)
        else:
            results = [await share._file(target, name, email, hint, shelf)]
    except Exception as e:  # noqa: BLE001  -- any failure: keep the bytes, say so
        # T0931 (U14): the raw exception (paths, internals) went to the phone; it now goes to WHY.txt only.
        results = [{"name": name, "status": "error", "why": "This file could not be filed.",
                    "detail": f"{type(e).__name__}: {e}"}]
    if any(r.get("status") == "error" for r in results) and keep_failed(work, email, name, results, uid):
        results.append({"name": name, "status": "kept", "why": "not filed; kept safe on the server for Jason"})
    else:
        shutil.rmtree(work, ignore_errors=True)
    results = [{k: v for k, v in r.items() if k != "detail"} for r in results]   # internals stay server-side
    _write_atomic(res_p, json.dumps(results))
    lock_p.unlink(missing_ok=True)
    return JSONResponse(results)


@router.post("/up/{uid}/cancel")
def cancel(uid: str, x_auth_email: str | None = Header(default=None)):
    _, part, meta_p = _load(uid, _who(x_auth_email))
    part.unlink(missing_ok=True)
    _got(uid).unlink(missing_ok=True)
    meta_p.unlink(missing_ok=True)
    return {"ok": True}
