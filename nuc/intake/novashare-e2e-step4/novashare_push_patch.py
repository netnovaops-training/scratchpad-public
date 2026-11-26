#!/usr/bin/env python3
"""novashare_push_patch.py - Novashare E2E Step 4 patcher (2026-11-24), for ~/sandbox/horizon/SWAP-PATCH.sh.

    python3 novashare_push_patch.py INTAKE_COPY_DIR

Copies push.py + push_js.py (new) and devices.py, receive.py, receive_js.py (Step 1 / Step 3 files with the
Notifications box and the service worker's push handlers added) from this file's folder into the copy; adds the push
router and its worker (wrapped around app.py's own lifespan) to the END of app.py, after Step 3's receive router (which
it needs); appends the pinned web push packages to requirements.txt. Nothing else in app.py changes. Running it twice
changes nothing the second time.

NOTE: SWAP-PATCH.sh installs *.py only. requirements.txt is installed by push_swap.sh (README.md), which wraps it.
"""
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
FILES = ("devices.py", "receive.py", "receive_js.py", "push.py", "push_js.py")
MARK = "# Novashare E2E step 4 (2026-11-24)"
BLOCK = f"""

{MARK}: web push, /relay/api/push/* and /relay/push/push.js (push.py). New routes, plus one worker thread
# that starts after app.py's own lifespan startup and stops before its shutdown (rescan_held.py's import starts nothing).
import push as _novashare_push  # noqa: E402

app.include_router(_novashare_push.router)
app.router.lifespan_context = _novashare_push.wrap_lifespan(app.router.lifespan_context)
"""
REQ_MARK = "# Novashare E2E step 4 (2026-11-24)"
# Every version >= 2 weeks old on 2026-11-24 and pip-audit clean (the whole resolved set, on the NUC). charset-normalizer
# is held at 3.5.2: 3.5.3 was one day old. The transitive packages are pinned too, so a rebuild cannot drift.
REQS = f"""
{REQ_MARK}: web push (push.py). pywebpush 2.5.0 (2026-08-29) and what it pulls in, all pinned.
pywebpush==2.5.0
py-vapid==1.9.6
http-ece==1.2.1
cryptography==50.0.2
cffi==2.1.1
pycparser==3.12
requests==2.34.2
urllib3==2.8.0
charset-normalizer==3.5.2
aiohttp==3.14.5
aiohappyeyeballs==2.7.1
aiosignal==1.4.0
attrs==26.1.0
frozenlist==1.8.0
multidict==7.2.1
propcache==0.5.4
yarl==1.25.1
"""


def main(dst: pathlib.Path) -> None:
    app = dst / "app.py"
    text = app.read_text(encoding="utf-8")
    if "include_router(receive_router)" not in text or not (dst / "receive.py").exists():
        raise SystemExit("app.py has no Step 3 receive router: not patched (Step 4 needs Step 3)")
    for f in FILES:
        shutil.copyfile(HERE / f, dst / f)
    if MARK not in text:
        app.write_text(text.rstrip("\n") + BLOCK, encoding="utf-8", newline="\n")
    req = dst / "requirements.txt"
    rt = req.read_text(encoding="utf-8")
    if REQ_MARK not in rt:
        req.write_text(rt.rstrip("\n") + "\n" + REQS, encoding="utf-8", newline="\n")
    print("patched:", ", ".join(FILES), "+ app.py router + requirements.txt")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]))
