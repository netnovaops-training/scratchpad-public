#!/usr/bin/env python3
"""novashare_receive_patch.py - Novashare E2E Step 3 patcher (2026-11-24), for ~/sandbox/horizon/SWAP-PATCH.sh.

    python3 novashare_receive_patch.py INTAKE_COPY_DIR

Copies receive.py + receive_js.py (new) and relay.py + relay_js.py (Step 2 files with the delivery table and the
send page's "Sent" list added) from this file's folder into the copy, and adds the receive router to the END of
its app.py, after Step 2's relay router (which it needs). Nothing else in app.py changes. Running it twice changes
nothing the second time.
"""
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
FILES = ("relay.py", "relay_js.py", "receive.py", "receive_js.py")
MARK = "# Novashare E2E step 3 (2026-11-24)"
BLOCK = f"""

{MARK}: receive, /relay/inbox and /relay/api/inbox, accept, decline, complete, pieces (receive.py). New routes only.
from receive import router as receive_router  # noqa: E402

app.include_router(receive_router)
"""


def main(dst: pathlib.Path) -> None:
    app = dst / "app.py"
    text = app.read_text(encoding="utf-8")
    if "include_router(relay_router)" not in text or not (dst / "relay.py").exists():
        raise SystemExit("app.py has no Step 2 relay router: not patched (Step 3 needs Step 2)")
    for f in FILES:
        shutil.copyfile(HERE / f, dst / f)
    if MARK not in text:
        app.write_text(text.rstrip("\n") + BLOCK, encoding="utf-8", newline="\n")
    print("patched:", ", ".join(FILES), "+ app.py router")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]))
