"""test_hzicon.py - run INSIDE the patched intake image with a drop.toml mounted; THEME env says what to expect.

    THEME=horizon -> /icon.svg, /icon-192.png, /icon-512.png are the black-hole icon; manifest name from drop.toml
    THEME=atomic  -> the three icon routes are the old atom bytes (nothing changed for the old theme)
Variables: theme (expected theme), ok (all checks true).
"""
import os
import sys

import icons_png
import icons_png_horizon as h
import share

theme = os.environ["THEME"]
if theme == "horizon":
    ok = (share.icon().body.decode() == h.ICON_SVG and share.icon_192().body == h.ICON_192
          and share.icon_512().body == h.ICON_512)
else:
    ok = (share.icon().body == share.ICON.encode() and share.icon_192().body == icons_png.ICON_192
          and share.icon_512().body == icons_png.ICON_512)
print(f"{theme}: icons as expected: {ok} | manifest name {share.MANIFEST['name']}")
sys.exit(0 if ok else 1)
