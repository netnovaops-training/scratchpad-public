#!/usr/bin/env python3
# =============================================================================
#  resize_all.py -- COPIES of every Legacy photo at web size. Never writes
#  inside Legacy/ itself: originals are the family archive and stay exactly
#  as they are, byte for byte. Output mirrors the folder structure into
#  Legacy-small/ so anything organised there (the per-person folders under
#  finshed/, the cropped set) keeps its meaning in the copy.
#
#  Skips the stray Python .venv that lives inside "Photos-1-001 (1)" --
#  site-packages ships test images and icons by the hundred, and none of
#  them are family photographs.
# =============================================================================
import os

from PIL import Image, ImageOps

SRC = "Legacy"
DST = "Legacy-small"
MAX = 1600
SKIP_DIRS = {".venv", "__pycache__"}
EXT = (".jpg", ".jpeg", ".png")

done = skipped = failed = 0
for root, dirs, files in os.walk(SRC):
    dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
    for f in files:
        if not f.lower().endswith(EXT):
            continue
        src = os.path.join(root, f)
        rel = os.path.relpath(src, SRC)
        out = os.path.join(DST, os.path.splitext(rel)[0] + ".jpg")
        if os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(src):
            skipped += 1
            continue
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            im = Image.open(src)
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((MAX, MAX))
            im.save(out, quality=85, optimize=True)
            done += 1
            if done % 200 == 0:
                print(done, "copied...", flush=True)
        except Exception as e:  # noqa: BLE001  one bad image is counted and the batch goes on
            failed += 1
            print("FAILED", rel, e, flush=True)

print(f"copied {done}, kept {skipped}, failed {failed}")
