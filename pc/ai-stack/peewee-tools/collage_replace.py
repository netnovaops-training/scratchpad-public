"""Rebuild the drifting mosaic behind Pee-Wee's name.

JASON, 2026-09-21: "I want my newly updated images. Replace the collage
that's in Peewee."

WHAT THE MOSAIC IS. index.html reads data/collage.json, shuffles it,
renders every entry as collage/<name>, and lays the board out TWICE
inside one drifting track -- so when the animation reaches -50% it is
showing exactly what 0% showed and the loop is invisible. The count is
therefore free to change: fewer tiles simply means a shorter board and
a sooner (still invisible) repeat.

TILE SHAPE, MEASURED OFF WHAT IS THERE. 240px on the long edge, around
7KB each. The CSS crops them square with object-fit:cover and lays
them out at minimum 86px, so the source aspect ratio does not matter --
but the long edge does, because a 1600px photograph in a 86px slot is
six megabytes of wasted download on a page that shows hundreds at once.

A HONEST NOTE ON DENSITY. The board being replaced has 888 tiles; this
one will have as many as you give it. At 155 the mosaic is visibly
sparser and the drift repeats sooner. That is a look, not a bug, but
it is a different look and worth seeing before it is kept.

THE OLD BOARD IS MOVED, NOT DELETED. 888 tiles of somebody's family
are not something to overwrite on a one-line instruction.
"""
from __future__ import annotations

import json
import pathlib
import shutil
import sys
from datetime import date

from PIL import Image, ImageOps

sys.stdout.reconfigure(encoding="utf-8", newline="\n")

SITE = pathlib.Path(__file__).resolve().parent.parent / "web/public/peewee"
COLLAGE = SITE / "collage"
MANIFEST = SITE / "data" / "collage.json"
EDGE = 240
EXTS = (".jpg", ".jpeg", ".png", ".webp")


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: collage_replace.py <folder> [<folder> ...] [--add]")
    add = "--add" in sys.argv
    folders = [pathlib.Path(a) for a in sys.argv[1:] if not a.startswith("--")]

    incoming = []
    for f in folders:
        if not f.is_dir():
            sys.exit(f"{f} is not a folder")
        incoming += sorted(p for p in f.iterdir()
                           if p.is_file() and p.suffix.lower() in EXTS
                           and not p.name.startswith("."))
    if not incoming:
        sys.exit("no photographs found")
    print(f"  {len(incoming)} photograph(s) for the mosaic")

    if not add and COLLAGE.exists() and any(COLLAGE.iterdir()):
        keep = SITE / (f"collage-replaced-{date.today().isoformat()}")
        if keep.exists():
            shutil.rmtree(keep)
        shutil.move(str(COLLAGE), str(keep))
        print(f"  kept the old {len(list(keep.iterdir()))}-tile board as {keep.name}")
    COLLAGE.mkdir(exist_ok=True)

    existing = sorted(p.name for p in COLLAGE.iterdir()) if add else []
    made = []
    for i, p in enumerate(incoming, 1):
        name = f"n{i:04d}.jpg"          # n for new, so it never collides
        try:                            # with the old c0001 naming
            with Image.open(p) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((EDGE, EDGE), Image.LANCZOS)
                im.save(COLLAGE / name, "JPEG", quality=78, optimize=True)
            made.append(name)
        except Exception as e:  # noqa: BLE001
            print(f"  SKIP {p.name[:30]!s:<30} {e}")

    tiles = sorted(set(existing + made))
    MANIFEST.write_text(json.dumps(tiles) + "\n", encoding="utf-8", newline="\n")

    total = sum((COLLAGE / t).stat().st_size for t in tiles)
    print(f"  {len(tiles)} tiles written, {total / 1048576:.1f} MB total, manifest updated")
    if len(tiles) < 300:
        print(f"  NOTE: the old board had 888. At {len(tiles)} the mosaic is sparser and")
        print("        the drift repeats sooner -- worth looking at before keeping.")


if __name__ == "__main__":
    main()
