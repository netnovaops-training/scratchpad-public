#!/usr/bin/env python3
"""patch_locks.py - clear stale finish locks at SERVER START, not on import (2026-11-23), in a COPY of intake.

    python3 patch_locks.py COPY_OF_INTAKE

Found by the 11-23 backend audit (backend-design:audit) and confirmed in the code: resumable.py called
_clear_finish_locks() at import ("one uvicorn process, so no finish can be running at import"). That stopped being
true on 11-22, when rescan_held.py (cron :23, `docker exec intake python rescan_held.py`) started importing app,
which imports resumable: every hour it deleted the LIVE server's finish locks, so a phone retrying a finish that
was still running could start a second filing of the same upload. Now the clear runs in app._lifespan, which only
the real server (uvicorn) runs; importing the modules (rescan_held, the tests) clears nothing.
Refuses the live folder, an already patched copy, or old text not found exactly once.
Variables: folder, files (name -> text), swap() (exact-once replace).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
files = {n: (folder / n).read_text(encoding="utf-8") for n in ("resumable.py", "app.py")}
if "clear_finish_locks" in files["app.py"]:
    raise SystemExit("already patched")


def swap(name, old, new):
    if files[name].count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:60]!r}, found {files[name].count(old)}")
    files[name] = files[name].replace(old, new)


swap("resumable.py", "\n\n_clear_finish_locks()\n",
     "\n\n# 2026-11-23: no longer called at import. rescan_held.py imports app (and so this module) every hour inside\n"
     "# the running container, which deleted live finish locks. app._lifespan calls it once at server start.\n")
swap("app.py", '''    import resumable
    task = asyncio.create_task(resumable.sweep_loop())
''', '''    import resumable
    # 2026-11-23: stale finish locks are cleared HERE, at real server start (was: at import of resumable, which the
    # hourly rescan_held.py import also triggered, deleting live locks). Only uvicorn runs this lifespan.
    resumable._clear_finish_locks()
    task = asyncio.create_task(resumable.sweep_loop())
''')
for name, text in files.items():
    compile(text, name, "exec")
    (folder / name).write_text(text, encoding="utf-8")
print("patched: resumable.py (no clear at import), app.py (clear at start-up)")
