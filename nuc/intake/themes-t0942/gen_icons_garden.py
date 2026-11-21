"""gen_icons_garden.py - the Garden Drop app icon (T0942 step 3, 2026-11-21): a grown plant in flower.

    python gen_icons_garden.py icons_png_garden.py     (run on the PC; needs Pillow)

Writes icons_png_garden.py with ICON_SVG (served at /icon.svg) and ICON_192 / ICON_512 (PNG, base64; the
Dockerfile copies only *.py). Both are drawn from the ONE shape list below, so the SVG and the PNGs cannot drift.
Why PNGs at all: Android Chrome installs a share target (WebAPK) only with a PNG icon of 144 px or more
(gen_icons.py, 11-18). Preview copies icons_png_garden-192/512.png are written next to the output, not shipped.

Maskable: Android crops the icon to a circle/squircle, keeping only the middle 80 % (radius 38.4 of 96). Every
shape stays inside that circle; SAFE_R below is checked on every point before anything is written.
Variables: BG (square background, same as the theme's theme_color), SOIL, STEM, LEAVES (start point, direction
+1 right / -1 left, colour), PETALS / CENTRE (the flower), SAFE_R, OVERSAMPLE (draw 4x, shrink = smooth edges).
"""
import base64
import io
import math
import pathlib
import sys

from PIL import Image, ImageDraw

BG = "#0c1f15"
SOIL = (48, 80, 18, 4.5, "#3b2a1a")                       # cx, cy, rx, ry, colour
STEM = ((48, 80), (46, 70), (51, 60), (48, 50), (45, 42), (50, 34), (48, 27))  # two cubic curves, as in the SVG
STEM_W, STEM_C = 3.4, "#4fbf74"
LEAF = ((6, -5), (15, -4), (19, 0), (-4, 4), (-13, 5), (-19, 0))  # the theme's #lf leaf: two relative curves
LEAVES = (((48, 66), 1, "#6fd38b"), ((48, 57), -1, "#4fbf74"), ((48, 47), 1, "#8fe3a8"), ((48, 39), -1, "#6fd38b"))
FLOWER = (48, 24)
PETALS = [(FLOWER[0] + 7 * math.cos(math.radians(a)), FLOWER[1] + 7 * math.sin(math.radians(a)), 5.5,
           "#ffb3c7" if i % 2 == 0 else "#ffc4d3") for i, a in enumerate(range(-90, 270, 72))]
CENTRE = (FLOWER[0], FLOWER[1], 4.2, "#fde68a")
SAFE_R = 38.4
OVERSAMPLE = 4


def bezier(p0, p1, p2, p3, n=200):
    """Points along one cubic Bezier curve (absolute coordinates)."""
    for i in range(n + 1):
        t = i / n
        u = 1 - t
        yield (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
               u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1])


def stem_points():
    for i in range(0, len(STEM) - 1, 3):
        yield from bezier(*STEM[i:i + 4])


def leaf_points(start, side):
    """The leaf outline: two relative curves from `start`, mirrored for side -1."""
    x0, y0 = start
    c = [(x0 + side * dx, y0 + dy) for dx, dy in LEAF]
    pts = list(bezier((x0, y0), c[0], c[1], c[2]))
    end = c[2]
    back = [(end[0] + side * dx, end[1] + dy) for dx, dy in LEAF[3:]]
    pts += list(bezier(end, back[0], back[1], back[2]))
    return pts


def check_safe():
    """Refuse to write an icon whose shapes poke out of Android's maskable safe circle."""
    pts = list(stem_points())
    for start, side, _ in LEAVES:
        pts += leaf_points(start, side)
    for x, y, r, _ in PETALS + [CENTRE]:
        pts += [(x + r * math.cos(a / 10), y + r * math.sin(a / 10)) for a in range(63)]
    cx, cy, rx, ry, _ = SOIL
    pts += [(cx + rx * math.cos(a / 10), cy + ry * math.sin(a / 10)) for a in range(63)]
    worst = max(math.hypot(x - 48, y - 48) for x, y in pts)
    if worst > SAFE_R:
        raise SystemExit(f"a shape reaches {worst:.1f} from the centre; the maskable safe circle is {SAFE_R}")
    return worst


def svg():
    """The same shapes as SVG text (quoted values only: an unquoted value before /> breaks the element)."""
    cx, cy, rx, ry, sc = SOIL
    s = STEM
    stem_d = (f"M{s[0][0]},{s[0][1]}C{s[1][0]},{s[1][1]} {s[2][0]},{s[2][1]} {s[3][0]},{s[3][1]}"
              f"C{s[4][0]},{s[4][1]} {s[5][0]},{s[5][1]} {s[6][0]},{s[6][1]}")
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><rect width="96" height="96" rx="20" fill="{BG}"/>',
             f'<ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="{sc}"/>',
             f'<path d="{stem_d}" fill="none" stroke="{STEM_C}" stroke-width="{STEM_W}" stroke-linecap="round"/>']
    for (x0, y0), side, col in LEAVES:
        a, b, c, d, e, f = [(side * dx, dy) for dx, dy in LEAF]
        parts.append(f'<path d="M{x0},{y0}c{a[0]},{a[1]} {b[0]},{b[1]} {c[0]},{c[1]}'
                     f'c{d[0]},{d[1]} {e[0]},{e[1]} {f[0]},{f[1]}z" fill="{col}"/>')
    for x, y, r, col in PETALS + [CENTRE]:
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{r}" fill="{col}"/>')
    return "".join(parts) + "</svg>"


def png(size):
    k = size * OVERSAMPLE / 96
    big = Image.new("RGB", (size * OVERSAMPLE,) * 2, BG)
    d = ImageDraw.Draw(big)
    cx, cy, rx, ry, sc = SOIL
    d.ellipse([((cx - rx) * k, (cy - ry) * k), ((cx + rx) * k, (cy + ry) * k)], fill=sc)
    r = STEM_W / 2 * k  # round dots along the curve, as gen_icons.py does (plain lines left ragged joints)
    for x, y in stem_points():
        d.ellipse([(x * k - r, y * k - r), (x * k + r, y * k + r)], fill=STEM_C)
    for start, side, col in LEAVES:
        d.polygon([(x * k, y * k) for x, y in leaf_points(start, side)], fill=col)
    for x, y, rr, col in PETALS + [CENTRE]:
        d.ellipse([((x - rr) * k, (y - rr) * k), ((x + rr) * k, (y + rr) * k)], fill=col)
    img = big.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return img, buf.getvalue()


if __name__ == "__main__":
    out = sys.argv[1]
    print(f"furthest point {check_safe():.1f} of {SAFE_R} allowed")
    lines = ['"""icons_png_garden.py - the Garden Drop icon (SVG + PNG), made by gen_icons_garden.py (T0942). Do not edit."""',
             "import base64", "", f"ICON_SVG = {svg()!r}"]
    for size in (192, 512):
        img, data = png(size)
        img.save(out.replace(".py", f"-{size}.png"))
        lines.append(f"ICON_{size} = base64.b64decode({base64.b64encode(data).decode()!r})")
        print(size, len(data), "bytes")
    pathlib.Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
