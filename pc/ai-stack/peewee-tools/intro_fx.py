"""The cold open: an atom, then the supernova, then the wave going out.

JASON, 2026-09-23: "Can we put an atom and super Nova as the intro that I
want to approve first."

THE ARC, AND WHY IT IS THIS ONE. An atom is a nucleus with something going
round it. A supernova is that structure failing and throwing everything it
had outward, and what leaves is a wave that keeps travelling long after the
thing that made it has gone. That is the story: a man in a small studio,
and a voice that outlived him. The last beat is deliberately the same shape
as a transmitter -- concentric rings leaving a point -- because for
twenty-two years that is literally what he was.

FOUR BEATS, cut so the turn lands on the flash:

  0.0 - 2.6   THE ATOM. Three shells drawn in, an electron on each, the
              nucleus steady. Nothing dramatic; it has to be calm first or
              the break means nothing.
  2.6 - 3.8   THE COLLAPSE. The orbits tighten and speed up, the shells
              dim, the nucleus overloads.
  3.8 - 4.2   THE FLASH. Four frames of white, then falling.
  4.2 - 8.0   THE WAVE. Debris thrown outward, and behind it the rings
              that keep going after the light has gone.

DRAWN AT 2x AND REDUCED, same as mapfilm -- Pillow will not antialias a
circle and a hard-edged orbit ring looks like clip art.

THE PALETTE IS THE PAGE'S, not a space palette. Amber #e8a33d, brass
#9c7a3c, cream #f3e9d7 on bakelite #1a120d. A blue-white astronomy
supernova would be a beautiful thing that belongs to a different piece of
work.

ADDITIVE LIGHT, NOT PAINTED LIGHT. Every glow is composited by adding
channels rather than by drawing a paler colour, so overlapping rays build
toward white the way real light does. Drawing them opaque gives flat discs
that cover each other.
"""
from __future__ import annotations

import math
import random

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# THE PALETTE IS A PARAMETER, NOT A FACT. This is a mark that will sit in
# front of more than one project, and the second one will not be bakelite
# and amber. Everything the animation draws takes its colour from here, so
# a new project is four values and no edits below.
BAKELITE = (26, 18, 13)
BRASS = (156, 122, 60)
AMBER = (232, 163, 61)
CREAM = (243, 233, 215)

SS = 2
FPS = 30

# The beats, as fractions of the whole, so the mark can run at any length
# and keep its rhythm. Eight seconds in front of a feature; four in front
# of something short. The flash always lands 52% of the way in.
ATOM_F, COLLAPSE_F, FLASH_F = 0.325, 0.475, 0.525
TOTAL = 8.0

ATOM_END = ATOM_F * TOTAL
COLLAPSE_END = COLLAPSE_F * TOTAL
FLASH_END = FLASH_F * TOTAL

SHELLS = ((1.00, 0.0), (0.74, 62.0), (0.52, -47.0))   # radius, tilt degrees

# Appears as the wave settles, or None for the symbol alone. Two lines:
# the name, then what it is. The business has no name yet -- when it has
# one, this is the single place it goes.
WORDMARK = ("SITE OWNER", "")


def _ease(t):
    return t * t * (3 - 2 * t)


