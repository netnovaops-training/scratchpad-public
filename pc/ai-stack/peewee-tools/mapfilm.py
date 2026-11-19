"""The map act: Albany under a camera that flies where the story goes.

WHAT THIS IS. index.html draws data/albany.json as 2,800 SVG paths and
then moves ONE transform over them -- "#mapStage is the camera; it flies,
nothing here is redrawn." That works in a browser and cannot be recorded
from one, so this reimplements the same camera as a renderer: same
geography, same palette, same four places, drawn frame by frame.

THE GEOGRAPHY IS NOT REDRAWN OR RE-PROJECTED, and getting the right file
matters more than it looks. There are TWO maps here:

  * data/albany.json -- mapdata.py, bbox 42.6395/-73.7595 to 42.656/-73.7405.
    Downtown Albany around South Pearl Street. A few blocks.
  * intro/albany-region.svg -- mapregion.py, bbox 42.625/-73.900 to
    42.779/-73.600. The whole corridor: Albany, Troy six miles up the
    Hudson, and Colonie out west.

#regionPic loads the SECOND one, and places.json is projected with the
SECOND one's bounds. Reading the first and plotting those pins on it puts
WXKW -- which is in Troy, outside downtown Albany entirely -- somewhere in
the middle of Albany, on a map that does not contain it. The first version
here did exactly that, and it looked plausible: a map, four pins, nothing
obviously wrong. This reads the SVG the page reads.

WHY VECTOR PER FRAME AND NOT ONE BIG BITMAP. The obvious shortcut is to
render the map once, enormous, and crop a window out of it per frame. To
stay sharp at the closest shot -- about 110 units of map height filling
1080 lines -- that bitmap would need to be nearly 10,000 pixels tall, which
is 200MB resident before a single frame is drawn. Re-drawing the visible
polylines instead costs a few milliseconds a frame, uses almost nothing,
and is exactly sharp at every zoom rather than only at one.

CULLING IS WHAT MAKES THAT AFFORDABLE. Each polyline's bounding box is
computed once, up front. At the closest shots that discards well over 90%
of the geometry before any drawing happens, so the frames that show the
most detail are the cheapest to draw.

DRAWN AT 2x AND REDUCED. Pillow does not antialias lines, and a one-pixel
street drawn hard looks like a fence. Drawing to twice the frame and
reducing with LANCZOS is the cheapest antialiasing there is.

THE PALETTE IS LIFTED FROM mapregion.py AND index.html, not invented --
brass #9c7a3c for the streets, a lighter #c9a36a for the majors, amber
#e8a33d for the pins and the route, on bakelite #1a120d. It has to look
like the same map because it is the same map.
"""
from __future__ import annotations

import itertools
import json
import math
import re

from PIL import Image, ImageDraw, ImageFont

# --- the map's own palette, from mapregion.py and index.html ---------------
BAKELITE = (26, 18, 13)
GLOW = (42, 31, 22)          # the radial the page puts behind the map
BRASS = (156, 122, 60)
MAJOR = (201, 163, 106)
AMBER = (232, 163, 61)
CREAM = (243, 233, 215)
CREAM_DIM = (201, 183, 153)

VIEW_W, VIEW_H = 1000.0, 700.0
SS = 2                        # supersample factor; see the note above

# How close the camera gets. In map units of visible HEIGHT: the whole
# region is 700, a place is 110 -- a few blocks, which is the scale at
# which a street name would mean something to somebody who knows Albany.
WIDE_H = 720.0
CLOSE_H = 110.0


