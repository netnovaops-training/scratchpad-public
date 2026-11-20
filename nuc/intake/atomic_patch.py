#!/usr/bin/env python3
"""atomic_patch.py - wire resumable.py + atomic_ui.py into intake (T0884, 2026-11-18). Run inside a COPY of
~/nuc-homelab/intake (copy-test-swap). Idempotent: running it twice changes nothing the second time."""
from pathlib import Path

s = Path("share.py")
t = s.read_text()
old = "SW = \"self.addEventListener('fetch', e => e.respondWith(fetch(e.request)));\\n\"\n"
if old in t:
    t = t.replace(old, (
        "# T0884 (2026-11-18): the service worker now catches the share POST and keeps the files on the phone\n"
        "# until /sending uploads them in resumable pieces; see atomic_ui.py. Plain POST /share below still works.\n"
        "from atomic_ui import SW  # noqa: E402\n"))
    t = t.replace(
        "CAPTCHA pages (captcha_patch.py, 11-18) are refused for every link, never solved.\n",
        "CAPTCHA pages (captcha_patch.py, 11-18) are refused for every link, never solved.\n"
        "Resumable sends (T0884, 11-18): files shared from the phone now go SW -> /sending -> /up/* (resumable.py,\n"
        "atomic_ui.py) with a progress bar and resume after a signal drop; this POST /share stays as the fallback\n"
        "and still handles links and text.\n")
    s.write_text(t)
    print("share.py patched")
elif "from atomic_ui import SW" in t:
    print("share.py already patched")
else:
    raise SystemExit("share.py: SW line not found; stop")

a = Path("app.py")
u = a.read_text()
if "resumable_router" not in u:
    u += ("\n# Atomic Share resumable sends (T0884, 2026-11-18): /up/* pieces + /sending page with progress and resume.\n"
          "from resumable import router as resumable_router  # noqa: E402\n"
          "from atomic_ui import router as atomic_ui_router  # noqa: E402\n"
          "app.include_router(resumable_router)\n"
          "app.include_router(atomic_ui_router)\n")
    a.write_text(u)
    print("app.py patched")
else:
    print("app.py already patched")
