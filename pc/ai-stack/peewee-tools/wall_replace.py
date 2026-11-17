"""Put a folder of photographs on Pee-Wee's wall, replacing what is there.

JASON, 2026-09-21: "Replace. All the images that are there ... I have
better images."

WHAT THE WALL IS. index.html renders `thumbs/<name>` for every entry in
stations.json's `wall`, and build.py fills that list from `photos/*.jpg`.
So a photograph reaches the wall by existing in BOTH folders under the
same name: the full-size copy in photos/, a 420px-tall copy in thumbs/.
Miss the thumb and the wall shows a broken tile; miss the photo and
build.py never lists it.

JPEG ONLY, AND THAT IS NOT A STYLE CHOICE. build.py's wall filter is
`f.endswith(".jpg")`. The cleaned album is PNG, so dropping it in
unconverted would have put 44 files on disk and nothing on the wall --
silently, with no error anywhere.

THE OLD ONES ARE MOVED, NOT DELETED. "Replace" is unambiguous, but the
24 photographs already there are somebody's, and a folder named
photos-replaced-<date> costs nothing and means a mistake is one `mv`
away from undone.
"""
from __future__ import annotations

import pathlib
import shutil
import sys
from datetime import date

from PIL import Image, ImageOps

sys.stdout.reconfigure(encoding="utf-8", newline="\n")

SITE = pathlib.Path(__file__).resolve().parent.parent / "web/public/peewee"
PHOTOS, THUMBS = SITE / "photos", SITE / "thumbs"

# Matching what is already on the wall, measured rather than guessed:
# the existing thumbs are 420px on the long edge and the full copies
# 1600px. Staying consistent means the drift animation keeps its rhythm.
THUMB_EDGE, FULL_EDGE = 420, 1600


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: wall_replace.py <folder-of-photographs> [--keep]")
    src = pathlib.Path(sys.argv[1])
    if not src.is_dir():
        sys.exit("%s is not a folder" % src)
    replace = "--keep" not in sys.argv

    incoming = sorted(p for p in src.iterdir()
                      if p.is_file()
                      and p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
                      and not p.name.startswith("."))
    if not incoming:
        sys.exit("no photographs in %s" % src)
    print("  %d photograph(s) to put on the wall" % len(incoming))

    if replace and PHOTOS.exists() and any(PHOTOS.iterdir()):
        stamp = date.today().isoformat()
        for folder in (PHOTOS, THUMBS):
            keep = folder.parent / ("%s-replaced-%s" % (folder.name, stamp))
            if keep.exists():
                shutil.rmtree(keep)
            shutil.move(str(folder), str(keep))
            print("  kept the old %s as %s" % (folder.name, keep.name))
    PHOTOS.mkdir(exist_ok=True)
    THUMBS.mkdir(exist_ok=True)

    made = 0
    for i, p in enumerate(incoming, 1):
        # A flat, sortable name. The wall is sorted by filename, so the
        # order on screen is the order here -- which beats whatever the
        # camera or the exporter happened to call them.
        name = "wall-%03d.jpg" % i
        try:
            with Image.open(p) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                full = im.copy()
                full.thumbnail((FULL_EDGE, FULL_EDGE), Image.LANCZOS)
                full.save(PHOTOS / name, "JPEG", quality=88, optimize=True)
                th = im.copy()
                th.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.LANCZOS)
                th.save(THUMBS / name, "JPEG", quality=82, optimize=True)
            made += 1
        except Exception as e:  # noqa: BLE001
            print("  SKIP %-30s %s" % (p.name[:30], e))

    print("  %d on the wall, full + thumb" % made)
    print("  now run build.py so stations.json lists them")


if __name__ == "__main__":
    main()
