#!/usr/bin/env python3
"""patch_hzicon.py - give the horizon theme (Novashare) its own app icon, in a COPY of intake (2026-11-23).

    python3 patch_hzicon.py COPY_OF_INTAKE

Jason, screenshot of the install sheet: "Still has the old logo". HORIZON had no icon_module, so share.py served
the atom. Adds "icon_module": "icons_png_horizon" (the copy must already hold icons_png_horizon.py, made by
gen_icons_horizon.py). Refuses the live folder, an already patched file, or old text not found exactly once.
Variables: folder, p (themes.py), s (its text), old / new (the replaced lines).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
if not (folder / "icons_png_horizon.py").is_file():
    raise SystemExit("icons_png_horizon.py missing in the copy")
p = folder / "themes.py"
s = p.read_text(encoding="utf-8")
if "icons_png_horizon" in s:
    raise SystemExit("already patched")
old = 'HORIZON = {\n    "root": ATOMIC["root"],\n    "theme_color": ATOMIC["theme_color"],\n'
new = old + ('    # 2026-11-23 (Jason: the install sheet still showed the old logo): Novashare\'s own icon, a black hole with\n'
             '    # its disk (gen_icons_horizon.py); without this key share.py served the atom.\n'
             '    "icon_module": "icons_png_horizon",\n')
if s.count(old) != 1:
    raise SystemExit("HORIZON block not found exactly once")
s = s.replace(old, new)
compile(s, "themes.py", "exec")
p.write_text(s, encoding="utf-8")
print("patched themes.py (HORIZON icon_module)")
