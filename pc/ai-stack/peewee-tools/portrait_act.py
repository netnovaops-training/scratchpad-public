"""The page's own opening: his face, and the three lines under it.

JASON, 2026-09-23: "For Peewee there's no picture like it is on his page.
I want it exactly as is on this page with added my new information of these
new videos that are four or five seconds each."

WHAT WAS MISSING. The film went straight from the insignia into the map.
The site does not: press Listen Live and you get his PORTRAIT, in a gold
ring, over "On the air", with his name and three lines of credit under it
-- and only then does it fade to the map. That card is the first thing
anybody sees of him, and the film did not have it at all.

NOT A NEW DESIGN. Every part of this is lifted from index.html rather than
invented: intro/portrait-bw.png and portrait-color.png are the two images
the page cross-fades, the gold ring is its border, "On the air" is its
`.live` line, the name is its `.who` with Edward and Harris in amber, and
the three credits are its `.story-lines` verbatim, down to which words are
bold. Jason asked for it exactly as it is on the page, so nothing here is
an improvement on it.

THE RESURRECTION IS THE POINT, and it is the page's idea, not mine. It
holds the black-and-white photograph first and warms it into colour while
he is introduced -- index.html's own comment calls it "black-and-white into
living colour". The film would lose the whole gesture by showing the colour
copy from the start.

FOUR OR FIVE SECONDS EACH, as asked: the portrait and name land first, then
the three credit lines arrive one at a time and stay. Nothing is taken away
once shown -- a fact that flashes past is a fact nobody read.

SILENT, and deliberately so. It sits between the insignia and the map, and
the soundtrack is delayed past both.
"""
from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

# index.html's variables, read off the page rather than picked.
BAKELITE_DEEP = (26, 18, 13)
GLOW = (42, 31, 22)          # the radial it sits in
AMBER = (232, 163, 61)
CREAM = (243, 233, 215)
CREAM_DIM = (201, 183, 153)
ONAIR = (216, 74, 58)        # the .live dot and label
BRASS = (156, 122, 60)

SS = 2

PORTRAIT_IN = 1.2            # the ring draws itself
WARM = 3.4                   # black-and-white into colour, over this long
NAME_AT = 2.2
LINE_EVERY = 4.5             # "four or five seconds each"
HOLD = 2.6                   # after the last line, before the map
TOTAL = NAME_AT + 1.4 + LINE_EVERY * 3 + HOLD

# The .story-lines block, verbatim. (text, which words are amber)
CREDITS = [
    ("1923-1992  ·  Albany's FIRST Black radio host", "1923-1992"),
    ("WABY  ·  twenty-two years behind the microphone", "WABY"),
    ("WXKW  ·  music director  ·  Ten Eyck Record Shop  ·  proprietor",
     "WXKW"),
]


def _ease(t):
    return t * t * (3 - 2 * t)


