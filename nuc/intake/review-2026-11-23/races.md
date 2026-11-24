# Novashare intake: race / concurrency review (2026-11-23)

Code read: `D:\scratchpad\nuc\intake\` app.py, resumable.py, share.py, classify.py, scan.py, rescan_held.py,
link_probe.py, link_route.py, dropconfig.py (plus atomic_ui.py, to see how the page really calls the server).
Read-only. Today's fixes (unique .part + _PLACE_LOCK/flock, _FINISHING, _SEQ_BUSY, lifespan-only lock clearing,
bounded model semaphore) were checked and hold up. I found no new race in those fixes. The issues below are
what is left. Each one comes with an interleaving I could trace in the code.

Facts the findings rely on: `cancel`, `status` and `healthz` are plain `def` routes, so they run in the
anyio thread pool, in parallel with the event loop. The page (atomic_ui.py:195) always asks for
`parallel: true`, so the sequential PUT path is only used by old cached pages or direct calls.

## Table

| # | Where | Race | Sev |
|---|-------|------|-----|
| 1 | app.py:362-384 `place()` | One global lock (`_PLACE_LOCK` + one flock) is held across NAS I/O: `iterdir`, a `stat` of every entry and a full `sha256` of every same-size file. A slow or hung NFS/CIFS share therefore stops ALL filing, local unsorted included, and piles up pool threads. | medium |
| 2 | app.py:358-382 `place()` | The bytes are moved into dest_dir as a hidden `.part` BEFORE the locked section. Any exception after that leaves an orphan `.part` that nobody tracks. One trigger is another process deleting a file during the duplicate scan (Paperless drains /srv/consume). The caller's `keep_failed` then finds an empty folder, keeps nothing, and the person is told "could not be filed". | medium |
| 3 | resumable.py:536-628 `_finish`, 631-638 `cancel` | `cancel` (thread) ignores `_FINISHING` and the lock. It can unlink `.part` between `part.exists()` and `os.open`/`rename`, which gives an uncaught FileNotFoundError, a 500 and a `.finishing` lock left behind (the phone sees "working" for 1 h). It can also delete the meta while filing runs: the retry gets 404 and `<uid>.result.json` is orphaned for good, because `_sweep` skips result files. Any unexpected exception in `_finish` leaves the lock the same way. | low-medium |
| 4 | resumable.py:479-513 vs 596-601 | Sequential PUT and finish do not exclude each other (`_SEQ_BUSY` and `_FINISHING` are separate sets). The last chunk takes the file to its full size. While `request.stream()` awaits the final empty receive, `/finish` renames the `.part` and starts filing. The piece then resumes, and on a checksum mismatch or overflow it calls `out.truncate(have)` on the inode that is being filed. | low (old pages only) |
| 5 | app.py:517-520, rescan_held.py:81-84 | The sidecar `<name>.intake.json` is written after `place()` returns, outside the lock, with `write_text` (no O_EXCL). A file uploaded under the literal name `bill.pdf.intake.json` and a concurrent (or later) `bill.pdf` clobber each other: `rename` replaces the sidecar, or `write_text` replaces the uploaded file. | low |
| 6 | app.py:264-278 `_rotate_log` | Two threads, or the server plus rescan_held, both see size >= max and both rotate. The second rotation shifts the first one's result again, so one 5 MB generation (`.2` -> `.3`) is silently lost and `.1` can end up holding a single line. | low |
| 7 | app.py:236-242, resumable.py:278 | Free-space check without a reservation. Two concurrent multipart uploads (/upload, /intake, /share) each pass `free >= declared + RESERVE`, then both write and hit ENOSPC. Only the parallel /up/start reserves space (fallocate). | low |
| 8 | app.py:405-409 `healthz` | Fixed probe name `STAGE/.healthz`. Two overlapping health calls (pool threads) run: A writes, B writes, A unlinks, then B's unlink raises FileNotFoundError. The result is a false "staging not writable" 503. | low |
| 9 | resumable.py:289-300 vs 183-201 | A resumed `/up/start` hands back an upload without refreshing the meta mtime. If the upload is just under 48 h old, the hourly `_sweep` thread can delete .part/.got/meta between the resume and the first piece. The phone then gets 404 and shows "Not saved". | low |
| 10 | rescan_held.py:76-80 | A file is held because the scanner was down. The phone shows an error, the person sends the file again, and it is filed to its library. An hour later rescan releases the held copy into unsorted. The duplicate check looks only inside UNSORTED, so a second copy lands there. | low |

### Checked, no bug found
- `_FINISHING` and `_SEQ_BUSY`: every check-then-add runs on the event loop with no await in between, so they are correct in one process.
- The parallel piece path (piece_at) against finish: lock_p is created before the EX flock, and writers check lock_p after taking SH. A writer whose fd was opened before the rename gets SH only after finish closes `pfd`, sees lock_p and answers 409. Duplicate `.got` lines are harmless (it is read as a set).
- The /up/start idempotent resume and the count and byte caps: the body after `await request.json()` has no await, so it is atomic in-process.
- `scan.hold` folder naming: the `mkdir()` loop with FileExistsError is atomic across threads and processes.
- rescan_held against the live server: `place()` is covered by the flock, the rescan lock is NB, and a half-made quarantine folder (no WHY.txt yet) is skipped.
- `_PEER_CACHE`: only touched from the async middleware, on the loop thread, so no race. Side note: `getaddrinfo` blocks the loop, at most once every 2 s per unknown peer.
- `keep_failed` names: timestamp plus 32 random bits, or the uid, which is used only once because the result file is written after it. No collision found.
- Zip extraction: one `ZipFile` is used by one worker thread at a time, because the calls run in sequence.
- `.got` appends: O_APPEND short lines on local disk.
- dropconfig, link_probe, link_route, classify: no shared mutable state apart from `_MODEL_LOCK`, which was fixed today.

### Related, not concurrency (seen while tracing)
- share.py:432 and share.py:272: the names in one /share tmp folder are not unique. Two shared files both called `scan.pdf`, or two zips that both produce `z0-readme.txt`, collide. If the first one failed to file, it is still in tmp, and the second `open("wb")` overwrites it before `keep_failed` runs.
- resumable.py:500-506: a sequential piece cut off by a disconnect (ClientDisconnect) keeps its partial bytes without the checksum check. The "never keep half a piece" rule only covers 413 and 422.
- /upload and /intake need about twice the declared size at peak: Starlette's spooled multipart in TMPDIR plus the copy into tmp_dir. The gate checks the declared size once.
- Not verified: whether Starlette 1.3.1's BaseHTTPMiddleware cancels the endpoint when the client disconnects. If it does, a phone timing out at 120 s during `_finish` would leave the lock behind, which is the same symptom as #3. Fix #3's try/finally covers it.

## Fix sketches

### 1. Lock per destination folder, with a bounded wait
```python
_DIR_LOCKS: dict[str, threading.Lock] = {}
_DIR_LOCKS_GUARD = threading.Lock()

