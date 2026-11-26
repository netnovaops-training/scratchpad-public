"""test_locks.py - proof for patch_locks.py. Run INSIDE the patched intake image:

    docker run --rm --entrypoint python IMAGE test_locks.py

1) Importing app (as rescan_held.py does) leaves an existing finish lock alone.
2) The server start-up (app._lifespan) still clears a stale lock left by a restart.
Variables: ROOT (throw-away folder), lock (a fake <uid>.finishing file), fails.
"""
import asyncio
import os
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(tempfile.mkdtemp())
for d in ("unsorted", "stage", "tmp"):
    (ROOT / d).mkdir()
os.environ.update({"INTAKE_KEY": "k-test", "INTAKE_UNSORTED": str(ROOT / "unsorted"), "INTAKE_STAGE": str(ROOT / "stage"),
                   "TMPDIR": str(ROOT / "tmp"), "LLM_BASE": "http://x", "AUTH_LEVELS_URL": "http://x",
                   "GOTENBERG_URL": "http://x", "INTAKE_LOG": str(ROOT / "unsorted" / "log")})
lock = ROOT / "stage" / "abc123.finishing"
lock.write_text("pid")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app  # noqa: E402  -- exactly what rescan_held.py does

fails = 0
ok1 = lock.exists()
print(("PASS" if ok1 else "FAIL"), "importing app leaves a live finish lock alone")
fails += not ok1


async def start_stop():
    async with app._lifespan(app.app):
        pass

asyncio.run(start_stop())
ok2 = not lock.exists()
print(("PASS" if ok2 else "FAIL"), "server start-up clears a stale finish lock")
fails += not ok2
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