class Intro:
    def __init__(self, w, h, seed=7, total=None, wordmark=WORDMARK,
                 palette=None):
        self.total = float(total or TOTAL)
        self.atom_end = ATOM_F * self.total
        self.collapse_end = COLLAPSE_F * self.total
        self.flash_end = FLASH_F * self.total
        self.wordmark = wordmark
        (self.bg, self.brass, self.amber,
         self.cream) = palette or (BAKELITE, BRASS, AMBER, CREAM)
        self.w, self.h = w, h
        self.W, self.H = w * SS, h * SS
        self.cx, self.cy = self.W / 2, self.H / 2
        self.unit = min(self.W, self.H) * 0.30       # the outer shell's radius
        rnd = random.Random(seed)
        # The debris, decided once so it is the same every render: each
        # fragment gets an angle, a speed and a size. Regenerating per
        # frame would make it boil rather than fly.
        self.debris = [(rnd.uniform(0, math.tau),
                        rnd.uniform(0.35, 1.0) ** 0.7,
                        rnd.uniform(0.6, 2.4),
                        rnd.uniform(0.5, 1.0))
                       for _ in range(220)]

    # -- helpers ------------------------------------------------------------
    def _canvas(self):
        return Image.new("RGB", (self.W, self.H), self.bg)

    def _glow(self, draw_fn, radius):
        """Draw on black, blur, and hand it back to be added as light."""
        layer = Image.new("RGB", (self.W, self.H), (0, 0, 0))
        draw_fn(ImageDraw.Draw(layer, "RGBA"))
        if radius:
            layer = layer.filter(ImageFilter.GaussianBlur(radius))
        return layer

    @staticmethod
    def _lighten(base, layer):
        from PIL import ImageChops
        return ImageChops.add(base, layer)

    def _ellipse_points(self, r, tilt, n=180):
        """One electron shell: a circle seen at an angle, which is an
        ellipse. Minor axis 0.34 -- flat enough to read as a plane rather
        than a ring, which is the whole visual difference between an atom
        and a target."""
        a = math.radians(tilt)
        ca, sa = math.cos(a), math.sin(a)
        pts = []
        for i in range(n + 1):
            t = i / n * math.tau
            x, y = math.cos(t) * r, math.sin(t) * r * 0.34
            pts.append((self.cx + x * ca - y * sa, self.cy + x * sa + y * ca))
        return pts

    def _electron_at(self, r, tilt, phase):
        a = math.radians(tilt)
        ca, sa = math.cos(a), math.sin(a)
        x, y = math.cos(phase) * r, math.sin(phase) * r * 0.34
        return (self.cx + x * ca - y * sa, self.cy + x * sa + y * ca)

    # -- one frame ----------------------------------------------------------
    def frame(self, t):
        im = self._canvas()

        if t < self.collapse_end:
            im = self._atom(im, t)
        elif t < self.flash_end:
            im = self._flash(im, t)
        else:
            im = self._wave(im, t)

        return im.resize((self.w, self.h), Image.LANCZOS)

    def _atom(self, im, t):
        # Draw in: each shell arrives in turn over the first second.
        collapsing = t > self.atom_end
        ct = ((t - self.atom_end) / (self.collapse_end - self.atom_end)) if collapsing else 0.0
        shrink = 1.0 - 0.55 * _ease(ct)
        spin = 1.0 + 7.0 * ct * ct

        def shells(d):
            for i, (rf, tilt) in enumerate(SHELLS):
                born = min(1.0, max(0.0, (t - i * 0.34) / 0.8))
                if born <= 0:
                    continue
                r = self.unit * rf * shrink
                pts = self._ellipse_points(r, tilt)
                cut = max(2, int(len(pts) * _ease(born)))
                fade = int(150 * born * (1.0 - 0.75 * ct))
                d.line(pts[:cut], fill=self.brass + (fade,),
                       width=max(1, int(self.unit * 0.008)))

        def lights(d):
            # the nucleus
            base = self.unit * 0.085
            r = base * (1 + 0.9 * ct * ct)
            for k, alpha in ((2.6, 40), (1.7, 90), (1.0, 255)):
                rr = r * k
                d.ellipse([self.cx - rr, self.cy - rr, self.cx + rr, self.cy + rr],
                          fill=(self.cream if k == 1.0 else self.amber) + (alpha,))
            # the electrons
            for i, (rf, tilt) in enumerate(SHELLS):
                born = min(1.0, max(0.0, (t - i * 0.34) / 0.8))
                if born <= 0:
                    continue
                ph = (t * (1.5 + i * 0.55) * spin) + i * 2.1
                x, y = self._electron_at(self.unit * rf * shrink, tilt, ph)
                er = self.unit * 0.030
                for k, alpha in ((3.0, 55), (1.0, 255)):
                    rr = er * k
                    d.ellipse([x - rr, y - rr, x + rr, y + rr],
                              fill=(self.amber if k > 1 else self.cream)
                              + (int(alpha * born),))

        im = self._lighten(im, self._glow(shells, self.unit * 0.012))
        im = self._lighten(im, self._glow(lights, self.unit * 0.05))
        if ct > 0.55:                 # the overload, just before it goes
            hot = int(190 * ((ct - 0.55) / 0.45) ** 2)
            im = self._lighten(im, Image.new("RGB", (self.W, self.H),
                                             (hot // 3, hot // 5, hot // 9)))
        return im

    def _flash(self, im, t):
        k = (t - self.collapse_end) / (self.flash_end - self.collapse_end)
        # Up in two frames, down over the rest. A flash that fades in is
        # not a flash.
        v = min(1.0, k / 0.18) if k < 0.18 else (1.0 - (k - 0.18) / 0.82) ** 1.6
        white = Image.new("RGB", (self.W, self.H),
                          tuple(int(c * v) for c in self.cream))
        return self._lighten(im, white)

    def _wave(self, im, t):
        k = (t - self.flash_end) / (self.total - self.flash_end)          # 0..1
        e = _ease(min(1.0, k))

        def debris(d):
            for ang, speed, size, life in self.debris:
                travel = e * speed * self.unit * 3.4
                if travel < self.unit * 0.05:
                    continue
                x = self.cx + math.cos(ang) * travel
                y = self.cy + math.sin(ang) * travel * 0.82
                fade = max(0.0, 1.0 - (k / life) ** 1.4)
                if fade <= 0:
                    continue
                r = self.unit * 0.006 * size
                # A streak, not a dot: drawn back toward where it came
                # from, which is what makes it read as moving.
                tail = travel * 0.10
                d.line([(x, y),
                        (x - math.cos(ang) * tail, y - math.sin(ang) * tail * 0.82)],
                       fill=self.amber + (int(230 * fade),), width=max(1, int(r * 2)))

        def rings(d):
            # THE TRANSMITTER. Three rings leaving one point, each started
            # later than the last, each thinning as it goes -- the diagram
            # on every radio licence ever issued.
            for i in range(3):
                rk = k - i * 0.16
                if rk <= 0:
                    continue
                rr = _ease(min(1.0, rk / 0.9)) * self.unit * 4.2
                fade = max(0.0, 1.0 - rk / 0.95) ** 1.2
                if fade <= 0.01:
                    continue
                d.ellipse([self.cx - rr, self.cy - rr * 0.86,
                           self.cx + rr, self.cy + rr * 0.86],
                          outline=self.amber + (int(150 * fade),),
                          width=max(1, int(self.unit * 0.016 * fade)))

        def core(d):
            # What is left behind: bright, then a cinder.
            r = self.unit * 0.30 * (1.0 - 0.86 * e)
            fade = max(0.0, 1.0 - k * 1.25)
            for kk, alpha in ((2.4, 60), (1.0, 255)):
                rr = max(1.0, r * kk)
                d.ellipse([self.cx - rr, self.cy - rr, self.cx + rr, self.cy + rr],
                          fill=(self.cream if kk == 1.0 else self.amber)
                          + (int(alpha * fade),))

        im = self._lighten(im, self._glow(rings, self.unit * 0.02))
        im = self._lighten(im, self._glow(debris, self.unit * 0.012))
        im = self._lighten(im, self._glow(core, self.unit * 0.09))

        # THE NAME ARRIVES AFTER THE NOISE, not during it. Letters set over
        # an explosion are unreadable and read as a stock template; letting
        # the debris fade first and then bringing them up out of the quiet
        # is what makes it a mark rather than an effect.
        if self.wordmark and k > 0.42:
            self._word(im, min(1.0, (k - 0.42) / 0.22))

        # The last of it falls to black so whatever follows comes up out
        # of it rather than cutting.
        if k > 0.86:
            im = Image.blend(Image.new("RGB", (self.W, self.H), (0, 0, 0)),
                             im, max(0.0, 1.0 - (k - 0.86) / 0.14))
        return im

    def _word(self, im, a):
        """The wordmark, letter-spaced and centred under the core."""
        name, sub = (list(self.wordmark) + [""])[:2]
        if not name:
            return
        d = ImageDraw.Draw(im, "RGBA")
        H = self.H

        def font(px, bold):
            for n in (("arialbd.ttf", "seguisb.ttf") if bold
                      else ("arial.ttf", "segoeui.ttf")):
                try:
                    return ImageFont.truetype(n, px)
                except OSError:
                    continue
            return ImageFont.load_default()

        # Tracked out by hand: Pillow has no letter-spacing, and a mark set
        # solid looks like a caption. Drawn glyph by glyph instead.
        #
        # SIZED AGAINST THE SHORT EDGE, NOT THE HEIGHT. Sizing from H alone
        # is right only while the frame is landscape. Turn it portrait and
        # the type nearly doubles while the frame gets NARROWER: at
        # 1080x1920 "CUT THE FLUFF" ran off both sides and read "UT THE
        # FLUF". The short edge is what the mark is already drawn to --
        # `unit` uses it -- so the wordmark follows it.
        edge = min(self.W, H)
        f = font(int(edge * 0.055), True)
        track = int(edge * 0.022)
        widths = [d.textbbox((0, 0), c, font=f)[2] for c in name]
        total = sum(widths) + track * (len(name) - 1)

        # AND THEN MADE TO FIT WHATEVER IT IS. The short edge handles the
        # aspect ratio; it cannot know how long the name is. "SITE OWNER"
        # is twelve characters and a longer one would overflow a square
        # frame the same way. Measured, and stepped down until it sits
        # inside the frame with a real margin -- so a name is never clipped,
        # whatever it is or wherever the mark is used.
        limit = self.W * 0.86
        while total > limit and f.size > 8:
            f = font(int(f.size * 0.92), True)
            track = int(track * 0.92)
            widths = [d.textbbox((0, 0), c, font=f)[2] for c in name]
            total = sum(widths) + track * (len(name) - 1)

        x = self.cx - total / 2
        y = self.cy + self.unit * 1.15
        alpha = int(255 * a)
        for c, cw in zip(name, widths):
            d.text((x, y), c, font=f, fill=self.cream + (alpha,))
            x += cw + track

        if sub:
            fs = font(int(edge * 0.026), False)
            sw = d.textbbox((0, 0), sub, font=fs)[2]
            d.text((self.cx - sw / 2, y + H * 0.085), sub, font=fs,
                   fill=self.amber + (int(alpha * 0.85),))
