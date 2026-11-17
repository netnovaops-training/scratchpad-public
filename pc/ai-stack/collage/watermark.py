"""Find Gemini's sparkle and paint it out.

WHY NOT A VISION MODEL, since that was the first idea. A vision model
describes pictures; it cannot edit them, and asking one to "remove the
watermark" gets you a description of a watermark. The job is two ordinary
pieces of image processing -- find a small bright glyph, then fill the
hole plausibly -- and both run in milliseconds on a CPU with no model
loaded and no GPU contention with everything else on this box.

HOW IT FINDS THE MARK, and why not by looking for bright pixels. The
sparkle is laid over whatever is behind it, so "bright" depends entirely
on the photo -- on the sample it sits on a pale gradient and is barely
brighter than its surroundings, while the album text nearby is far
brighter and is not the watermark. What IS reliably true is that the
background varies SLOWLY and the glyph does not. Subtracting a heavily
blurred copy of the image removes anything smooth and leaves edges; the
mark survives that, and so does text, so the candidates are then judged
on shape.

THE SHAPE TEST IS WHAT SEPARATES IT FROM TEXT. A four-pointed star is
nearly square in its bounding box, fills only about a third of it (the
concave sides are the whole point), is symmetric both ways, and -- the
test that turned out to matter most -- is SOLID AT ITS CENTRE and widest
there. Most letters fail the first four: "N" is tall, fills most of its
box, and is not vertically symmetric. A capital O passes all four and is
the sparkle's twin; it fails the fifth, because the middle of an O is
empty and its column counts dip there rather than peaking. Without that
test, cleaning this album cover and running the detector again picked
the O of "COLLECTION" and would have painted out a letter.

Each candidate is scored on all five, MULTIPLIED rather than averaged: a
candidate that fails one of them is not the mark, and averaging would
let four good scores carry a disqualifying one. The best is taken only
if it clears the floor -- a photo with no watermark comes back with
nothing, and saying "I found nothing" is much better than painting over
a real feature of somebody's picture.

WHAT THIS DOES NOT DO. Gemini also embeds SynthID, an invisible
watermark in the pixel values themselves. Nothing here touches it and
nothing local can; an image cleaned by this is still detectable as
AI-generated. That is worth knowing rather than discovering.
"""
from __future__ import annotations

import math

# Where the mark is allowed to be: any corner, within this fraction of the
# image from the edges. Gemini uses the bottom-right, but a corner search
# costs nothing and survives them changing their mind.
CORNER_SPAN = 0.30

# The glyph's size as a fraction of the SHORTER side. Measured at 0.046 on
# the 1008x1058 sample; the window is wide enough for the marks Gemini
# puts on very small and very large exports.
MIN_REL = 0.015
MAX_REL = 0.12

# How much of its own bounding box a four-pointed star fills. A filled
# square is 1.0, a circle 0.79, this glyph about a third.
FILL_LO, FILL_HI = 0.18, 0.55

# Below this the candidate is not confidently a sparkle and is left alone.
SCORE_FLOOR = 0.60


def _components(mask, w, h, min_px):
    """Label connected runs of True. Iterative -- a recursive flood fill
    overflows the stack on a large mask, which is the kind of bug that
    only shows up on somebody's 48-megapixel phone photo."""
    seen = bytearray(w * h)
    out = []
    for start in range(w * h):
        if seen[start] or not mask[start]:
            continue
        stack = [start]
        seen[start] = 1
        px = []
        while stack:
            i = stack.pop()
            px.append(i)
            x, y = i % w, i // w
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1),
                           (1, 1), (1, -1), (-1, 1), (-1, -1)):
                nx, ny = x + dx, y + dy
                if 0 <= nx < w and 0 <= ny < h:
                    j = ny * w + nx
                    if mask[j] and not seen[j]:
                        seen[j] = 1
                        stack.append(j)
        if len(px) >= min_px:
            out.append(px)
    return out


