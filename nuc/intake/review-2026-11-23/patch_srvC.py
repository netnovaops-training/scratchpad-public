#!/usr/bin/env python3
"""patch_srvC.py - Novashare backend review 2026-11-23, batch C (the three lows, T0970-T0972), on a COPY of intake.

    python3 patch_srvC.py COPY_OF_INTAKE

  #15 the old sequential PUT /up/{uid}?offset= had no lock: a resend of the same offset while the first was still
      streaming passed the offset check twice and appended the piece twice (file past its size, /finish 409
      forever). Now a uid already being written answers 409 with the current offset. One uvicorn process, so an
      in-memory set is the truth. The route stays for old clients (today's pages always send in parallel).
  #16 writes had no cross-site check; safety rested on the SSO cookie's SameSite. The gatekeeper now refuses a
      non-GET request whose Sec-Fetch-Site is "cross-site". same-origin, same-site and none (the phone's share
      sheet, server-to-server calls without the header) still pass.
  #17 one person could hold MAX_OPEN (20) x the max size of reserved disk for 48 h. Now the declared sizes of a
      person's unfinished uploads plus the new one may not pass INTAKE_OPEN_CAP_MB (default 4 x INTAKE_MAX_MB, so the 3 parallel sends of the Drop page fit): 429.
Refuses the live folder, an already patched copy, or any old text not found exactly once.
Variables: folder, files (name -> text), swap() (exact-once replace).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
files = {n: (folder / n).read_text(encoding="utf-8") for n in ("app.py", "resumable.py")}
if "_SEQ_BUSY" in files["resumable.py"]:
    raise SystemExit("already patched")


def swap(name, old, new):
    if files[name].count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:70]!r}, found {files[name].count(old)}")
    files[name] = files[name].replace(old, new)


# ---- #16 -------------------------------------------------------------------------------------------------
swap("app.py", '''    write = request.method not in READ_METHODS
''', '''    write = request.method not in READ_METHODS
    # Review 11-23 #16: another site's page must not be able to make a signed-in visitor upload or share.
    # Browsers send Sec-Fetch-Site; only "cross-site" is refused (the share sheet sends "none", server calls none).
    if write and (request.headers.get("sec-fetch-site") or "").lower() == "cross-site":
        return JSONResponse({"error": "not allowed from another site"}, status_code=403)
''')

# ---- #17 -------------------------------------------------------------------------------------------------
swap("resumable.py", '''MAX_OPEN = int(os.environ.get("INTAKE_MAX_OPEN", "20"))''',
     '''# Review 11-23 #17: total declared bytes of one person's unfinished uploads (default 4 x the file limit: 3 parallel sends fit).
OPEN_CAP = int(os.environ.get("INTAKE_OPEN_CAP_MB", str(4 * int(os.environ.get("INTAKE_MAX_MB", "2048"))))) * 1048576
MAX_OPEN = int(os.environ.get("INTAKE_MAX_OPEN", "20"))''')
swap("resumable.py", '''    if _open_count(email) >= MAX_OPEN:   # T0931: one person cannot fill the disk with half-sent uploads
        raise HTTPException(429, "Too many unfinished uploads. Let some finish, then try again.")''',
     '''    if _open_count(email) >= MAX_OPEN:   # T0931: one person cannot fill the disk with half-sent uploads
        raise HTTPException(429, "Too many unfinished uploads. Let some finish, then try again.")
    if _open_bytes(email) + size > OPEN_CAP:   # review 11-23 #17: reserved space, not just the count
        raise HTTPException(429, "Too much is still waiting to finish. Let some uploads finish, then try again.")''')
swap("resumable.py", '''def _pieces(meta: dict) -> int:''', '''def _open_bytes(email: str) -> int:
    """Declared size of this person's unfinished uploads (review 11-23 #17; fallocate reserves them for real)."""
    total = 0
    for p in STAGE.glob("*.json"):
        if p.name.endswith(".result.json") or not p.with_suffix(".part").exists():
            continue
        try:
            m = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if m.get("email") == email:
            total += int(m.get("size") or 0)
    return total


def _pieces(meta: dict) -> int:''')

# ---- #15 -------------------------------------------------------------------------------------------------
swap("resumable.py", '''@router.put("/up/{uid}")
async def piece(uid: str, offset: int, request: Request, x_auth_email: str | None = Header(default=None)):
    meta, part, meta_p = _load(uid, _who(x_auth_email))
    have = part.stat().st_size''', '''# Review 11-23 #15: uids with a sequential piece being written right now (one process: this set is the truth).
_SEQ_BUSY: set[str] = set()


@router.put("/up/{uid}")
async def piece(uid: str, offset: int, request: Request, x_auth_email: str | None = Header(default=None)):
    """Old sequential route: one piece at an offset. A second request for the same uid while one is still being
    written gets 409 with the current offset (review 11-23 #15: both used to pass the check and append twice)."""
    meta, part, meta_p = _load(uid, _who(x_auth_email))
    if uid in _SEQ_BUSY:
        return JSONResponse({"offset": part.stat().st_size if part.exists() else 0, "busy": True}, status_code=409)
    _SEQ_BUSY.add(uid)
    try:
        return await _piece_seq(uid, offset, request, meta, part, meta_p)
    finally:
        _SEQ_BUSY.discard(uid)


async def _piece_seq(uid, offset, request, meta, part, meta_p):
    have = part.stat().st_size''')

for name, text in files.items():
    compile(text, name, "exec")
    (folder / name).write_text(text, encoding="utf-8")
print("patched: app.py, resumable.py (review 11-23 #15 #16 #17)")
