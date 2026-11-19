#!/usr/bin/env python3
"""raw_to_jpg.py - T1014 phase 2: convert the RAW-only shots (action raw-convert) to JPG, keeping date + EXIF.

Run (PC):  python D:\\scratchpad\\tools\\raw_to_jpg.py [--limit N]
Reads:     D:\\backups\\photo-verify\\unify-plan.csv (raw-convert rows: path = RAW, target = new JPG)
Writes:    the JPG at target (P:\\YYYY\\MM\\name.jpg over SMB), via name.jpg.part then rename; skips a target that
           exists. Log: D:\\backups\\photo-verify\\unify\\converted.jsonl {"raw", "jpg", "size"|"err"}.
Picture:   rawpy (LibRaw) camera white balance, 8-bit sRGB, rotated upright (so Orientation is written as 1),
           JPEG quality 95.
EXIF kept: Make, Model, DateTime, and from the Exif IFD: DateTimeOriginal, DateTimeDigitized, ExposureTime,
           FNumber, ISO, FocalLength, LensModel, read from the RAW's own TIFF header with Pillow. Maker notes are
           left out (offsets would break). The file time is set to the capture time.
The RAW itself is not touched here; nas_move.py moves it to the holding folder only once this JPG exists.
OUTCOME 2026-11-17: written for T1014 phase 2.
"""
import csv
import io
import json
import os
import sys
from datetime import datetime
from pathlib import Path

import rawpy
from PIL import Image

PLAN = Path(r"D:\backups\photo-verify\unify-plan.csv")
LOG = Path(r"D:\backups\photo-verify\unify\converted.jsonl")
IFD0_KEEP = (0x010F, 0x0110, 0x0132)                                    # Make, Model, DateTime
EXIF_KEEP = (0x9003, 0x9004, 0x829A, 0x829D, 0x8827, 0x920A, 0xA434)   # dates, exposure, f, ISO, focal, lens


def exif_from_raw(raw_path):
    """Pillow Exif with the kept tags copied from the RAW's TIFF header (NEF/DNG are TIFF files)."""
    out = Image.Exif()
    with open(raw_path, "rb") as f:
        head = f.read(4 << 20)
    src = Image.Exif()
    try:
        src.load(b"Exif\x00\x00" + head)
    except Exception:  # noqa: BLE001  odd header: keep the picture, lose the tags
        return out
    for t in IFD0_KEEP:
        if t in src:
            out[t] = src[t]
    sub = src.get_ifd(0x8769)
    if sub:
        ifd = out.get_ifd(0x8769)
        for t in EXIF_KEEP:
            if t in sub:
                ifd[t] = sub[t]
    out[0x0112] = 1  # pixels are already upright
    return out


def with_exif(jpeg, exif):
    """JPEG bytes with our EXIF as an APP1 segment right after SOI (no re-encode)."""
    app1 = exif.tobytes()
    return jpeg[:2] + b"\xff\xe1" + (len(app1) + 2).to_bytes(2, "big") + app1 + jpeg[2:]


def embedded_jpeg(raw):
    """(bytes, width) of the camera's own full-size JPEG inside the RAW, or None.
    Used when LibRaw cannot decode the sensor data. OUTCOME 2026-11-17: Nikon Z 'NZ502' NEFs (HE compression)
    fail in rawpy 0.27 with LibRawDataError; their embedded preview is the full-size camera JPEG."""
    try:
        th = raw.extract_thumb()
    except Exception:  # noqa: BLE001
        return None
    if th.format != rawpy.ThumbFormat.JPEG:
        return None
    w = Image.open(io.BytesIO(th.data)).size[0]
    if w < 3000:  # a small preview is not a stand-in for the photo
        return None
    return th.data, w


def convert(raw_path, jpg_path, when):
    """Write jpg_path from raw_path; returns (size, how)."""
    exif = exif_from_raw(raw_path)
    if when and 0x9003 not in exif.get_ifd(0x8769):
        stamp = when.strftime("%Y:%m:%d %H:%M:%S")
        exif.get_ifd(0x8769)[0x9003] = stamp
        exif[0x0132] = stamp
    with rawpy.imread(raw_path) as raw:
        try:
            rgb = raw.postprocess(use_camera_wb=True, output_bps=8)
            buf = io.BytesIO()
            Image.fromarray(rgb).save(buf, "JPEG", quality=95, exif=exif.tobytes())
            data, how = buf.getvalue(), "libraw"
        except rawpy.LibRawError:
            emb = embedded_jpeg(raw)
            if not emb:
                raise
            # the embedded JPEG is not rotated: keep the RAW's own Orientation tag instead of 1
            with open(raw_path, "rb") as f:
                src = Image.Exif()
                src.load(b"Exif\x00\x00" + f.read(1 << 20))
            exif[0x0112] = src.get(0x0112, 1)
            data, how = with_exif(emb[0], exif), f"embedded camera JPEG {emb[1]}px"
    os.makedirs(os.path.dirname(jpg_path), exist_ok=True)
    part = jpg_path + ".part"
    with open(part, "wb") as f:
        f.write(data)
    os.replace(part, jpg_path)
    if when:
        ts = when.timestamp()
        os.utime(jpg_path, (ts, ts))
    return os.path.getsize(jpg_path), how


def main(argv):
    limit = int(argv[argv.index("--limit") + 1]) if "--limit" in argv else None
    with open(PLAN, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["action"] == "raw-convert"]
    done = ok = bad = 0
    with open(LOG, "a", encoding="utf-8") as log:
        for r in rows[:limit]:
            if os.path.exists(r["target"]):
                done += 1
                continue
            when = datetime.fromisoformat(r["date"]) if r["date"] else None
            rec = {"raw": r["path"], "jpg": r["target"]}
            try:
                rec["size"], rec["how"] = convert(r["path"], r["target"], when)
                ok += 1
            except Exception as e:  # noqa: BLE001  log it, carry on with the next file
                rec["err"] = f"{type(e).__name__}: {e}"[:300]
                bad += 1
            log.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log.flush()
    print(f"converted {ok}, errors {bad}, already there {done}, of {len(rows[:limit])}")


if __name__ == "__main__":
    main(sys.argv[1:])