def _read_svg(path):
    """The three drawing groups out of mapregion.py's SVG.

    A real SVG parser is not needed and would be the wrong tool: this file
    is machine-written by one script, every path is a plain M/L polyline
    with no curves, transforms or nesting, and the groups arrive in a fixed
    order. Two regexes read it in under a second; an XML tree of 5,644
    elements costs more memory than the whole render.
    """
    text = path.read_text(encoding="utf-8")
    out = []
    for g in re.finditer(r'<g ([^>]*)>(.*?)</g>', text, re.DOTALL):
        attrs, body = g.group(1), g.group(2)

        def attr(name, default="", attrs=attrs):  # bound now (B023); called only in this pass anyway
            m = re.search(name + r'="([^"]*)"', attrs)
            return m.group(1) if m else default

        stroke = attr("stroke", "#9c7a3c")
        colour = tuple(int(stroke[i:i + 2], 16) for i in (1, 3, 5))
        width = float(attr("stroke-width", "0.7"))
        opacity = float(attr("opacity", "1"))
        closed = attr("fill", "none") != "none"

        polys = []
        for d in re.finditer(r'<path d="([^"]+)"', body):
            nums = re.findall(r'(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)',
                              d.group(1))
            if len(nums) > 1:
                polys.append([(float(a), float(b)) for a, b in nums])
        out.append((polys, colour, width, closed, opacity))
    return out


def _bbox(pts):
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


