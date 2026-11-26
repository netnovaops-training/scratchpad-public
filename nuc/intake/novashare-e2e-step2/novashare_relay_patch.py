#!/usr/bin/env python3
"""novashare_relay_patch.py - Novashare E2E Step 2 patcher (2026-11-24), for ~/sandbox/horizon/SWAP-PATCH.sh.

    python3 novashare_relay_patch.py INTAKE_COPY_DIR

Copies relay.py and relay_js.py (from this file's folder) into the copy and adds the relay router to the END of
its app.py, after Step 1's devices router (which it needs). Nothing else in app.py changes: no edit to the
gatekeeper, the lifespan, /healthz or any existing route. Running it twice changes nothing the second time.
"""
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
FILES = ("relay.py", "relay_js.py")
MARK = "# Novashare E2E step 2 (2026-11-24)"
BLOCK = f"""

{MARK}: the encrypted send, /relay/send and /relay/api/* (relay.py). New routes only.
from relay import router as relay_router  # noqa: E402

app.include_router(relay_router)
"""


def main(dst: pathlib.Path) -> None:
    app = dst / "app.py"
    text = app.read_text(encoding="utf-8")
    if "include_router(devices_router)" not in text or not (dst / "devices.py").exists():
        raise SystemExit("app.py has no Step 1 devices router: not patched (Step 2 needs Step 1)")
    for f in FILES:
        shutil.copyfile(HERE / f, dst / f)
    if MARK not in text:
        app.write_text(text.rstrip("\n") + BLOCK, encoding="utf-8", newline="\n")
    print("patched:", ", ".join(FILES), "+ app.py router")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]))