def _score(px, w):
    """How much like a four-pointed sparkle is this blob?

    Five properties, each 0..1, multiplied rather than averaged: a
    candidate that fails any one of them is not the mark, and averaging
    would let four good scores carry one disqualifying one.
    """
    xs = [i % w for i in px]
    ys = [i // w for i in px]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    if bw < 3 or bh < 3:
        return 0.0, (x0, y0, x1, y1)

    # 1. Square-ish.
    square = min(bw, bh) / max(bw, bh)

    # 2. Fills about a third of its box.
    fill = len(px) / float(bw * bh)
    if not (FILL_LO <= fill <= FILL_HI):
        fillscore = 0.0
    else:
        mid = (FILL_LO + FILL_HI) / 2
        fillscore = 1.0 - abs(fill - mid) / (FILL_HI - mid)

    # 3 & 4. Symmetric left-to-right and top-to-bottom. Compared as
    # column and row profiles, which is cheap and does not care about
    # the glyph being a pixel off centre.
    cols = [0] * bw
    rows = [0] * bh
    for i in px:
        cols[i % w - x0] += 1
        rows[i // w - y0] += 1

    def sym(profile):
        n = len(profile)
        tot = sum(profile) or 1
        diff = sum(abs(profile[k] - profile[n - 1 - k]) for k in range(n // 2))
        return max(0.0, 1.0 - diff / tot)

    # 5. SOLID IN THE MIDDLE, AND WIDEST THERE. This is the test that
    # separates a sparkle from a capital O, which is otherwise its twin:
    # square in its box, hollow enough to pass the fill test, and
    # symmetric both ways. On the sample, with the real mark already
    # removed, the O of "COLLECTION" scored 0.73 and was picked as the
    # watermark -- a false positive that would have painted out a letter.
    #
    # A four-pointed star is filled at its centre and its column counts
    # peak there. An O is empty at its centre and its column counts DIP
    # there, because the middle of an O is two thin arcs where the sides
    # are full strokes.
    px_set = set(px)
    cxm, cym = (x0 + x1) // 2, (y0 + y1) // 2
    solid = 0.0
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if (cym + dy) * w + (cxm + dx) in px_set:
                solid += 1.0
    solid /= 9.0

    def peaked(profile):
        n = len(profile)
        if n < 5:
            return 0.0
        mid = profile[n // 2]
        edge = max(profile[0], profile[-1], 1)
        # The middle must dominate. A star's widest span is its waist; a
        # letter's is one of its strokes, at the edge of the box.
        return max(0.0, min(1.0, (mid / float(edge) - 0.6) / 0.8))

    middle = solid * max(peaked(cols), peaked(rows))

    return (square * fillscore * sym(cols) * sym(rows) * middle,
            (x0, y0, x1, y1))


def find(img):
    """Locate the sparkle. Returns (box, score) or (None, 0.0).

    `img` is a PIL Image. Nothing is modified.
    """
    from PIL import ImageFilter

    w, h = img.size
    short = min(w, h)
    grey = img.convert("L")
    # The radius scales with the image so the same code works on a phone
    # photo and a thumbnail. Too small and the glyph blurs into its own
    # background and disappears from the difference.
    blur = grey.filter(ImageFilter.GaussianBlur(max(8, short // 40)))
    gp, bp = grey.load(), blur.load()

    span = int(short * CORNER_SPAN)
    best, best_box = 0.0, None

    for cx, cy in ((w - span, h - span), (0, h - span), (w - span, 0), (0, 0)):
        x0, y0 = max(0, cx), max(0, cy)
        x1, y1 = min(w, x0 + span), min(h, y0 + span)
        rw, rh = x1 - x0, y1 - y0
        if rw < 8 or rh < 8:
            continue

        mask = bytearray(rw * rh)
        for y in range(rh):
            row = y * rw
            for x in range(rw):
                if gp[x0 + x, y0 + y] - bp[x0 + x, y0 + y] > 10:
                    mask[row + x] = 1

        lo = int((short * MIN_REL) ** 2 * 0.15)
        for px in _components(mask, rw, rh, max(12, lo)):
            s, (bx0, by0, bx1, by1) = _score(px, rw)
            side = max(bx1 - bx0 + 1, by1 - by0 + 1) / float(short)
            if not (MIN_REL <= side <= MAX_REL):
                continue
            if s > best:
                best = s
                best_box = (x0 + bx0, y0 + by0, x0 + bx1, y0 + by1)

    return (best_box, best) if best >= SCORE_FLOOR else (None, best)


def _texture(img, box, pad=8):
    """How much fine detail surrounds the patch.

    Distinct from the caller's own busy-check, which asks whether to do
    this at all. This asks WHICH FILL to use, and it measures neighbour-
    to-neighbour difference rather than spread: a smooth gradient has a
    wide range of values but tiny steps between adjacent pixels, and it
    is the steps that a blend can reproduce and texture cannot.
    """
    x0, y0, x1, y1 = box
    w, h = img.size
    g = img.convert("L").load()
    steps = []
    for y in (max(0, y0 - pad), min(h - 1, y1 + pad)):
        for x in range(max(1, x0 - pad), min(w - 1, x1 + pad)):
            steps.append(abs(g[x, y] - g[x - 1, y]))
    for x in (max(0, x0 - pad), min(w - 1, x1 + pad)):
        for y in range(max(1, y0 - pad), min(h - 1, y1 + pad)):
            steps.append(abs(g[x, y] - g[x, y - 1]))
    return (sum(steps) / len(steps)) if steps else 0.0


def _patch_fill(img, box, feather):
    """Copy a clean block of the same size from nearby, instead of blending.

    WHY THIS EXISTS. The blend below is invisible on a gradient and a soft
    grey rectangle on anything textured -- skin, knitted fabric, hair. The
    contact sheet for Jason's fridge album showed exactly that on one
    photo: the star gone, and a blurred patch where it had been, which is
    not an improvement.

    Texture cannot be averaged into existence, only borrowed. This takes
    the nearest block of real pixels that does not overlap the mark and
    lays it over, feathered at the edges so the seam does not show. The
    result is not the truth about what was behind the glyph -- nothing
    can be -- but it is made of the same material as its surroundings,
    which is what the eye checks.
    """
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    w, h = img.size

    # Candidate sources, nearest first: straight up, left, down, right.
    # A corner mark usually has clean material on at least two sides.
    for dx, dy in ((0, -(bh + feather * 2)), (-(bw + feather * 2), 0),
                   (0, bh + feather * 2), (bw + feather * 2, 0),
                   (-(bw + feather * 2), -(bh + feather * 2))):
        sx, sy = x0 + dx, y0 + dy
        if sx < 0 or sy < 0 or sx + bw > w or sy + bh > h:
            continue
        out = img.copy()
        patch = img.crop((sx, sy, sx + bw, sy + bh))

        # Feathered so the block does not announce itself. A hard paste of
        # real texture reads as a seam, which is its own artefact.
        from PIL import Image as _I
        mask = _I.new("L", (bw, bh), 0)
        mp = mask.load()
        for yy in range(bh):
            for xx in range(bw):
                edge = min(xx, yy, bw - 1 - xx, bh - 1 - yy)
                mp[xx, yy] = 255 if edge >= feather else int(255 * edge / feather)
        out.paste(patch, (x0, y0), mask)
        return out
    return None


def remove(img, box, feather: int = 3):
    """Paint the mark out, using what surrounds it.

    THE FILL IS A DISTANCE-WEIGHTED BLEND of the ring of pixels just
    outside the patch, which is the right method for exactly this case:
    Gemini puts the mark in a corner, and a corner of a photograph is
    nearly always smooth -- sky, a gradient, a wall. On a smooth
    background this is invisible. On a busy one it will leave a soft
    smudge, and that is the honest limit of doing this without a model.
    """
    # OPENCV FIRST, WHERE IT IS AVAILABLE. Telea's algorithm walks inward
    # from the boundary propagating both colour AND gradient direction,
    # which is the part a weighted average cannot do -- it is why a blend
    # turns knitted fabric into a grey rectangle. This is a solved problem
    # with a well-tested implementation; hand-rolling a worse one and
    # shipping it would be the actual mistake.
    #
    # THE FALLBACK IS NOT A LESSER PATH, it is the no-OpenCV path: the
    # collage container carries Pillow only, and adding a 60MB wheel to it
    # so that uploads can inpaint marginally better is not a good trade
    # when the alternative runs fine on the smooth corners that uploads
    # mostly have. The batch tool runs where cv2 exists, and gets it.
    try:
        import numpy as np
        import cv2
        x0, y0, x1, y1 = box
        pad = max(3, feather)
        arr = np.array(img)[:, :, ::-1].copy()          # PIL RGB -> cv2 BGR
        mask = np.zeros(arr.shape[:2], np.uint8)
        mask[max(0, y0 - pad):y1 + pad + 1, max(0, x0 - pad):x1 + pad + 1] = 255
        done = cv2.inpaint(arr, mask, 7, cv2.INPAINT_TELEA)
        from PIL import Image as _I
        return _I.fromarray(done[:, :, ::-1])
    except ImportError:
        pass

    # No OpenCV. Texture borrows a real block; smooth gets the blend.
    if _texture(img, box) > 4.0:
        patched = _patch_fill(img, box, max(3, feather))
        if patched is not None:
            return patched

    x0, y0, x1, y1 = box
    w, h = img.size
    x0, y0 = max(0, x0 - feather), max(0, y0 - feather)
    x1, y1 = min(w - 1, x1 + feather), min(h - 1, y1 + feather)

    out = img.copy()
    src, dst = img.load(), out.load()

    ring = []
    pad = 2
    for x in range(max(0, x0 - pad), min(w, x1 + pad + 1)):
        for y in (y0 - pad, y1 + pad):
            if 0 <= y < h:
                ring.append((x, y, src[x, y]))
    for y in range(max(0, y0 - pad), min(h, y1 + pad + 1)):
        for x in (x0 - pad, x1 + pad):
            if 0 <= x < w:
                ring.append((x, y, src[x, y]))
    if not ring:
        return img

    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            num = [0.0, 0.0, 0.0]
            den = 0.0
            for rx, ry, rgb in ring:
                d = (x - rx) ** 2 + (y - ry) ** 2
                wgt = 1.0 / (d + 1.0) ** 1.5
                den += wgt
                for k in range(3):
                    num[k] += rgb[k] * wgt
            dst[x, y] = tuple(int(round(num[k] / den)) for k in range(3))
    return out


def clean(img):
    """Find it and remove it. Returns (image, box_or_None, score).

    UNTOUCHED WHEN NOTHING IS FOUND. A photo with no watermark comes back
    as the same object, not a re-encoded copy -- re-saving a JPEG costs
    quality every time, and doing that to every upload to catch the few
    that need it would be a poor trade.
    """
    box, score = find(img)
    if box is None:
        return img, None, score
    return remove(img, box), box, score
