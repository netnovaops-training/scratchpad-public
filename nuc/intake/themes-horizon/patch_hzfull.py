#!/usr/bin/env python3
"""patch_hzfull.py - fix: an empty /sending page showed the finished black hole + the inward ring (2026-11-22).

    python3 patch_hzfull.py FILE [FILE ...]      (themes_horizon.py.part and/or a COPY's themes.py)

Why (11-22 doc review): with nothing waiting, the page sets body.done at once. The horizon rules keyed the
collapsed star, the full-size hole and the "gulp" ring on body.done alone, so an idle page played the end of an
upload instead of showing the bright star (garden has no such problem: its flower also needs --bud at 92 %).
Now the picture's script adds class hz-full only when the progress is 100 %, and those three rules need it.
Refuses a live intake path, a file that is already patched, or any old text that is not found exactly once.
Variables: SWAPS (old -> new pairs), path / s (the file being patched).
"""
import sys
from pathlib import Path

SWAPS = [
    ("  body.done .atom .star{transform:scale(0);opacity:0}\n",
     "  /* hz-full: only when the upload really reached 100 % (an empty page is also body.done: keep the star) */\n"
     "  body.done .atom.hz-full .star{transform:scale(0);opacity:0}\n"),
    ("  body.done .atom .hole{transform:scale(1)}\n",
     "  body.done .atom.hz-full .hole{transform:scale(1)}\n"),
    ("  body.done .atom .gulp{animation:hz-gulp",
     "  body.done .atom.hz-full .gulp{animation:hz-gulp"),
    ("   const set = () => svg.style.setProperty('--g',\n"
     "     Math.max(0, Math.min(1, (+bar.getAttribute('aria-valuenow') || 0) / 100)).toFixed(3));\n",
     "   const set = () => {\n"
     "     const g = Math.max(0, Math.min(1, (+bar.getAttribute('aria-valuenow') || 0) / 100));\n"
     "     svg.style.setProperty('--g', g.toFixed(3));\n"
     "     svg.classList.toggle('hz-full', g >= 0.999);   // the end state needs a real 100 %\n"
     "   };\n"),
]
live = (Path.home() / "nuc-homelab" / "intake").resolve()
for arg in sys.argv[1:]:
    path = Path(arg).expanduser()
    if path.resolve().parent == live:
        raise SystemExit(f"refusing to patch the LIVE file {path}")
    s = path.read_text(encoding="utf-8")
    if "hz-full" in s:
        raise SystemExit(f"{path}: already patched")
    for old, new in SWAPS:
        if s.count(old) != 1:
            raise SystemExit(f"{path}: expected exactly one copy of {old[:50]!r}, found {s.count(old)}")
        s = s.replace(old, new)
    if path.suffix == ".py":
        compile(s, str(path), "exec")
    path.write_text(s, encoding="utf-8")
    print("patched", path)
