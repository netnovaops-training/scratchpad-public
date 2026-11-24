#!/usr/bin/env python3
"""novashare_sweep_patch.py - Novashare E2E Step 6 patcher (2026-11-24), for ~/sandbox/horizon/SWAP-PATCH.sh.

    python3 novashare_sweep_patch.py INTAKE_COPY_DIR

Copies sweep.py + audit.py (new) and relay.py, relay_js.py, receive.py, receive_js.py, push.py, devices.py (changed)
from this file's folder into the copy, and adds the sweep router and worker to the END of app.py, after Step 5's
picker block. Nothing else in app.py changes. Running it twice changes nothing the second time.

The changed files were built on the live copies of 2026-11-24 after Step 5 (sha256 below). If live has moved on, the
patcher stops instead of overwriting newer work: rebuild Step 6 on the new live files first.
Only *.py files change, so SWAP-PATCH.sh alone does the swap (no wrapper, no new package, no compose change).
"""
import hashlib
import pathlib
import shutil
import sys

HERE = pathlib.Path(__file__).resolve().parent
NEW = ("sweep.py", "audit.py")
BASE = {   # sha256 of the live file each changed file was built on (2026-11-24, Steps 1-5 live)
    "relay.py": "55ed8e7963b58b54494252e42d81f80eb163da4b65546e472c4aa73ab8f3d549",
    "relay_js.py": "158645713f25a5a39d725bcb6eb02e7929d208a66bf827fa19ee2afb54672928",
    "receive.py": "12709b9bbd3d793824f2d618b4ea6b60889efa4132f01c0dd848f5122d1c830d",
    "receive_js.py": "1447068596b2b5a98f80be7d338c7a7baf426f58aa2f4556aa972523c8dc41b2",
    "push.py": "cd5889e3cc41bd0a7a4b51000d0c17968edaa99586792e16b94a53208a80b62b",
    "devices.py": "24fe4c4314ca269a870a3b70c1dde0661140bb2789f5b69c94cfacc9d82b74e8",
}
MARK = "# Novashare E2E step 6 (2026-11-24)"
BLOCK = f"""

{MARK}: expiry sweep, sender cancel, quotas, audit log (sweep.py, audit.py). New routes
# (/relay/api/transfers/<id>/cancel, /relay/api/relay-stats and /relay/api/audit for the admin) plus one worker thread
# that sweeps the relay every 10 minutes, started after the lifespan startup above (rescan_held.py's import starts nothing).
import sweep as _novashare_sweep  # noqa: E402

app.include_router(_novashare_sweep.router)
app.router.lifespan_context = _novashare_sweep.wrap_lifespan(app.router.lifespan_context)
"""


def main(dst: pathlib.Path) -> None:
    app = dst / "app.py"
    text = app.read_text(encoding="utf-8")
    if "app.include_router(pick_router)" not in text or not (dst / "pick.py").exists():
        raise SystemExit("app.py has no Step 5 picker router: not patched (Step 6 needs Steps 1-5)")
    for f, want in BASE.items():
        have = hashlib.sha256((dst / f).read_bytes()).hexdigest()
        mine = hashlib.sha256((HERE / f).read_bytes()).hexdigest()
        if have not in (want, mine):
            raise SystemExit(f"{f} in the copy is not the file Step 6 was built on: not patched (rebuild on live)")
    for f in (*NEW, *BASE):
        shutil.copyfile(HERE / f, dst / f)
    if MARK not in text:
        app.write_text(text.rstrip("\n") + BLOCK, encoding="utf-8", newline="\n")
    print("patched:", ", ".join((*NEW, *BASE)), "+ app.py router and worker")


if __name__ == "__main__":
    main(pathlib.Path(sys.argv[1]))
