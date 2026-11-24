#!/usr/bin/env python3
"""patch_purple.py - Novashare in black and purple with an event-horizon orange (2026-11-23 evening), on a COPY.

    python3 patch_purple.py COPY_OF_INTAKE NEW_icons_png_horizon.py

Jason, 11-23: "make them purple and black for everything / And orange like a event horizon". The horizon theme had
the atomic night-navy palette with cyan accents. Now: near-black sky (#07050d, a hint of violet), purple accent
(#b388ff text/buttons, #8a4dff glow, #a66bff inner disk), orange star / outer disk / photon ring (#ff8a3d, #ffb35c).
Changes:
  - themes.py HORIZON: its own colour line (was ATOMIC["root"], so atomic stays byte-for-byte the same), theme_color,
    the Drop page tokens (portal_root), the bar track (3:1 on the sky), the picture's glow/disk/debris/ring colours,
    the small heading mark.
  - share.py: the icon version tag is now a hash of the icon bytes, not the module name. With the module name the
    new picture kept the same address (?v=icons_png_horizon), and phones would keep the cached navy icon.
  - icons_png_horizon.py: the new icon (gen_icons_horizon.py, colours above).
Contrast (test_purple.py measures it): ink 17:1, dim 7.9:1, accent 8.6:1 on the sky; button text on purple 8.6:1.
Refuses the live folder, an already patched copy, or any old text not found exactly once.
Variables: folder, files (name -> text), swap() (exact-once replace).
"""
import shutil
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
files = {n: (folder / n).read_text(encoding="utf-8") for n in ("themes.py", "share.py")}
if "#07050d" in files["themes.py"]:
    raise SystemExit("already patched")


def swap(name, old, new):
    if files[name].count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:70]!r}, found {files[name].count(old)}")
    files[name] = files[name].replace(old, new)


ROOT = (':root{--bg:#07050d;--card:#151024;--ink:#efe9ff;--dim:#ab9fcc;--cyan:#b388ff;--orange:#ff8a3d;'
        '--ok:#4ade80;--bad:#f87171;--spin:7s}\\nhtml .bar{background:#7a62b3}')
swap("themes.py", '''    "theme_color": ATOMIC["theme_color"],
    # 2026-11-23 (Jason: the install sheet''', '''    "theme_color": "#07050d",
    # 2026-11-23 (Jason: the install sheet''')
swap("themes.py", '''HORIZON = {
    "root": ATOMIC["root"],''', f'''HORIZON = {{
    # 2026-11-23 evening (Jason: "purple and black ... and orange like an event horizon"): its own palette, was the
    # atomic navy + cyan. --cyan is the accent NAME the page uses; here it holds purple. "html .bar" beats the page's
    # own .bar track (same trick as garden): #7a62b3 is 3.4:1 on the sky (WCAG 1.4.11 wants 3).
    "root": "{ROOT}",''')
swap("themes.py", '''"--ink:#e8ecff;--dim:#9aa3c7;--line:#26305a;--bg:#0b1026;--card:#131a3a;--accent:#5ad1ff;"
                   "--on-accent:#0b1026;--accent-soft:#16264a;--warn:#ffb46b;--warn-soft:#2a2238;--ok:#4ade80;--bad:#f87171;color-scheme:dark",''',
     '''"--ink:#efe9ff;--dim:#ab9fcc;--line:#2c2142;--bg:#07050d;--card:#151024;--accent:#b388ff;"
                   "--on-accent:#07050d;--accent-soft:#2a1b47;--warn:#ff8a3d;--warn-soft:#2a1a10;--ok:#4ade80;--bad:#f87171;color-scheme:dark",''')
# the picture: glow, inner disk, debris and ring go purple / orange; the star keeps its gold-orange gradient
swap("themes.py", '''<radialGradient id="hzglow"><stop offset="0" stop-color="#ffb46b" stop-opacity=".45"/><stop offset=".5" stop-color="#5ad1ff" stop-opacity=".1"/><stop offset="1" stop-color="#5ad1ff" stop-opacity="0"/></radialGradient>''',
     '''<radialGradient id="hzglow"><stop offset="0" stop-color="#ff8a3d" stop-opacity=".4"/><stop offset=".5" stop-color="#8a4dff" stop-opacity=".18"/><stop offset="1" stop-color="#8a4dff" stop-opacity="0"/></radialGradient>''')
swap("themes.py", '''  <ellipse cx="48" cy="48" rx="36" ry="9" fill="none" stroke="#ffb46b" stroke-width="2.2" stroke-opacity=".75"/>
  <ellipse cx="48" cy="48" rx="28" ry="6.5" fill="none" stroke="#5ad1ff" stroke-width="1.4" stroke-opacity=".7"/>''',
     '''  <ellipse cx="48" cy="48" rx="36" ry="9" fill="none" stroke="#ff8a3d" stroke-width="2.2" stroke-opacity=".8"/>
  <ellipse cx="48" cy="48" rx="28" ry="6.5" fill="none" stroke="#a66bff" stroke-width="1.4" stroke-opacity=".8"/>''')
