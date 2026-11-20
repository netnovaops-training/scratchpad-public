"""gen_icons.py - draw the Atomic Share atom (same as share.ICON) as PNG 192 and 512 and write icons_png.py (2026-11-18).

Why: Android Chrome only installs a share target (WebAPK) when the manifest has a PNG icon of at least 144 px;
the manifest had only icon.svg. First try rendered the SVG with gotenberg-web's screenshot route: 403 (its
lockdown blocks it), so this draws the same shapes with Pillow on the PC, 4x oversampled for smooth edges.
Shapes copied from share.ICON (viewBox 96): background #0b1026 (square: Android masks it), three ellipses
rx 34 ry 13 at 0/60/120 degrees, stroke #5ad1ff width 4, nucleus r 8 #ff9b3d.
Run on the PC: python gen_icons.py icons_png.py   (the Dockerfile copies only *.py, so PNGs ship as base64)
"""
import base64
import io
import math
import sys

from PIL import Image, ImageDraw

out = sys.argv[1]
lines = ['"""icons_png.py - PNG copies of the Atomic Share atom, made by gen_icons.py (2026-11-18). Do not edit."""']
for size in (192, 512):
    k = size * 4 / 96  # viewBox unit -> oversampled pixels
    big = Image.new("RGB", (size * 4, size * 4), "#0b1026")
    d = ImageDraw.Draw(big)
    for angle in (0, 60, 120):
        a = math.radians(angle)
        pts = [(48 + 34 * math.cos(t) * math.cos(a) - 13 * math.sin(t) * math.sin(a),
                48 + 34 * math.cos(t) * math.sin(a) + 13 * math.sin(t) * math.cos(a))
               for t in (i * 2 * math.pi / 1440 for i in range(1440))]
        # round dots along the path: d.line with joints left ragged teeth on the curve (first render 11-18)
        r = 2 * k
        for x, y in pts:
            d.ellipse([(x * k - r, y * k - r), (x * k + r, y * k + r)], fill="#5ad1ff")
    d.ellipse([(40 * k, 40 * k), (56 * k, 56 * k)], fill="#ff9b3d")
    img = big.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    img.save(out.replace(".py", f"-{size}.png"))  # preview copy, not shipped
    lines.append(f"ICON_{size} = base64.b64decode({base64.b64encode(buf.getvalue()).decode()!r})")
    print(size, len(buf.getvalue()), "bytes")
open(out, "w").write("import base64\n\n" + "\n".join(lines) + "\n")
