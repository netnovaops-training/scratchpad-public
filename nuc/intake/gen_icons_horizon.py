"""gen_icons_horizon.py - the Novashare app icon (theme "horizon", 2026-11-23): a black hole with a bright photon
ring and a tilted, glowing accretion disk on the night sky, the end state of the collapsing-star animation.

    python gen_icons_horizon.py icons_png_horizon.py     (run on the PC; needs Pillow)

Writes icons_png_horizon.py with ICON_SVG (served at /icon.svg) and ICON_192 / ICON_512 (PNG, base64: the
Dockerfile copies only *.py). The SVG and the PNGs come from the ONE shape list below, so they cannot drift.
Why (Jason 2026-11-23, screenshot of the install sheet): "Still has the old logo" - the horizon theme had no icon of
its own, so the atom was served. Maskable: Android keeps only the middle 80 % (radius 38.4 of 96); check_safe()
refuses to write an icon with any point outside that circle. Depth: the BACK half of the disk is drawn first, then
the hole, then the FRONT half over it, so the disk wraps around the hole.
Variables: BG (sky, = theme_color), C (centre), TILT (disk angle), DISKS (rx, ry, width, colour per ring), HOLE_R,
RING (photon ring radius, width, colour), GLOW (soft rings: radius, colour, opacity), SAFE_R, OVERSAMPLE.
"""
import base64
import io
import math
import pathlib
import sys

from PIL import Image, ImageDraw

BG = "#07050d"   # 2026-11-23 evening (Jason: "purple and black ... orange like an event horizon"); was navy #0b1026
C = 48.0
TILT = -16.0
DISKS = [(35.0, 9.5, 3.6, "#ff8a3d"), (27.0, 6.8, 2.2, "#a66bff")]   # orange outer disk, purple inner (was cyan)
HOLE_R = 13.0
RING = (15.2, 2.2, "#ffb35c")   # the orange event-horizon ring
GLOW = [(31.0 - 1.5 * i, "#8a4dff" if i < 6 else "#ff8a3d", 0.05) for i in range(10)]   # 10 faint rings = a smooth glow (3 rings showed bands, 11-23)
SAFE_R = 38.4
OVERSAMPLE = 4


def ellipse_points(rx, ry, a0, a1, n=720):
    """Points on the tilted disk ellipse from angle a0 to a1 (radians, parameter angle)."""
    t = math.radians(TILT)
    for i in range(n + 1):
        a = a0 + (a1 - a0) * i / n
        x, y = rx * math.cos(a), ry * math.sin(a)
        yield (C + x * math.cos(t) - y * math.sin(t), C + x * math.sin(t) + y * math.cos(t))


def check_safe():
    worst = 0.0
    for rx, ry, w, _ in DISKS:
        for x, y in ellipse_points(rx, ry, 0, 2 * math.pi):
            worst = max(worst, math.hypot(x - C, y - C) + w / 2)
    worst = max(worst, RING[0] + RING[1] / 2, max(g[0] for g in GLOW))
    if worst > SAFE_R:
        raise SystemExit(f"a shape reaches {worst:.1f} from the centre; the maskable safe circle is {SAFE_R}")
    return worst


def svg():
    """The same shapes as SVG (every attribute value quoted)."""
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><rect width="96" height="96" rx="20" fill="{BG}"/>']
    for r, col, op in GLOW:
        parts.append(f'<circle cx="{C}" cy="{C}" r="{r}" fill="{col}" fill-opacity="{op}"/>')

    def arc(rx, ry, w, col, back):
        # back half = parameter angles pi..2pi (upper, behind the hole); front half = 0..pi (lower, in front)
        pts = list(ellipse_points(rx, ry, math.pi if back else 0.0, 2 * math.pi if back else math.pi, 120))
        d = "M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts)
        return f'<path d="{d}" fill="none" stroke="{col}" stroke-width="{w}" stroke-linecap="round"/>'

    parts += [arc(rx, ry, w, col, True) for rx, ry, w, col in DISKS]
    parts.append(f'<circle cx="{C}" cy="{C}" r="{HOLE_R}" fill="#000000"/>')
    parts.append(f'<circle cx="{C}" cy="{C}" r="{RING[0]}" fill="none" stroke="{RING[2]}" stroke-width="{RING[1]}"/>')
    parts += [arc(rx, ry, w, col, False) for rx, ry, w, col in DISKS]
    return "".join(parts) + "</svg>"


def png(size):
    k = size * OVERSAMPLE / 96
    big = Image.new("RGBA", (size * OVERSAMPLE,) * 2, BG)
    for r, col, op in GLOW:   # soft glow: translucent discs composited one by one
        layer = Image.new("RGBA", big.size, (0, 0, 0, 0))
        rgb = tuple(int(col[i:i + 2], 16) for i in (1, 3, 5))
        ImageDraw.Draw(layer).ellipse([((C - r) * k, (C - r) * k), ((C + r) * k, (C + r) * k)], fill=rgb + (int(255 * op),))
        big = Image.alpha_composite(big, layer)
    d = ImageDraw.Draw(big)

    def stroke(points, w, col):
        rr = w / 2 * k   # round dots along the path (as gen_icons.py: plain lines left ragged joints)
        for x, y in points:
            d.ellipse([(x * k - rr, y * k - rr), (x * k + rr, y * k + rr)], fill=col)

    for rx, ry, w, col in DISKS:
        stroke(ellipse_points(rx, ry, math.pi, 2 * math.pi), w, col)
    d.ellipse([((C - HOLE_R) * k, (C - HOLE_R) * k), ((C + HOLE_R) * k, (C + HOLE_R) * k)], fill="#000000")
    rr = RING[0]
    stroke([(C + rr * math.cos(a / 100), C + rr * math.sin(a / 100)) for a in range(629)], RING[1], RING[2])
    for rx, ry, w, col in DISKS:
        stroke(ellipse_points(rx, ry, 0.0, math.pi), w, col)
    img = big.convert("RGB").resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return img, buf.getvalue()


if __name__ == "__main__":
    out = sys.argv[1]
    print(f"furthest point {check_safe():.1f} of {SAFE_R} allowed")
    lines = ['"""icons_png_horizon.py - the Novashare icon (SVG + PNG), made by gen_icons_horizon.py. Do not edit."""',
             "import base64", "", f"ICON_SVG = {svg()!r}"]
    for size in (192, 512):
        img, data = png(size)
        img.save(out.replace(".py", f"-{size}.png"))
        lines.append(f"ICON_{size} = base64.b64decode({base64.b64encode(data).decode()!r})")
        print(size, len(data), "bytes")
    pathlib.Path(out).write_text("\n".join(lines) + "\n", encoding="utf-8")