class PortraitAct:
    def __init__(self, site, w, h):
        self.w, self.h = w, h
        self.W, self.H = w * SS, h * SS
        self.total = TOTAL
        intro = site / "intro"
        self.bw = Image.open(intro / "portrait-bw.png").convert("RGB")
        self.clr = Image.open(intro / "portrait-color.png").convert("RGB")
        self._bg = None
        self._fonts = {}

    def font(self, px, bold=False):
        key = (px, bold)
        if key not in self._fonts:
            f = None
            for n in (("arialbd.ttf", "seguisb.ttf") if bold
                      else ("consola.ttf", "cour.ttf", "arial.ttf")):
                try:
                    f = ImageFont.truetype(n, px)
                    break
                except OSError:
                    continue
            self._fonts[key] = f or ImageFont.load_default()
        return self._fonts[key]

    def background(self):
        if self._bg is None:
            import math
            bg = Image.new("RGB", (self.W, self.H), BAKELITE_DEEP)
            px = bg.load()
            cx, cy = self.W * 0.5, self.H * 0.42
            r = max(self.W, self.H) * 0.70
            for y in range(0, self.H, 2):
                for x in range(0, self.W, 2):
                    t = min(1.0, math.hypot(x - cx, y - cy) / r) ** 2
                    c = tuple(int(GLOW[i] + (BAKELITE_DEEP[i] - GLOW[i]) * t)
                              for i in range(3))
                    for dy in (0, 1):
                        for dx in (0, 1):
                            if x + dx < self.W and y + dy < self.H:
                                px[x + dx, y + dy] = c
            self._bg = bg
        return self._bg.copy()

    def _tracked(self, d, text, font, cx, y, fill, track):
        """Letter-spaced text. The page tracks these lines out to .12em and
        .2em; Pillow has no letter-spacing, so it is drawn glyph by glyph."""
        widths = [d.textbbox((0, 0), c, font=font)[2] for c in text]
        total = sum(widths) + track * (len(text) - 1)
        x = cx - total / 2
        for c, cw in zip(text, widths):
            d.text((x, y), c, font=font, fill=fill)
            x += cw + track
        return total

    def frame(self, t):
        im = self.background()
        W, H = self.W, self.H
        cx = W / 2

        # --- the portrait, in its gold ring ------------------------------
        ring_r = int(min(W, H) * 0.20)
        cy = int(H * 0.30)
        appear = _ease(min(1.0, t / PORTRAIT_IN))
        if appear > 0.01:
            r = int(ring_r * appear)
            # Square crop from the top of the frame, the way the page's
            # object-position does it -- the face is not in the middle.
            src_bw = self._crop_face(self.bw, r * 2)
            src_cl = self._crop_face(self.clr, r * 2)
            warm = _ease(max(0.0, min(1.0, (t - 0.6) / WARM)))
            face = Image.blend(src_bw, src_cl, warm)

            mask = Image.new("L", face.size, 0)
            ImageDraw.Draw(mask).ellipse([0, 0, face.size[0] - 1,
                                          face.size[1] - 1], fill=255)
            im.paste(face, (int(cx - r), cy - r), mask)

            d = ImageDraw.Draw(im, "RGBA")
            d.ellipse([cx - r, cy - r, cx + r, cy + r],
                      outline=BRASS + (int(235 * appear),),
                      width=max(2, int(min(W, H) * 0.004)))

        d = ImageDraw.Draw(im, "RGBA")
        y = cy + ring_r + int(H * 0.075)

        # --- "On the air" -------------------------------------------------
        if t > NAME_AT - 0.5:
            a = int(255 * min(1.0, (t - NAME_AT + 0.5) / 0.7))
            f = self.font(int(H * 0.021))
            self._tracked(d, "●  ON THE AIR", f, cx, y,
                          ONAIR + (a,), int(H * 0.010))
            y += int(H * 0.055)

        # --- the name -----------------------------------------------------
        if t > NAME_AT:
            a = int(255 * min(1.0, (t - NAME_AT) / 0.8))
            f = self.font(int(H * 0.062), bold=True)
            # Edward and Harris in amber, "Pee-Wee" in cream: the page's
            # own .gold spans, not a colour picked here.
            parts = [("EDWARD ", AMBER), ("“PEE-WEE” ", CREAM),
                     ("HARRIS", AMBER)]
            widths = [d.textbbox((0, 0), s, font=f)[2] for s, _ in parts]
            x = cx - sum(widths) / 2
            for (s, col), wpx in zip(parts, widths):
                d.text((x, y), s, font=f, fill=col + (a,))
                x += wpx
            y += int(H * 0.105)

        # --- the three credit lines, one every four and a half seconds ----
        start = NAME_AT + 1.4
        f = self.font(int(H * 0.0235))
        for i, (line, lead) in enumerate(CREDITS):
            at = start + i * LINE_EVERY
            if t < at:
                break
            a = int(255 * min(1.0, (t - at) / 0.9))
            head, rest = line.split("  ·  ", 1)
            hw = self._measure(d, head, f, int(H * 0.008))
            rw = self._measure(d, "  ·  " + rest, f, int(H * 0.008))
            x = cx - (hw + rw) / 2
            self._tracked(d, head, f, x + hw / 2, y, AMBER + (a,),
                          int(H * 0.008))
            self._tracked(d, "  ·  " + rest, f, x + hw + rw / 2, y,
                          CREAM_DIM + (a,), int(H * 0.008))
            y += int(H * 0.048)

        im = im.resize((self.w, self.h), Image.LANCZOS)

        # Up out of black at the start, down into it for the map.
        if t < 0.8:
            im = Image.blend(Image.new("RGB", im.size, (0, 0, 0)), im, t / 0.8)
        elif t > self.total - 1.0:
            k = max(0.0, 1.0 - (t - (self.total - 1.0)) / 1.0)
            im = Image.blend(Image.new("RGB", im.size, (0, 0, 0)), im, k)
        return im

    @staticmethod
    def _measure(d, text, font, track):
        ws = [d.textbbox((0, 0), c, font=font)[2] for c in text]
        return sum(ws) + track * (len(text) - 1)

    @staticmethod
    def _crop_face(src, size):
        """A square from the upper part of the portrait.

        index.html sets object-position 50% 10.5% on this image, which is
        its way of saying the head is near the top and centring the crop
        would cut it. Same number used here.
        """
        w, h = src.size
        side = min(w, h)
        left = (w - side) // 2
        top = int((h - side) * 0.105)
        return src.crop((left, top, left + side, top + side)) \
                  .resize((size, size), Image.LANCZOS)
