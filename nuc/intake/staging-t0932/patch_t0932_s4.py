#!/usr/bin/env python3
"""patch_t0932_s4.py - T0932 step 4 (2026-11-22, autopilot): check free space on the DESTINATION before a
cross-disk filing copy. Edits <copy>/app.py (after patch_t0932_s3.py); never the live one.

    python3 patch_t0932_s4.py <intake folder copy>

Why (11-21 audit point 4): free space was checked only on the staging disk, never on the library (a NAS share),
so a full share failed half-way through the copy. Now it is refused before the first byte is written, with the
staging copy kept. Anchor must be found once, or nothing changes.
"""
import pathlib
import sys

f = pathlib.Path(sys.argv[1]) / "app.py"
s = f.read_text(encoding="utf-8")

OLD = ('    else:\n'
       '        try:\n'
       '            _copy_checked(tmp, part, digest)\n')
NEW = ('    else:\n'
       '        # T0932 step 4 (2026-11-22): the library disk (usually a NAS share) must hold the file plus\n'
       '        # INTAKE_DEST_RESERVE_MB before the copy starts; before, only the staging disk was checked and a full\n'
       '        # share failed mid-copy. Refused = OSError(ENOSPC), staging copy kept, nothing written. A share that\n'
       '        # cannot report its space (OSError) is not refused here; the copy + fsync then fails loudly instead.\n'
       '        try:\n'
       '            free = shutil.disk_usage(dest_dir).free\n'
       '        except OSError:\n'
       '            free = None\n'
       '        need = tmp.stat().st_size + DEST_RESERVE\n'
       '        if free is not None and free < need:\n'
       '            say(f"  library disk full for {name}: {free // 1048576} MB free, {need // 1048576} MB needed")\n'
       '            raise OSError(errno.ENOSPC, "the library disk is full, so the file was kept and not filed")\n'
       '        try:\n'
       '            _copy_checked(tmp, part, digest)\n')
A2 = 'MAX_MB = int(os.environ.get("INTAKE_MAX_MB", "2048"))\n'
B2 = A2 + ('# T0932 step 4: room a library disk must keep free after a filing copy (default 1 GB, like INTAKE_RESERVE_MB).\n'
           'DEST_RESERVE = int(os.environ.get("INTAKE_DEST_RESERVE_MB", "1024")) * 1048576\n')
for a in (OLD, A2):
    if s.count(a) != 1:
        sys.exit(f"anchor found {s.count(a)} times, nothing changed: {a[:50]!r}")
s = s.replace(OLD, NEW).replace(A2, B2)
f.write_text(s, encoding="utf-8")
print("patched", f)
