#!/usr/bin/env python3
"""patch_races.py - race-condition review 2026-11-23 (D:\\overnight\\2026-11-23\\review\\races.md), on a COPY of intake.

    python3 patch_races.py COPY_OF_INTAKE

Jason, 11-23: "Eliminate race conditions and make the code run with best practices". Fixed:
  R1 place(): ONE global lock was held while it listed the folder and hashed same-size files over NAS shares, so a
     slow share stopped all filing. Now one lock PER destination folder (thread lock + flock file named from the
     folder), each waited for at most PLACE_WAIT s (120), then TimeoutError (the caller keeps the file).
  R2 place(): an error after the bytes moved to the hidden .part left them orphaned in the library folder and
     keep_failed kept nothing. Now any error moves the .part back to `tmp` (the caller keeps it), and a file that
     vanishes or cannot be read during the duplicate scan (Paperless consuming it) is skipped, not fatal.
  R3 cancel could delete a file mid-finish and an unexpected error in _finish left the finish lock for an hour.
     cancel is async now and answers 409 while the upload is finishing or a piece is being written; the finish
     wrapper removes the lock if _finish raised.
  R4 the old sequential PUT and finish did not exclude each other: each now answers 409 while the other runs.
  R5 notes: a file literally named "x.pdf.intake.json" and the note for x.pdf could overwrite each other. safe_name
     turns a trailing ".intake.json" into ".intake-json", and notes are opened exclusively (write_sidecar); a name
     already taken gets a short random suffix instead of overwriting.
  R6 log rotation from two threads or two processes could drop a log generation: rotate+append now run under a
     thread lock and a flock on intake.log.lock.
  R8 healthz: a fixed probe name made two overlapping checks report a false 503; the probe name is random.
  R9 resuming an upload now refreshes its meta time, so the hourly sweep cannot delete it between resume and piece.
  S1 /share: files in one share (two scan.pdf, or two zips' z0-... members) shared one temp folder and could
     overwrite each other; each file gets its own numbered sub-folder.
Left on the to-do list: R7 (a reservation counter for the free-space check), R10 (skip releasing a held file that
was re-sent meanwhile).
Refuses the live folder, an already patched copy, or any old text not found exactly once.
Variables: folder, files (name -> text), swap() (exact-once replace).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
files = {n: (folder / n).read_text(encoding="utf-8") for n in ("app.py", "resumable.py", "share.py", "rescan_held.py")}
if "def write_sidecar" in files["app.py"]:
    raise SystemExit("already patched")


def swap(name, old, new):
    if files[name].count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:70]!r}, found {files[name].count(old)}")
    files[name] = files[name].replace(old, new)


# ---------------------------------------------------------------- R1 + R2 place() --------------------------------
swap("app.py", '''_PLACE_LOCK = threading.Lock()
''', '''_PLACE_LOCK = threading.Lock()          # guards _FOLDER_LOCKS only (race review 11-23 R1)
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
''')
swap("app.py", '''    import fcntl
    import resumable
    resumable.STAGE.mkdir(parents=True, exist_ok=True)
    with _PLACE_LOCK, open(resumable.STAGE / ".place.lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        try:
            # Already here, under any name? The checksum decides, so a volume
            # re-sent from a phone does not land twice.
            size = part.stat().st_size
            for existing in dest_dir.iterdir():
                if (existing.is_file() and existing != part and not existing.name.endswith(".part")
                        and existing.stat().st_size == size and sha256(existing) == digest):
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
        finally:
            fcntl.flock(lk, fcntl.LOCK_UN)''', '''    try:
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
        raise''')

# ---------------------------------------------------------------- R5 notes ---------------------------------------
swap("app.py", '''def sidecar_for(filed: str | pathlib.Path) -> pathlib.Path:''', '''def write_sidecar(filed: str | pathlib.Path, data: dict) -> pathlib.Path:
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


def sidecar_for(filed: str | pathlib.Path) -> pathlib.Path:''')
swap("app.py", '''            sidecar_for(res["path"]).write_text(json.dumps(
                {**verdict, **sidecar, "sha256": digest, "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                indent=2), encoding="utf-8")''', '''            write_sidecar(res["path"],     # R5: exclusive create, never overwrites another note
                          {**verdict, **sidecar, "sha256": digest, "at": time.strftime("%Y-%m-%d %H:%M:%S")})''')
swap("app.py", '''    name = re.sub(r'[<>:"|?*]', "_", name)
    return name[:200] or "unnamed"''', '''    name = re.sub(r'[<>:"|?*]', "_", name)
    if name.lower().endswith(".intake.json"):   # R5: an upload must not look like (or overwrite) a note
        name = name[:-len(".intake.json")] + ".intake-json"
    return name[:200] or "unnamed"''')
swap("rescan_held.py", '''        side = app.sidecar_for(res["path"])          # review 11-23 #13: same name rule as uploads
        side.write_text(json.dumps({**verdict, "source": "quarantine re-scan", "sha256": digest,
                                    "held_in": folder.name, "at": time.strftime("%F %T")}, indent=2))''',
     '''        app.write_sidecar(res["path"], {**verdict, "source": "quarantine re-scan", "sha256": digest,   # R5
                                        "held_in": folder.name, "at": time.strftime("%F %T")})''')

# ---------------------------------------------------------------- R6 log -----------------------------------------
swap("app.py", '''def say(msg: str) -> None:
    """Print one line and append it to intake.log. Never put keys, tokens or file contents in `msg`."""
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        _rotate_log()
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\\n")''', '''_LOG_LOCK = threading.Lock()


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
                    f.write(line + "\\n")
            finally:
                fcntl.flock(lk, fcntl.LOCK_UN)''')

# ---------------------------------------------------------------- R8 healthz ---------------------------------------
swap("app.py", '''        probe = resumable.STAGE / ".healthz"''',
     '''        import secrets as _secrets
        probe = resumable.STAGE / f".healthz-{_secrets.token_hex(4)}"   # R8: overlapping checks must not collide''')

# ---------------------------------------------------------------- R3 R4 R9 resumable -------------------------------
swap("resumable.py", '''                _say(f"start resumed uid={mp.stem} size={size}")''',
     '''                try:
                    os.utime(mp)         # R9: a resumed upload is active again; keep it out of the hourly sweep
                except OSError:
                    pass
                _say(f"start resumed uid={mp.stem} size={size}")''')
swap("resumable.py", '''    meta, part, meta_p = _load(uid, _who(x_auth_email))
    if uid in _SEQ_BUSY:''', '''    meta, part, meta_p = _load(uid, _who(x_auth_email))
    if uid in _SEQ_BUSY or uid in _FINISHING:     # R4: never append while a finish is filing this upload''')
swap("resumable.py", '''    meta, _, _ = _load(uid, _who(x_auth_email))
    if uid in _FINISHING:
        return JSONResponse({"offset": meta["size"], "working": True}, status_code=409)
    _FINISHING.add(uid)
    try:
        return await _finish(uid, x_auth_email)
    finally:
        _FINISHING.discard(uid)''', '''    meta, _, _ = _load(uid, _who(x_auth_email))
    if uid in _FINISHING or uid in _SEQ_BUSY:      # R4: not while an old-style piece is still being written
        return JSONResponse({"offset": meta["size"], "working": True}, status_code=409)
    _FINISHING.add(uid)
    try:
        return await _finish(uid, x_auth_email)
    except BaseException:
        _extra(uid)[1].unlink(missing_ok=True)     # R3: an unexpected error must not leave the lock for an hour
        raise
    finally:
        _FINISHING.discard(uid)''')
swap("resumable.py", '''@router.post("/up/{uid}/cancel")
def cancel(uid: str, x_auth_email: str | None = Header(default=None)):
    _, part, meta_p = _load(uid, _who(x_auth_email))''', '''@router.post("/up/{uid}/cancel")
async def cancel(uid: str, x_auth_email: str | None = Header(default=None)):
    """Drop an unfinished upload. R3 (race review 11-23): async now (it ran in the thread pool beside the event
    loop) and refused with 409 while the upload is being finished or a piece is being written."""
    _, part, meta_p = _load(uid, _who(x_auth_email))
    if uid in _FINISHING or uid in _SEQ_BUSY or _extra(uid)[1].exists():
        return JSONResponse({"error": "this upload is being filed right now"}, status_code=409)''')

# ---------------------------------------------------------------- S1 /share temp names ------------------------------
swap("share.py", '''            p = _in_tmp(tmp, name)
            with p.open("wb") as out:''', '''            sub = tmp / str(len(list(tmp.iterdir())))   # S1: one sub-folder per shared file (no name collisions)
            sub.mkdir()
            p = _in_tmp(sub, name)
            with p.open("wb") as out:''')
swap("share.py", '''                results += await _unzip_and_file(p, email, tmp, details)''',
     '''                results += await _unzip_and_file(p, email, sub, details)''')

for name, text in files.items():
    compile(text, name, "exec")
    (folder / name).write_text(text, encoding="utf-8")
print("patched: app.py, resumable.py, share.py, rescan_held.py (race review 11-23 R1-R6 R8 R9 S1)")
