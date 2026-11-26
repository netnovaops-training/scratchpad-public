#!/usr/bin/env python3
"""novashare_devices_patch.py - Novashare E2E Step 1 patcher (2026-11-24), for ~/sandbox/horizon/SWAP-PATCH.sh.

    python3 novashare_devices_patch.py INTAKE_COPY_DIR

Copies devices.py, devices_js.py and vendor_noble_pq.py (from this file's folder) into the copy and adds the
devices router to the END of its app.py. Nothing else in app.py changes: no edit to the gatekeeper, the lifespan,
/healthz or any existing route. Running it twice changes nothing the second time.
"""
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
FILES = ("devices.py", "devices_js.py", "vendor_noble_pq.py")
MARK = "# Novashare E2E step 1 (2026-11-24)"
BLOCK = f"""

{MARK}: the device registry, /devices and /devices/api/* (devices.py). New routes only.
from devices import router as devices_router  # noqa: E402

app.include_router(devices_router)
"""


def main(dst: pathlib.Path) -> None:
    app = dst / "app.py"
    text = app.read_text(encoding="utf-8")
    if "include_router(atomic_ui_router)" not in text:
        raise SystemExit("app.py does not look like the intake app (no atomic_ui router): not patched")
    for f in FILES:
        shutil.copyfile(HERE / f, dst / f)
    if MARK not in text:
        app.write_text(text.rstrip("\n") + BLOCK, encoding="utf-8", newline="\n")
    print("patched:", ", ".join(FILES), "+ app.py router")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]))
