#!/usr/bin/env python3
"""patch_t0964a.py - T0964 tidy-ups that do not touch classify.py (those wait for the T0958 swap), in a COPY.

    python3 patch_t0964a.py ~/sandbox/t0964/intake

1) portal.py: the video card said "Up to 10 GB per file" as fixed text; the limit is INTAKE_MAX_MB (10240 on
   the NUC today, 2048 when unset), so on a copy without the setting the page promised 5x what it accepts.
   The card now shows the real limit.
2) scan.py: held_count() counted every quarantine folder, including those rescan_held.py has released (no
   .held file left), so /healthz "held" over-counted. It now counts folders that still hold a .held file.
Refuses on the live folder or if any old text is not found exactly once.
Variables: folder (the copy), po / sc (file texts), swap() (exact-once replace).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser() if len(sys.argv) == 2 else None
if folder is None or not (folder / "portal.py").is_file():
    raise SystemExit("usage: patch_t0964a.py <copy of the intake folder>")
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")


def swap(text: str, old: str, new: str, name: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:60]!r}, found {text.count(old)}")
    return text.replace(old, new)


po = (folder / "portal.py").read_text(encoding="utf-8")
if "MAX_GB" in po:
    raise SystemExit("already patched")
po = swap(po, "import html\n", '''import html
import os
''', "portal.py")
po = swap(po, "from dropconfig import WHO, PRIVATE_OK, NAMES  # noqa: E402\n",
          '''from dropconfig import WHO, PRIVATE_OK, NAMES  # noqa: E402

# T0964 (2026-11-21): the video card shows the real upload limit (app.MAX_MB reads the same setting); it said
# "10 GB" as fixed text, which was only true while compose set INTAKE_MAX_MB=10240.
MAX_GB = f"{int(os.environ.get('INTAKE_MAX_MB', '2048')) / 1024:g}"
''', "portal.py")
po = swap(po, "Goes straight to Jellyfin. Up to 10 GB per file.", "Goes straight to Jellyfin. Up to {MAX_GB} GB per file.",
          "portal.py")
compile(po, "portal.py", "exec")

sc = (folder / "scan.py").read_text(encoding="utf-8")
sc = swap(sc, '''        return sum(1 for d in QUARANTINE.iterdir() if d.is_dir())
''', '''        # T0964: only folders that still hold a file; rescan_held.py leaves released folders as a record.
        return sum(1 for d in QUARANTINE.iterdir() if d.is_dir() and any(p.suffix == ".held" for p in d.iterdir()))
''', "scan.py")
compile(sc, "scan.py", "exec")

(folder / "portal.py").write_text(po, encoding="utf-8")
(folder / "scan.py").write_text(sc, encoding="utf-8")
print("patched: portal.py (real upload limit on the video card), scan.py (held_count)")
