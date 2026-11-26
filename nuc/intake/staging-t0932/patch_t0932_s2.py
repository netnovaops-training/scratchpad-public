#!/usr/bin/env python3
"""patch_t0932_s2.py - T0932 step 2 (2026-11-22, autopilot): add a max-staging size check to resumable.py.

    python3 patch_t0932_s2.py <intake folder copy>     (edits that copy's resumable.py; never the live one)

Why: once staging moves to the USB SSD (step 1), that disk also holds Kopia's NUC repo and other backups.
The free-space check (size + INTAKE_RESERVE_MB) alone would let half-sent uploads use up to all free space on
the SSD; this caps what unfinished uploads may hold in total. Each exact-match anchor must be found once, or
the patch stops and changes nothing.
"""
import pathlib
import sys

f = pathlib.Path(sys.argv[1]) / "resumable.py"
s = f.read_text(encoding="utf-8")

A1 = 'MAX_OPEN = int(os.environ.get("INTAKE_MAX_OPEN", "20"))   # unfinished uploads one person may hold at once\n'
B1 = A1 + (
    '# T0932 step 2 (2026-11-22): staging moves to the USB SSD, which also holds the Kopia repo and backups. The\n'
    '# free-space check above alone let unfinished uploads take ALL free space there; this caps the total that\n'
    '# unfinished uploads may hold (their declared sizes, counted at start, so a parallel upload\'s fallocate and a\n'
    '# slow one-piece upload count the same). Default 200 GB = 20 uploads at the 10 GB limit; 634 GB free on 11-22.\n'
    '# 0 = no cap. A start over the cap answers 507; an idempotent resume of an upload already counted is not\n'
    '# refused (it is handed back before this check).\n'
    'STAGE_MAX = int(os.environ.get("INTAKE_STAGE_MAX_MB", "204800")) * 1048576\n')

A2 = ('    if _open_count(email) >= MAX_OPEN:   # T0931: one person cannot fill the disk with half-sent uploads\n'
      '        raise HTTPException(429, "Too many unfinished uploads. Let some finish, then try again.")\n')
B2 = A2 + (
    '    if STAGE_MAX and _staged_bytes() + size > STAGE_MAX:   # T0932 step 2: everyone\'s unfinished uploads together\n'
    '        _say(f"start refused: staging cap {STAGE_MAX // 1048576} MB, size={size}")\n'
    '        raise HTTPException(507, "The drop is busy with other uploads. Try again later.")\n')

A3 = 'def _pieces(meta: dict) -> int:\n'
B3 = ('def _staged_bytes() -> int:\n'
      '    """Declared sizes of all unfinished uploads (a .json whose .part still waits), everyone together.\n'
      '\n'
      '    T0932 step 2: declared size, not bytes on disk, so an upload counts in full from its start and the cap\n'
      '    cannot be passed by starting many uploads at once and sending slowly. Unreadable meta counts 0 (the same\n'
      '    rule _open_count uses); _sweep moves stale uploads out, so abandoned ones stop counting after STALE_HOURS.\n'
      '    """\n'
      '    n = 0\n'
      '    for p in STAGE.glob("*.json"):\n'
      '        if p.name.endswith(".result.json") or not p.with_suffix(".part").exists():\n'
      '            continue\n'
      '        try:\n'
      '            n += int(json.loads(p.read_text()).get("size") or 0)\n'
      '        except (OSError, ValueError, TypeError):\n'
      '            pass\n'
      '    return n\n'
      '\n'
      '\n') + A3

for a, b in ((A1, B1), (A2, B2), (A3, B3)):
    if s.count(a) != 1:
        sys.exit(f"anchor found {s.count(a)} times, nothing changed: {a[:60]!r}")
for a, b in ((A1, B1), (A2, B2), (A3, B3)):
    s = s.replace(a, b)
f.write_text(s, encoding="utf-8")
print("patched", f)
