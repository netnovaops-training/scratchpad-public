"""Take the Gemini sparkle off a whole folder, in place.

FOR A GOOGLE PHOTOS FOLDER, or any other folder of already-enhanced
pictures. The web-wall upload path cleans one photo as it arrives; this
is the same detector pointed at a directory that already exists.

IT DOES NOT WRITE ANYTHING UNLESS TOLD TO. Run it and it reports: how
many files it looked at, which ones carry a mark, how confident it is
about each, and which ones it would refuse. Pass --apply and it writes.
A tool that edits several hundred of somebody's photographs on first
contact is a tool that has to be trusted before it has earned it.

ORIGINALS ARE KEPT BY DEFAULT, moved into an `.originals` folder beside
the photos. Not because the cleaning is expected to go wrong, but
because the failure mode is silent: a fill that smudges a busy corner
looks fine in a thumbnail and wrong at full size, and by the time
anybody notices, the only copy is the bad one. --replace turns that off
for somebody who has their own backup and means it.

IT REFUSES THE ONES IT WOULD BOTCH, and --force IS A TRAP. The busy
check was written to protect fill QUALITY -- a diffusion fill is
invisible on a smooth corner and a smudge on a busy one. Running Jason's
two albums showed it is doing something more important than that.

Forced, the detector claimed a watermark on: a star of the United States
flag, a yellow flower on a dress, and a diamond pattern in knitted
fabric. All three are genuinely star-shaped and genuinely symmetric, so
no shape test can rule them out -- and forcing the run would have
inpainted a hole into three real photographs.

What separates them from the real mark is not their shape but their
SURROUNDINGS: Gemini puts its sparkle on a quiet corner, and a flag, a
flower and a knit are none of those. So the busy check is load-bearing
and --force should be used on one named file at a time, after looking,
never across a folder.

--contact-sheet writes a before/after strip -- in a dry run too -- so the
judgement can be checked by eye instead of taken on trust. That is how
the flag was caught.

WHAT IT CANNOT DO: Gemini's invisible SynthID watermark stays. These
files remain identifiable as AI-generated.
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import watermark  # noqa: E402
from PIL import Image, ImageOps  # noqa: E402

SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}

# How varied the ring around the mark may be before the fill is a bad
# idea. Standard deviation of luminance, on 0..255. A gradient sits in the
# low single digits; foliage or a crowd runs well past twenty.
BUSY_LIMIT = 18.0


def ring_variation(img, box, pad=6):
    """How busy the area around the patch is."""
    x0, y0, x1, y1 = box
    w, h = img.size
    g = img.convert("L").load()
    vals = []
    for x in range(max(0, x0 - pad), min(w, x1 + pad + 1)):
        for y in (y0 - pad, y1 + pad):
            if 0 <= y < h:
                vals.append(g[x, y])
    for y in range(max(0, y0 - pad), min(h, y1 + pad + 1)):
        for x in (x0 - pad, x1 + pad):
            if 0 <= x < w:
                vals.append(g[x, y])
    if len(vals) < 8:
        return 999.0
    mean = sum(vals) / len(vals)
    return (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5


def save_like(img, path, original):
    """Write back in the format it arrived in, at a quality that does not
    visibly cost anything. A PNG stays a PNG -- re-encoding somebody's
    lossless file as JPEG to save a few kilobytes is not this tool's
    decision to make."""
    ext = path.suffix.lower()
    if ext in (".jpg", ".jpeg"):
        img.save(path, "JPEG", quality=95, optimize=True,
                 subsampling=0, exif=original.info.get("exif", b""))
    elif ext == ".webp":
        img.save(path, "WEBP", quality=95, method=6)
    else:
        img.save(path)


def main():
    ap = argparse.ArgumentParser(
        description="Remove the Gemini watermark from a folder of photos.")
    ap.add_argument("folder", type=pathlib.Path)
    ap.add_argument("--apply", action="store_true",
                    help="actually write. Without this, nothing is changed.")
    ap.add_argument("--replace", action="store_true",
                    help="do not keep originals in .originals/")
    ap.add_argument("--force", action="store_true",
                    help="clean even where the surroundings are busy. READ THE "
                         "MODULE DOCSTRING FIRST -- forced, this has claimed a "
                         "flag's star and a flower as watermarks.")
    ap.add_argument("--recursive", action="store_true")
    ap.add_argument("--contact-sheet", type=pathlib.Path, default=None,
                    help="write a before/after strip of every change here")
    a = ap.parse_args()

    if not a.folder.is_dir():
        sys.exit("%s is not a folder" % a.folder)

    files = sorted(
        p for p in (a.folder.rglob("*") if a.recursive else a.folder.iterdir())
        if p.is_file() and p.suffix.lower() in SUFFIXES
        and ".originals" not in p.parts)

    print("  %d image(s) in %s" % (len(files), a.folder))
    print("  %s\n" % ("WRITING" if a.apply else
                      "dry run -- nothing will be changed. Add --apply."))

    found = cleaned = skipped = clean_already = 0
    sheet = []

    for p in files:
        try:
            with Image.open(p) as raw:
                img = ImageOps.exif_transpose(raw).convert("RGB")
                box, score = watermark.find(img)
                if box is None:
                    clean_already += 1
                    continue
                found += 1
                busy = ring_variation(img, box)
                if busy > BUSY_LIMIT and not a.force:
                    skipped += 1
                    print("  skip  %-44s mark at %s, but the area around it "
                          "is busy (%.0f) -- the fill would smudge. --force "
                          "to do it anyway." % (p.name[:44], box, busy))
                    continue

                print("  clean %-44s %s  confidence %.2f  background %.0f"
                      % (p.name[:44], box, score, busy))

                # THE PREVIEW IS BUILT IN A DRY RUN TOO, which is the point
                # of a dry run: deciding whether to trust the detector by
                # looking at what it would do is impossible if the pictures
                # only appear after it has already done it.
                out = watermark.remove(img, box)
                if a.contact_sheet is not None:
                    sheet.append((p.name, img.crop(_pad(box, img.size)),
                                  out.crop(_pad(box, img.size))))
                if not a.apply:
                    continue

                if not a.replace:
                    keep = p.parent / ".originals"
                    keep.mkdir(exist_ok=True)
                    shutil.copy2(p, keep / p.name)
                save_like(out, p, raw)
                cleaned += 1
        except Exception as e:  # noqa: BLE001
            print("  ERROR %-44s %s" % (p.name[:44], e))

    print("\n  %d carried a mark, %d had none" % (found, clean_already))
    if a.apply:
        print("  %d cleaned%s, %d skipped as too busy"
              % (cleaned,
                 "" if a.replace else " (originals in .originals/)", skipped))
    else:
        print("  %d would be cleaned, %d skipped as too busy"
              % (found - skipped, skipped))

    if sheet and a.contact_sheet is not None:
        _write_sheet(sheet, a.contact_sheet)
        print("  before/after strip: %s" % a.contact_sheet)


def _pad(box, size, pad=50):
    x0, y0, x1, y1 = box
    w, h = size
    return (max(0, x0 - pad), max(0, y0 - pad),
            min(w, x1 + pad), min(h, y1 + pad))


def _write_sheet(rows, path):
    """A strip of before|after pairs, so the whole run can be judged by eye
    in one look rather than by opening several hundred files."""
    from PIL import ImageDraw
    cw = max(r[1].width for r in rows)
    ch = max(r[1].height for r in rows)
    out = Image.new("RGB", (cw * 2 + 30, (ch + 22) * len(rows) + 10), (18, 18, 18))
    d = ImageDraw.Draw(out)
    y = 6
    for name, before, after in rows:
        out.paste(before, (6, y))
        out.paste(after, (cw + 24, y))
        d.text((6, y + ch + 4), name[:60], fill=(190, 190, 190))
        y += ch + 22
    out.save(path)


if __name__ == "__main__":
    main()
