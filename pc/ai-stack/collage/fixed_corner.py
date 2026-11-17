"""Clean the Gemini sparkle where the DETECTOR cannot see it, using position.

WHY THIS EXISTS ALONGSIDE unwatermark.py. That script hunts for the mark by
brightness and then fills its bounding box. Both halves fail on the same kind
of photograph, and an eighteen-picture album showed it plainly: five had the
sparkle sitting in the usual place over grey quilting, a painted canvas, a
pink sleeve, a blurred interior and a dark jacket, and every one was reported
clean.

THE POSITION IS A CONSTANT. Jason noticed it first and measuring bore it out
exactly: on ten of ten the mark is a 95x95 square whose bottom-right corner
sits 65 pixels in from the bottom right of the frame -- through portrait and
landscape, across widths from 1664 to 2582. Two shapes the old detector
flagged were 1335 pixels from the bottom and 1520 from the right; neither can
be reached from this corner, which is why pinning the search here is safer
than any scoring threshold.

MAKING THE DETECTOR KEENER DOES NOT WORK -- tried and measured. Dropping its
brightness threshold from 10 to 4 and restricting it to the bottom-right
corner still found none of those five, and invented two hits elsewhere. The
glyph on a bright background simply is not brighter than its own local blur by
the margin that scoring needs. Sensitivity was the wrong lever; location was
the right one.

FILL THE STAR, NOT ITS BOX. This is the part that decides whether the result
is worth shipping. Replacing the whole 95x95 square throws away the half of it
that is real, correct, textured photograph, and no fill can reinvent quilting
or brush strokes -- so the square edge of the replaced region reads as a
diamond-shaped ghost. Measured on the same five: filling the box left a
visible ghost in every one; filling the traced glyph left a soft patch in two
and nothing to speak of in three.

OTSU TRACES THE GLYPH, not a number somebody guessed. A fixed threshold traced
52% of the box on one photograph and 82% on another -- at 82% it had stopped
following the star and started swallowing the background, which is how the
ghost came back. Otsu cuts each photograph on its own histogram of "brighter
than my local blur": cuts of 27 to 46 across the five, and a mask covering 36
to 38% of the box every time, which is what a four-pointed star inside its
bounding square actually occupies.

TWO GUARDS, because an automatic threshold can still be wrong:
  MIN_CUT     below this Otsu is splitting noise, and a photograph with no
              mark would otherwise have one invented and filled.
  MAX_COVER   above this the trace has stopped following the glyph. The fill
              is refused rather than shipped, because a bad fill looks like a
              success until somebody views it full size.

Originals are moved to .originals/ exactly as unwatermark.py does. Nothing is
written without --apply.
"""
from __future__ import annotations

import argparse
import pathlib

import cv2
import numpy as np
from PIL import Image, ImageFilter

# Measured, not assumed. See the module docstring.
INSET = 65
SIZE = 95

# Otsu's cut, in grey levels above the local blur. Observed 27-46 on real
# marks; 6 is well below any of them and well above photographic noise.
MIN_CUT = 6

# A four-pointed star fills about a third of its bounding square. Observed
# 35-38% on every real mark in the album. Past half, the trace is following
# something else.
MAX_COVER = 0.55

# ...and under this it is following nothing. Otsu ALWAYS splits its input into
# two classes, so on an already-cleaned corner it dutifully separates a handful
# of noise pixels from the rest and reports a cut of 50 with a "star" covering
# 0% of the box. That is not a faint watermark, it is an algorithm doing what
# it is told with nothing to work on. MAX_COVER caught the trace being too big
# and had no opinion about it being too small, which is how a cleaned
# photograph came back still flagged.
MIN_COVER = 0.12

# The glyph's edge is anti-aliased. A mask cut exactly to it leaves a ring of
# half-sparkle, which reads as a smudge -- worse than either extreme.
DILATE = 3

SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}


def corner_box(size):
    """The square the mark occupies, for an image of this size."""
    w, h = size
    x1, y1 = w - INSET, h - INSET
    return (x1 - SIZE, y1 - SIZE, x1, y1)


def star_mask(im, box):
    """Trace the glyph inside its box. Returns (mask, otsu_cut, coverage)."""
    w, h = im.size
    grey = im.convert("L")
    # Same radius rule as watermark.find(), so "brighter than its surroundings"
    # means the same thing in both places.
    blur = grey.filter(ImageFilter.GaussianBlur(max(8, min(w, h) // 40)))
    diff = np.asarray(grey, np.int16) - np.asarray(blur, np.int16)

    x0, y0, x1, y1 = box
    sub = np.clip(diff[y0:y1, x0:x1], 0, 255).astype(np.uint8)
    cut, binary = cv2.threshold(sub, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    if cut < MIN_CUT:
        return None, cut, 0.0

    binary = cv2.morphologyEx(
        binary, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))

    # The star is ONE connected shape. A speck of bright weave elsewhere in
    # the box is not part of it and must not be filled.
    n, lbl, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
    if n > 1:
        big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        binary = np.where(lbl == big, 255, 0).astype(np.uint8)

    cover = float((binary > 0).mean())
    binary = cv2.dilate(binary, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (DILATE * 2 + 1,) * 2))

    mask = np.zeros((h, w), np.uint8)
    mask[y0:y1, x0:x1] = binary
    return mask, int(cut), cover


def fill(im, mask, radius=8):
    """Inpaint the traced glyph. Telea propagates structure in from the edge.

    Radius 8 rather than the usual 3: the mark is ~95px across, and a narrow
    radius pulls colour from too thin a ring, which is what left a pale patch
    in the first attempt.
    """
    bgr = cv2.cvtColor(np.array(im), cv2.COLOR_RGB2BGR)
    out = cv2.inpaint(bgr, mask, radius, cv2.INPAINT_TELEA)
    return Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))


def main():
    ap = argparse.ArgumentParser(
        description="Remove the Gemini sparkle from its fixed corner position.")
    ap.add_argument("folder")
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without this, nothing is changed.")
    ap.add_argument("--radius", type=int, default=8)
    a = ap.parse_args()

    folder = pathlib.Path(a.folder)
    files = sorted(p for p in folder.iterdir()
                   if p.suffix.lower() in SUFFIXES and p.is_file())
    print("  %d image(s) in %s" % (len(files), folder))
    print("  %s" % ("WRITING" if a.apply else
                    "dry run -- nothing will be changed. Add --apply."))
    print()

    originals = folder / ".originals"
    done = skipped = 0
    for p in files:
        im = Image.open(p).convert("RGB")
        box = corner_box(im.size)
        mask, cut, cover = star_mask(im, box)

        if mask is None:
            print("  none  %-42s no glyph at the fixed box (otsu cut %d)"
                  % (p.name, cut))
            skipped += 1
            continue
        if not (MIN_COVER <= cover <= MAX_COVER):
            why = "too big" if cover > MAX_COVER else "too small -- noise, not a glyph"
            print("  skip  %-42s trace covers %.0f%% of the box, %s"
                  % (p.name, 100 * cover, why))
            skipped += 1
            continue

        print("  clean %-42s otsu %2d, star %.0f%% of box" % (p.name, cut, 100 * cover))
        done += 1
        if a.apply:
            originals.mkdir(exist_ok=True)
            if not (originals / p.name).exists():
                im.save(originals / p.name)
            fill(im, mask, a.radius).save(p)

    print()
    print("  %d cleaned, %d left alone%s"
          % (done, skipped, " (originals in .originals/)" if a.apply else ""))


if __name__ == "__main__":
    main()
