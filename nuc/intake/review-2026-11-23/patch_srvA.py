#!/usr/bin/env python3
"""patch_srvA.py - Novashare backend review 2026-11-23, batch A, applied to a COPY of intake.

    python3 patch_srvA.py COPY_OF_INTAKE

Findings fixed (D:\\overnight\\2026-11-23\\review\\backend.md; Jason approved all high+medium, 11-23):
  #1  place(): two same-name files filed at the same moment shared "<name>.part"; one overwrote the other.
      Now: the bytes move to a UNIQUE part name first, then the duplicate check + target choice + rename run
      under one lock (a thread lock plus flock on STAGE/.place.lock, so the hourly rescan_held process is
      covered too). A taken name gets the digest suffix, then -2, -3 ...
  #3  /upload deleted the bytes on any error; now keep_failed() like /intake, /share and /up/finish.
  #4  place() onto a NAS share that is not mounted built the folder on the local disk. Now a missing
      INTAKE_SENTINELS file for the dest's share raises (the caller keeps the file in .failed).
  #7  INTAKE_TOKEN is set live (checked 11-23), so an empty one is now a start-up error like INTAKE_KEY.
  #9  finish lock taken over by AGE (1 h): an in-memory set of uids being finished answers 409 while the
      first finish still runs, however long it takes. The file lock stays for restarts.
  #11 classify: a 200 reply with "content": null raised TypeError outside the try ("NEVER RAISES" broke).
  #13 unsorted sidecar was with_suffix(".intake.json"): bill.pdf and bill.docx shared one note. Now
      "<full name>.intake.json" (sidecar_for), not written for a duplicate; rescan_held uses the same helper.
  #14 intake.log carried emails ("level check failed for <email>", "drop from <email>", held why). Person id now.
Refuses the live folder, an already patched copy, or any old text not found exactly once.
Variables: folder, files (name -> text), swap() (exact-once replace).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
files = {n: (folder / n).read_text(encoding="utf-8") for n in ("app.py", "resumable.py", "classify.py", "rescan_held.py")}
if "def sidecar_for" in files["app.py"]:
    raise SystemExit("already patched")


def swap(name, old, new):
    if files[name].count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:70]!r}, found {files[name].count(old)}")
    files[name] = files[name].replace(old, new)


# ---- #7 token required -------------------------------------------------------------------------------------
swap("app.py", '''if not DROP_KEY:
    raise RuntimeError("INTAKE_KEY is not set: refusing to start (T0931 item 7). Set it in compose.")
''', '''if not DROP_KEY:
    raise RuntimeError("INTAKE_KEY is not set: refusing to start (T0931 item 7). Set it in compose.")
# Review 11-23 #7: with no token, any signed-in "full" user could POST /intake with someone else's sender id.
# n8n sends the token (live since T0931 item 10), so an empty one now stops start-up like INTAKE_KEY.
if not INTAKE_TOKEN:
    raise RuntimeError("INTAKE_TOKEN is not set: refusing to start (review 2026-11-23 #7). Set it in compose.")
''')

# ---- #1 + #4 place() ---------------------------------------------------------------------------------------
swap("app.py", '''def place(tmp: pathlib.Path, dest_dir: pathlib.Path, name: str,
          digest: str, meta: dict) -> dict:
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Already here, under any name? The checksum decides, so a volume
    # re-sent from a phone does not land twice.
    for existing in dest_dir.iterdir():
        if (existing.is_file() and existing.stat().st_size == tmp.stat().st_size
                and sha256(existing) == digest):
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
''', '''_PLACE_LOCK = threading.Lock()


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
    import fcntl
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
            fcntl.flock(lk, fcntl.LOCK_UN)
    return {"status": "filed", "path": str(target)}


def sidecar_for(filed: str | pathlib.Path) -> pathlib.Path:
    """The .intake.json note next to an unsorted file: '<full name>.intake.json' (review 11-23 #13; was
    with_suffix, so bill.pdf and bill.docx shared bill.intake.json and the second overwrote the first)."""
    p = pathlib.Path(filed)
    return p.with_name(p.name + ".intake.json")
''')
swap("app.py", "import tempfile\n", "import tempfile\nimport threading\n")

# ---- #13 sidecar -------------------------------------------------------------------------------------------
swap("app.py", '''        res = place(tmp, UNSORTED, name, digest, verdict)
        side = pathlib.Path(res["path"]).with_suffix(".intake.json")
        side.write_text(json.dumps(
            {**verdict, **sidecar, "sha256": digest, "at": time.strftime("%Y-%m-%d %H:%M:%S")},
            indent=2), encoding="utf-8")''', '''        res = place(tmp, UNSORTED, name, digest, verdict)
        if res["status"] == "filed":     # review 11-23 #13: a duplicate keeps the original's note
            sidecar_for(res["path"]).write_text(json.dumps(
                {**verdict, **sidecar, "sha256": digest, "at": time.strftime("%Y-%m-%d %H:%M:%S")},
                indent=2), encoding="utf-8")''')
swap("rescan_held.py", '''        side = pathlib.Path(res["path"]).with_suffix(".intake.json")''',
     '''        side = app.sidecar_for(res["path"])          # review 11-23 #13: same name rule as uploads''')

# ---- #3 /upload keeps the bytes ----------------------------------------------------------------------------
swap("app.py", '''        # A3: the blocking work (hash, model, grant lookup, move) runs in a worker thread.
        return JSONResponse(await run_in_threadpool(route_file, tmp, name, size, email, hint, shelf))
    finally:''', '''        # A3: the blocking work (hash, model, grant lookup, move) runs in a worker thread.
        return JSONResponse(await run_in_threadpool(route_file, tmp, name, size, email, hint, shelf))
    except HTTPException:
        raise                            # refused (too big, held, not allowed): handled, nothing to keep here
    except Exception as e:
        # Review 11-23 #3: this door had no keep_failed, so a NAS hiccup or a bug deleted the bytes in `finally`.
        from resumable import keep_failed
        keep_failed(tmp_dir, email, name, [{"name": name, "status": "error", "detail": f"{type(e).__name__}: {e}"}])
        say(f"KEPT drop file from {_person(email) or 'unmapped'} after {type(e).__name__}")
        raise
    finally:''')

# ---- #14 no emails in intake.log ---------------------------------------------------------------------------
swap("app.py", '''        say(f"level check failed for {email} on {app}: {type(e).__name__}")''',
     '''        say(f"level check failed for {_person(email) or 'unmapped'} on {app}: {type(e).__name__}")  # 11-23 #14''')
swap("app.py", '''            "why": f"uploaded by {who or email} to the {sub} library",''',
     '''            "why": f"uploaded by {who or 'unmapped'} to the {sub} library",   # 11-23 #14: no email in notes''')
swap("app.py", '''                       .format(email, lvl or "no", grant),''',
     '''                       .format(who or "unmapped", lvl or "no", grant),   # 11-23 #14''')
swap("app.py", '''    say(f"drop from {email}: {name} ({size/1048576:.1f}MB) "''',
     '''    say(f"drop from {who or 'unmapped'}: {name} ({size/1048576:.1f}MB) "   # 11-23 #14: person id, not email''')

# ---- #11 classify null content -----------------------------------------------------------------------------
swap("classify.py", '''                out = r.json()["choices"][0]["message"]["content"]''',
     '''                out = r.json()["choices"][0]["message"]["content"] or ""   # 11-23 #11: null content''')

# ---- #9 in-memory finishing set ----------------------------------------------------------------------------
swap("resumable.py", '''@router.post("/up/{uid}/finish")
async def finish(uid: str, x_auth_email: str | None = Header(default=None)):''', '''# Review 11-23 #9: uids whose finish is running in THIS process. The file lock was taken over by age (1 h), but a
# finish can run for hours (a big zip while the PC model sleeps); a second filing of the same work folder then
# raced the first. One uvicorn process serves every finish, so this set is the truth about "still running";
# the file lock remains for restarts (cleared at start-up, app._lifespan).
_FINISHING: set[str] = set()


@router.post("/up/{uid}/finish")
async def finish(uid: str, x_auth_email: str | None = Header(default=None)):
    """Answer 409 "working" while this uid is already being finished here (review 11-23 #9); else _finish."""
    meta, _, _ = _load(uid, _who(x_auth_email))
    if uid in _FINISHING:
        return JSONResponse({"offset": meta["size"], "working": True}, status_code=409)
    _FINISHING.add(uid)
    try:
        return await _finish(uid, x_auth_email)
    finally:
        _FINISHING.discard(uid)


async def _finish(uid: str, x_auth_email: str | None = Header(default=None)):''')

# ---- #7 follow-up: in-process tests set their own env; they now need a token too ------------------------------
for t in ("test_locks.py", "test_rescan.py", "test_t0927s3.py", "test_t0962b.py", "test_t0962.py"):
    files[t] = (folder / t).read_text(encoding="utf-8")
    swap(t, '"INTAKE_KEY": "k-test",', '"INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test",')
files["test_libraries.py"] = (folder / "test_libraries.py").read_text(encoding="utf-8")
swap("test_libraries.py", 'INTAKE_KEY="k-test")', 'INTAKE_KEY="k-test", INTAKE_TOKEN="t-test")')
# #13 follow-up: the two tests that read a sidecar use the new name rule (<full name>.intake.json)
files["test_rescan.py"] = files["test_rescan.py"].replace(
    'out[0].with_suffix(".intake.json")', 'out[0].with_name(out[0].name + ".intake.json")')
files["test_t0927s3.py"] = files["test_t0927s3.py"].replace(
    'pathlib.Path(r["path"]).with_suffix(".intake.json")', 'pathlib.Path(r["path"] + ".intake.json")')

for name, text in files.items():
    compile(text, name, "exec")
    (folder / name).write_text(text, encoding="utf-8")
print("patched: app.py, resumable.py, classify.py, rescan_held.py (review 11-23 #1 #3 #4 #7 #9 #11 #13 #14)")