def _dir_lock(d: pathlib.Path) -> tuple[threading.Lock, pathlib.Path]:
    key = str(d.resolve())
    with _DIR_LOCKS_GUARD:
        lk = _DIR_LOCKS.setdefault(key, threading.Lock())
    return lk, resumable.STAGE / f".place.{hashlib.sha256(key.encode()).hexdigest()[:16]}.lock"

# in place():
tlock, lpath = _dir_lock(dest_dir)
if not tlock.acquire(timeout=120):
    raise RuntimeError("filing is busy")          # -> keep_failed path, see #2
try:
    with open(lpath, "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)            # rescan_held uses the same file name
        ...                                       # same body as today
finally:
    tlock.release()
```
A hung NAS share now blocks only filing into that share.

### 2. Never lose track of the bytes once they are moved
```python
shutil.move(str(tmp), str(part))
try:
    ...locked section...
except BaseException:
    try:
        shutil.move(str(part), str(tmp))          # put it back: the caller's keep_failed finds it
    except OSError:
        say(f"  ORPHAN left as {part.name}")      # last resort: at least it is logged
    raise
```
In the duplicate scan, skip entries that vanish or cannot be read:
```python
for existing in dest_dir.iterdir():
    try:
        if (existing.is_file() and ... and existing.stat().st_size == size and sha256(existing) == digest):
            ...
    except (FileNotFoundError, PermissionError):
        continue
```

### 3. cancel respects finish; _finish always drops its lock
```python
@router.post("/up/{uid}/cancel")
async def cancel(uid, x_auth_email=Header(default=None)):   # async: runs on the loop, atomic vs _FINISHING
    _, part, meta_p = _load(uid, _who(x_auth_email))
    if uid in _FINISHING or _extra(uid)[1].exists():
        return JSONResponse({"working": True}, status_code=409)
    ...unchanged...
```
In `finish()`, wrap the call: `try: return await _finish(...) except Exception: lock_p.unlink(missing_ok=True); raise`.
Better: use one `try/finally` around the body of `_finish` after the lock is created, which unlinks lock_p
unless a result was written.

### 4. Make sequential PUT and finish exclude each other
```python
# piece():
if uid in _SEQ_BUSY or uid in _FINISHING or _extra(uid)[1].exists():
    return JSONResponse({"offset": ..., "busy": True}, status_code=409)
# finish(), before _FINISHING.add:
if uid in _FINISHING or uid in _SEQ_BUSY:
    return JSONResponse({"offset": meta["size"], "working": True}, status_code=409)
```
All of these run on the loop with no await between the check and the add, so the sets are enough.

### 5. Reserve the sidecar name
In `safe_name`: `if name.lower().endswith(".intake.json"): name = name[:-5] + "_.json"`. Then write sidecars
with `open(path, "x")`. On FileExistsError, log the problem and skip the note. Never overwrite.

### 6. Serialize rotation
```python
_LOG_LOCK = threading.Lock()
def say(msg):
    ...
    with _LOG_LOCK, open(LOG.with_name(LOG.name + ".lock"), "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)            # covers rescan_held too
        _rotate_log()                             # re-stats under the lock
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
```
(`say` is called inside place's lock. The log lock is always the innermost lock, so there is no ordering problem.)

### 7. In-process reservation counter (gatekeeper runs on the loop, so a plain int is enough)
```python
_RESERVED = 0
# gatekeeper, for write paths with a declared length:
if free is not None and declared and free - _RESERVED < declared + resumable.RESERVE: -> 507
_RESERVED += declared
try:
    return await call_next(request)
finally:
    _RESERVED -= declared
```
For multipart, reserve about 2 x declared (see the "related" note above).

### 8. Unique probe name
`probe = resumable.STAGE / f".healthz.{secrets.token_hex(4)}"`. sweep_stale_files does not match `.healthz.*`.
Unlink it with `missing_ok=True`.

### 9. Refresh the mtime on resume
In the resume branch, call `os.utime(mp)` before returning. The 48 h clock then restarts when the phone comes back.

### 10. Check for the file elsewhere before releasing
Before `app.place(...)`, rescan_held can search unsorted and the libraries for the digest. That is expensive.
The cheaper option: have the live server record each filed digest (for example in a small
`STAGE/.filed-digests` file, appended under the place lock). rescan_held then marks a held copy whose digest
is already filed as `released_status="duplicate"` and does not place it. Leave it alone if a duplicate in
unsorted is acceptable.