class MapFilm:
    def __init__(self, site, w, h):
        self.w, self.h = w, h
        self.aspect = w / h
        p = json.loads((site / "data/places.json").read_text(encoding="utf-8"))
        self.places = {x["key"]: x for x in p["places"]}
        self.order = [x["key"] for x in p["places"]]

        # The three <g> groups come out of the file in the order
        # mapregion.py writes them -- buildings, streets, majors -- and each
        # carries its own stroke and opacity as attributes. Read from the
        # group rather than hardcoded, so restyling the map restyles this.
        self.layers = []
        for pts_list, colour, width, close, opacity in _read_svg(
                site / "intro/albany-region.svg"):
            items = [(pts, _bbox(pts)) for pts in pts_list if len(pts) > 1]
            self.layers.append((items, colour, width, close, opacity))

        self._bg = None
        self._font_cache = {}

    # -- type ---------------------------------------------------------------
    def font(self, px, bold=False):
        k = (px, bold)
        if k not in self._font_cache:
            f = None
            for name in (("arialbd.ttf", "seguisb.ttf") if bold
                         else ("arial.ttf", "segoeui.ttf")):
                try:
                    f = ImageFont.truetype(name, px)
                    break
                except OSError:
                    continue
            self._font_cache[k] = f or ImageFont.load_default()
        return self._font_cache[k]

    # -- the ground ---------------------------------------------------------
    def background(self):
        """The radial glow the page has behind the map. Built once."""
        if self._bg is None:
            w, h = self.w * SS, self.h * SS
            bg = Image.new("RGB", (w, h), BAKELITE)
            px = bg.load()
            cx, cy = w * 0.5, h * 0.42
            r = max(w, h) * 0.72
            for y in range(0, h, 2):
                for x in range(0, w, 2):
                    t = min(1.0, math.hypot(x - cx, y - cy) / r)
                    t = t * t
                    c = tuple(int(GLOW[i] + (BAKELITE[i] - GLOW[i]) * t)
                              for i in range(3))
                    px[x, y] = c
                    if x + 1 < w:
                        px[x + 1, y] = c
                    if y + 1 < h:
                        px[x, y + 1] = c
                        if x + 1 < w:
                            px[x + 1, y + 1] = c
            self._bg = bg
        return self._bg.copy()

    # -- one frame ----------------------------------------------------------
    def frame(self, cam, lit, card=None, card_alpha=1.0, fade=1.0):
        """cam is (centre x, centre y, visible height) in map units."""
        cx, cy, vh = cam
        vw = vh * self.aspect
        _w, h = self.w * SS, self.h * SS
        k = h / vh                                  # pixels per map unit
        x0, y0 = cx - vw / 2, cy - vh / 2

        def T(p):
            return ((p[0] - x0) * k, (p[1] - y0) * k)

        im = self.background()
        d = ImageDraw.Draw(im, "RGBA")

        vx1, vy1 = x0 + vw, y0 + vh
        for items, colour, width, close, opacity in self.layers:
            lw = max(1, round(width * k / 6))
            # The group's own opacity, applied as alpha rather than by
            # mixing with the background, so the glow still shows through.
            alpha = int(255 * opacity)
            col = colour + (alpha,)
            for pts, bb in items:
                if bb[2] < x0 or bb[0] > vx1 or bb[3] < y0 or bb[1] > vy1:
                    continue
                xy = [T(p) for p in pts]
                if close:
                    d.polygon(xy, fill=colour + (int(alpha * 0.25),))
                d.line(xy + ([xy[0]] if close else []), fill=col, width=lw,
                       joint="curve")

        # --- the route between the four places, dashed like the page's -----
        route = [self.places[k2] for k2 in self.order if k2 in self.places]
        for a, b in itertools.pairwise(route):
            self._dashed(d, T((a["x"], a["y"])), T((b["x"], b["y"])),
                         AMBER + (110,), max(1, round(1.4 * k / 6)))

        # --- pins ----------------------------------------------------------
        for key, pl in self.places.items():
            if key not in lit:
                continue
            x, y = T((pl["x"], pl["y"]))
            r = max(3.0, 3.4 * k / 6)
            d.ellipse([x - r, y - r, x + r, y + r], fill=AMBER + (255,))
            hr = r * 3.4
            d.ellipse([x - hr, y - hr, x + hr, y + hr],
                      outline=AMBER + (140,), width=max(1, int(r * 0.42)))

        im = im.resize((self.w, self.h), Image.LANCZOS)

        if card and card_alpha > 0.01:
            self._card(im, card, card_alpha)
        if fade < 1.0:
            im = Image.blend(Image.new("RGB", im.size, (0, 0, 0)), im, fade)
        return im

    def _dashed(self, d, a, b, colour, width, dash=6, gap=5):
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux, uy = dx / length, dy / length
        step = (dash + gap) * SS
        t = 0.0
        while t < length:
            e = min(length, t + dash * SS)
            d.line([(a[0] + ux * t, a[1] + uy * t),
                    (a[0] + ux * e, a[1] + uy * e)], fill=colour, width=width)
            t += step

    def _card(self, im, place, alpha):
        """The card that names wherever the camera has landed."""
        d = ImageDraw.Draw(im, "RGBA")
        h = self.h
        pad = int(h * 0.045)
        f_name = self.font(int(h * 0.046), bold=True)
        f_where = self.font(int(h * 0.024))
        f_line = self.font(int(h * 0.026))

        lines = [(place["name"], f_name, CREAM),
                 (place["where"], f_where, CREAM_DIM),
                 (place["line"], f_line, AMBER)]
        widths = [d.textbbox((0, 0), t, font=f)[2] for t, f, _ in lines]
        heights = [int(d.textbbox((0, 0), t, font=f)[3] * 1.55) for t, f, _ in lines]
        bw = max(widths) + pad * 2
        bh = sum(heights) + pad * 2

        x, y = pad, h - bh - pad
        a = int(255 * alpha)
        d.rounded_rectangle([x, y, x + bw, y + bh], radius=int(h * 0.012),
                            fill=(26, 18, 13, int(a * 0.88)),
                            outline=(156, 122, 60, a), width=2)
        ty = y + pad
        for (t, f, col), hh in zip(lines, heights):
            d.text((x + pad, ty), t, font=f, fill=col + (a,))
            ty += hh


def ease(t):
    """Slow out, slow in. A camera that starts and stops abruptly reads as
    a cut with extra steps."""
    return t * t * (3 - 2 * t)


def cam_for(film, key):
    if key and key in film.places:
        p = film.places[key]
        return (p["x"], p["y"], CLOSE_H)
    return (VIEW_W / 2, VIEW_H / 2, WIDE_H)


def interp(a, b, t):
    """Pan and zoom together, with the ZOOM interpolated in log space.

    Linear zoom pulls the far end of a long flight past the subject and
    then back -- the classic 'dolly overshoot'. Geometric interpolation is
    what makes a move from 720 units down to 110 feel like one gesture.
    """
    t = ease(t)
    vh = a[2] * (b[2] / a[2]) ** t
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, vh)
