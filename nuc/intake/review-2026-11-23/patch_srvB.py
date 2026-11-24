#!/usr/bin/env python3
"""patch_srvB.py - Novashare backend review 2026-11-23, batch B, applied to a COPY of intake.

    python3 patch_srvB.py COPY_OF_INTAKE

Findings fixed (D:\\overnight\\2026-11-23\\review\\backend.md; Jason approved all high+medium on 11-23):
  #5  a file over the scan limit (95 MB) was filed unscanned whatever its type. Now only videos may skip the
      check. Any other big file is held in quarantine as "too-big" (422). rescan_held re-checks only "error",
      so it stays held for Jason.
  #6  the size limit ran after the whole body was on disk, and /share had none. Now the gatekeeper refuses a
      Content-Length over MAX_MB (413), or one the disk cannot take with the reserve (507), on /intake,
      /upload and /share before the body is read. The Caddy body cap for chunked bodies is in Jason's sudo batch.
  #10 waiting for the model held a pool thread up to 2 x 120 s while the PC slept, and enough waiting files
      starved every door. Now a file waits at most INTAKE_MODEL_WAIT s (30) for its turn, then goes to unsorted
      as "unknown:busy". The connect timeout is INTAKE_LLM_CONNECT s (5), so a sleeping PC fails in seconds.
  #12 videos skipped the write-grant check, so any drop user could add to Jellyfin. Jason chose a grant: kind
      "video" now needs INTAKE_VIDEO_GRANT ("jellyfin", known to the auth service; checked 11-23). Without
      "full" on it, the video is held in PENDING_ROOT/<person>/video like every other kind.
Refuses the live folder, an already patched copy, or any old text not found exactly once.
Variables: folder, files (name -> text), swap() (exact-once replace).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
files = {n: (folder / n).read_text(encoding="utf-8") for n in ("app.py", "classify.py")}
if "VIDEO_GRANT" in files["app.py"]:
    raise SystemExit("already patched")


def swap(name, old, new):
    if files[name].count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:70]!r}, found {files[name].count(old)}")
    files[name] = files[name].replace(old, new)


# ---- #5 big non-video files are held ----------------------------------------------------------------------
swap("app.py", '''    state, detail = scan.scan(tmp)
    if state in ("infected", "error"):''', '''    state, detail = scan.scan(tmp)
    # Review 11-23 #5: "skipped" (over the scan limit) is allowed for videos only. A 96 MB PDF or .docx (padding
    # is easy) went to Paperless or a shelf unchecked. Held as "too-big" (rescan_held re-checks only "error",
    # so it stays held for Jason to look at).
    if state == "skipped" and pathlib.PurePosixPath(name).suffix.lower() not in VIDEO_EXT:
        scan.hold(tmp, name, held_as, "too-big", detail, digest)
        say(f"HELD {door}: {name} -- too big for the virus check and not a video")
        raise HTTPException(422, "This file is too big for the virus check, so it was held for Jason to look at.")
    if state in ("infected", "error"):''')

# ---- #6 Content-Length at the gate --------------------------------------------------------------------------
swap("app.py", '''    write = request.method not in READ_METHODS
    level = (request.headers.get("x-auth-level") or "").strip().lower()''', '''    write = request.method not in READ_METHODS
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
    level = (request.headers.get("x-auth-level") or "").strip().lower()''')

# ---- #12 video grant ---------------------------------------------------------------------------------------
swap("app.py", '''VIDEO_EXT = {''', '''# Review 11-23 #12 (Jason: "yes, a jellyfin grant"): adding a video needs "full" on this auth-service app.
VIDEO_GRANT = os.environ.get("INTAKE_VIDEO_GRANT", "jellyfin").strip()
VIDEO_EXT = {''')
swap("app.py", '''    grant = classify_mod.GRANT_FOR_KIND.get(verdict["kind"])
    if grant:''', '''    grant = classify_mod.GRANT_FOR_KIND.get(verdict["kind"])
    if verdict["kind"] == "video" and not grant:
        grant = VIDEO_GRANT or None       # review 11-23 #12: videos had no grant check at all
    if grant:''')

# ---- #10 model wait bounded, fast connect failure -----------------------------------------------------------
swap("classify.py", '''_MODEL_LOCK = threading.Semaphore(1)
''', '''_MODEL_LOCK = threading.Semaphore(1)
# Review 11-23 #10: the lock was waited for without limit INSIDE a pool thread, so files queued behind a sleeping
# PC (2 x 120 s each) held every worker thread and starved the other doors. Now: wait at most MODEL_WAIT for a
# turn (then "unknown:busy" -> unsorted, recoverable), and fail the CONNECT in LLM_CONNECT seconds.
MODEL_WAIT = float(os.environ.get("INTAKE_MODEL_WAIT", "30"))
LLM_CONNECT = float(os.environ.get("INTAKE_LLM_CONNECT", "5"))
''')
swap("classify.py", '''    with _MODEL_LOCK:                    # T0931 item 16
''', '''    if not _MODEL_LOCK.acquire(timeout=MODEL_WAIT):     # review 11-23 #10: bounded wait
        return "unknown:busy"
    with _ReleaseAfter(_MODEL_LOCK):     # T0931 item 16 (one model call at a time)
''')
swap("classify.py", '''                               headers=headers, timeout=LLM_TIMEOUT)''',
     '''                               headers=headers, timeout=httpx.Timeout(LLM_TIMEOUT, connect=LLM_CONNECT))''')
swap("classify.py", '''def ask_model(name: str, text: str) -> str:''', '''class _ReleaseAfter:
    """`with _ReleaseAfter(lock):` releases a lock that was already acquired (with a timeout) when the block ends."""

    def __init__(self, lock):
        self.lock = lock

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.lock.release()
        return False


def ask_model(name: str, text: str) -> str:''')

for name, text in files.items():
    compile(text, name, "exec")
    (folder / name).write_text(text, encoding="utf-8")
print("patched: app.py, classify.py (review 11-23 #5 #6 #10 #12)")