swap("themes.py", '''  <circle cx="84" cy="48" r="1.8" fill="#ffd27a"/><circle cx="20" cy="40" r="1.5" fill="#bdeeff"/>
  <circle cx="60" cy="16" r="1.3" fill="#fff"/><circle cx="34" cy="80" r="1.6" fill="#ffb46b"/>
  <circle cx="76" cy="74" r="1.2" fill="#bdeeff"/><circle cx="14" cy="62" r="1.2" fill="#fff"/>
  <circle cx="70" cy="26" r="1" fill="#ffd27a"/><circle cx="26" cy="22" r="1" fill="#bdeeff"/>''',
     '''  <circle cx="84" cy="48" r="1.8" fill="#ffb35c"/><circle cx="20" cy="40" r="1.5" fill="#d8c4ff"/>
  <circle cx="60" cy="16" r="1.3" fill="#fff"/><circle cx="34" cy="80" r="1.6" fill="#ff8a3d"/>
  <circle cx="76" cy="74" r="1.2" fill="#d8c4ff"/><circle cx="14" cy="62" r="1.2" fill="#fff"/>
  <circle cx="70" cy="26" r="1" fill="#ffb35c"/><circle cx="26" cy="22" r="1" fill="#d8c4ff"/>''')
swap("themes.py", '''  <circle class="ring" cx="48" cy="48" r="14.6" fill="none" stroke="#ffe6b0" stroke-width="1.6"/>''',
     '''  <circle class="ring" cx="48" cy="48" r="14.6" fill="none" stroke="#ffb35c" stroke-width="1.8"/>''')
swap("themes.py", ''' <circle class="gulp" cx="48" cy="48" r="16" fill="none" stroke="#ffd27a" stroke-width="2"/>''',
     ''' <circle class="gulp" cx="48" cy="48" r="16" fill="none" stroke="#b388ff" stroke-width="2"/>''')
# the heading mark
swap("themes.py", '''stroke="#ffb46b" stroke-width="5" transform="rotate(-16 48 48)"/>\'
            \'<circle cx="48" cy="48" r="24" fill="#000"/><circle cx="48" cy="48" r="26" fill="none" stroke="#ffe6b0" stroke-width="4"/></svg>\'''',
     '''stroke="#a66bff" stroke-width="5" transform="rotate(-16 48 48)"/>\'
            \'<circle cx="48" cy="48" r="24" fill="#000"/><circle cx="48" cy="48" r="26" fill="none" stroke="#ff8a3d" stroke-width="4"/></svg>\'''')

# share.py: icon version = hash of the icon bytes
swap("share.py", '''ICON_VER = f"?v={themes.current()['icon_module']}" if themes.current().get("icon_module") else ""''',
     '''# 2026-11-23 evening: a hash of the icon BYTES (was the module name, which stayed the same when the picture changed
# to purple/black, so phones would have kept the cached icon). atomic: still "" (its manifest stays byte-identical).
ICON_VER = (f"?v={hashlib.sha256(importlib.import_module(themes.current()['icon_module']).ICON_192).hexdigest()[:10]}"
            if themes.current().get("icon_module") else "")''')
if "\nimport hashlib\n" not in files["share.py"]:
    swap("share.py", "import importlib  # T0942 step 3: icon module per theme\n",
         "import hashlib\nimport importlib  # T0942 step 3: icon module per theme\n")

# Bug found in the 11-23 evening screenshot: "Nothing waiting to send." still showed "0%". #nums{display:flex} (and
# the Drop page's .nums) beat the browser's own [hidden] rule, so setting .hidden did nothing. An author rule fixes it.
files["atomic_ui.py"] = (folder / "atomic_ui.py").read_text(encoding="utf-8")
swap("atomic_ui.py", "*{box-sizing:border-box}html,body{", "[hidden]{display:none!important}*{box-sizing:border-box}html,body{")
files["portal.py"] = (folder / "portal.py").read_text(encoding="utf-8")
swap("portal.py", "*{box-sizing:border-box}\nbody{",
     "*{box-sizing:border-box}\n[hidden]{display:none!important}   /* .nums{display:flex} beat the UA [hidden] rule (11-23) */\nbody{")

# test_ui.py (the screens test) checked the old navy values; it follows the palette now
files["test_ui.py"] = (folder / "test_ui.py").read_text(encoding="utf-8")
swap("test_ui.py", ':root:not(#_){--ink:#e8ecff', ':root:not(#_){--ink:#efe9ff')
swap("test_ui.py", 'and "#0b1026" in result', 'and "#07050d" in result')

for name, text in files.items():
    compile(text, name, "exec")
    (folder / name).write_text(text, encoding="utf-8")
icon_src = sys.argv[2] if len(sys.argv) > 2 else str(Path.home() / "sandbox/horizon/purple-src/icons_png_horizon.py")
shutil.copyfile(icon_src, folder / "icons_png_horizon.py")
print("patched: themes.py, share.py, icons_png_horizon.py (purple / black / event-horizon orange)")
