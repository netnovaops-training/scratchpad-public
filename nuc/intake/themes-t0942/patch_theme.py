#!/usr/bin/env python3
"""patch_theme.py - T0942 step 1, in a COPY of intake only: make the drop's look come from themes.py.

    python3 patch_theme.py ~/sandbox/t0942/intake THEMES_GARDEN_PART

Cuts the Atomic look OUT of atomic_ui.PAGE (the :root colour line, the atom <svg>, the name and the theme
colour) into themes.py as the ATOMIC theme, so with theme "atomic" the page is byte-for-byte the old one
(checked by test_themes.py). portal.py's <title>/theme-color and share.MANIFEST's name/colours read the same
theme. Refuses on the live folder, if already patched, or if any cut is not found exactly where expected.
"""
import re
import sys
from pathlib import Path

def add_import(src: str, line: str) -> str:
    """Insert an import line before the first import that is NOT `from __future__` (which must stay first)."""
    m = re.search(r"^(?!from __future__)(import|from) ", src, re.M)
    return src[:m.start()] + line + "\n" + src[m.start():]


folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
if (folder / "themes.py").exists():
    raise SystemExit("already patched")

# ---- atomic_ui.PAGE ----
ui = (folder / "atomic_ui.py").read_text(encoding="utf-8")
start = ui.index('PAGE = r"""')
end = ui.index('"""', start + len('PAGE = r"""')) + 3
page = ui[start:end]
root = re.search(r"^:root\{--bg:#0b1026[^\n]*\}$", page, re.M).group(0)
svg = re.search(r"<svg class=atom.*?</svg>", page, re.S).group(0)
assert page.count(root) == 1 and page.count(svg) == 1
assert page.count("Atomic Share") == 2 and page.count('content="#0b1026"') == 1
new_page = (page.replace(root, "@@ROOT@@").replace(svg, "@@SVG@@").replace("Atomic Share", "@@NAME@@")
            .replace('content="#0b1026"', 'content="@@THEME_COLOR@@"'))
ui = ui[:start] + new_page + ("\n# T0942 (2026-11-21): the look (colours, picture, name) comes from themes.py / drop.toml;"
                              " with theme atomic this is byte-for-byte the old page (test_themes.py).\n"
                              "PAGE = themes.apply(PAGE)") + ui[end:]
ui = add_import(ui, "import themes  # T0942: the drop's look")
compile(ui, "atomic_ui.py", "exec")
(folder / "atomic_ui.py").write_text(ui, encoding="utf-8")

# ---- themes.py (ATOMIC cut from the code above + the hand-made garden part) ----
head = ('"""themes.py - the drop\'s look, chosen by drop.toml [brand] theme (T0942, 2026-11-21).\n\n'
        'ATOMIC was CUT from atomic_ui.PAGE by patch_theme.py, so the Atomic look is exactly the old one.\n'
        'Keys: root (the :root colour line of the sending page), theme_color (browser bar + manifest), svg (the\n'
        'animated picture; must keep the classes atom, orbits, nucleus, glow that the page script drives).\n"""\n'
        "import dropconfig\n\n"
        f"ATOMIC = {{\n    \"root\": {root!r},\n    \"theme_color\": \"#0b1026\",\n    \"svg\": {svg!r},\n}}\n")
themes = head + Path(sys.argv[2]).read_text(encoding="utf-8")
compile(themes, "themes.py", "exec")
(folder / "themes.py").write_text(themes, encoding="utf-8")

# ---- portal.py: <title> and theme-color of the main page ----
po = (folder / "portal.py").read_text(encoding="utf-8")
for old, new in (("<title>Atomic Share</title>", "<title>{themes.current()['name']}</title>"),
                 ('<meta name="theme-color" content="#0b1026">',
                  '<meta name="theme-color" content="{themes.current()[\'theme_color\']}">')):
    assert po.count(old) == 1, old
    po = po.replace(old, new)
po = add_import(po, "import themes  # T0942")
compile(po, "portal.py", "exec")
(folder / "portal.py").write_text(po, encoding="utf-8")

# ---- share.MANIFEST ----
sh = (folder / "share.py").read_text(encoding="utf-8")
for old, new in (('    "name": "Atomic Share",\n', '    "name": themes.current()["name"],\n'),
                 ('    "short_name": "Atomic Share",\n', '    "short_name": themes.current()["short_name"],\n'),
                 ('    "background_color": "#0b1026",\n', '    "background_color": themes.current()["theme_color"],\n'),
                 ('    "theme_color": "#0b1026",\n', '    "theme_color": themes.current()["theme_color"],\n')):
    assert sh.count(old) == 1, old
    sh = sh.replace(old, new)
# step 3: the app icon follows the theme. A theme with "icon_module" serves that module's ICON_SVG / ICON_192 /
# ICON_512; atomic has none, so its icon bytes are the old ones (test_themes.py compares them).
for old, new in (('    return Response(ICON, media_type="image/svg+xml")\n',
                  '    mod = themes.current().get("icon_module")  # T0942 step 3\n'
                  '    return Response(importlib.import_module(mod).ICON_SVG if mod else ICON, media_type="image/svg+xml")\n'),
                 ('def icon_192():\n    import icons_png\n',
                  'def icon_192():\n    icons_png = importlib.import_module(themes.current().get("icon_module", "icons_png"))\n'),
                 ('def icon_512():\n    import icons_png\n',
                  'def icon_512():\n    icons_png = importlib.import_module(themes.current().get("icon_module", "icons_png"))\n')):
    assert sh.count(old) == 1, old
    sh = sh.replace(old, new)
sh = add_import(sh, "import importlib  # T0942 step 3: icon module per theme")
sh = add_import(sh, "import themes  # T0942")
compile(sh, "share.py", "exec")
(folder / "share.py").write_text(sh, encoding="utf-8")
print("patched: atomic_ui.py, portal.py, share.py; wrote themes.py")
