#!/usr/bin/env python3
"""photo_meta.py - small, dependency-free helpers for the T1014 photo unify plan (unify_photos.py).

What is here:
  - file kind by extension: image / raw / video / other
  - capture time from EXIF (JPEG APP1 or TIFF-based RAW: NEF, DNG, CR2, ARW, ORF, RW2 ...), reading only the
    first 64 KB of the file (RAWs retry with 512 KB), so it is cheap over SMB
  - capture time from a file name (20240412_191346.jpg, PXL_20230101_120000123.jpg, IMG-20200101-WA0001.jpg)
  - shot key: the base name with copy/edit/Pixel-RAW suffixes removed, used to pair a RAW with its JPG
Why no Pillow: Pillow cannot open NEF (tested 2026-11-17: UnidentifiedImageError on M:\\Pictures\\Nikon\\DSC_0060.NEF)
and exiftool is not installed on the PC. A TIFF IFD walk is ~60 lines and reads every RAW we have.
Read-only: nothing in this file writes anywhere.
"""
import re
import struct
from datetime import datetime

IMAGE = {"jpg", "jpeg", "jpe", "png", "gif", "bmp", "tif", "tiff", "heic", "heif", "hif", "webp"}
RAW = {"nef", "nrw", "dng", "cr2", "cr3", "crw", "arw", "srf", "sr2", "orf", "rw2", "raf", "raw", "pef",
       "srw", "x3f", "3fr", "erf", "kdc", "mrw", "rwl", "iiq"}
VIDEO = {"mp4", "mov", "m4v", "avi", "mts", "m2ts", "3gp", "mkv", "wmv", "mpg", "mpeg", "webm"}
JPG = {"jpg", "jpeg", "jpe"}
EXIF_READABLE = JPG | {"tif", "tiff"} | (RAW - {"cr3", "crw", "raf", "x3f"})


def kind(ext):
    """Return 'image', 'raw', 'video' or 'other' for a lower-case extension without the dot."""
    if ext in RAW:
        return "raw"
    if ext in IMAGE:
        return "image"
    if ext in VIDEO:
        return "video"
    return "other"


def _parse_dt(s):
    """EXIF 'YYYY:MM:DD HH:MM:SS' -> datetime, or None for blank/zero/garbage values."""
    try:
        s = s.strip("\x00 ").replace("-", ":")
        d = datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S")
        return d if 1990 <= d.year <= 2100 else None
    except (ValueError, TypeError):
        return None


def _tiff_date(buf, base=0):
    """Walk a TIFF structure in buf starting at base; return DateTimeOriginal, else DateTime, else None."""
    try:
        bo = buf[base:base + 2]
        e = "<" if bo == b"II" else ">" if bo == b"MM" else None
        if not e:
            return None

        def u16(o):
            return struct.unpack_from(e + "H", buf, base + o)[0]

        def u32(o):
            return struct.unpack_from(e + "I", buf, base + o)[0]

        def ifd(off):
            tags = {}
            n = u16(off)
            for i in range(min(n, 500)):
                p = off + 2 + i * 12
                tag, typ, cnt = u16(p), u16(p + 2), u32(p + 4)
                if typ == 2 and cnt <= 64:  # ASCII
                    vo = p + 8 if cnt <= 4 else u32(p + 8)
                    tags[tag] = buf[base + vo:base + vo + cnt].decode("ascii", "replace")
                elif typ in (4, 13):        # LONG / IFD
                    tags[tag] = u32(p + 8)
            return tags

        ifd0 = ifd(u32(4))
        if 0x8769 in ifd0:
            ex = ifd(ifd0[0x8769])
            for t in (0x9003, 0x9004):
                d = _parse_dt(ex.get(t, ""))
                if d:
                    return d
        return _parse_dt(ifd0.get(0x0132, ""))
    except (struct.error, IndexError, UnicodeError):
        return None


def exif_date(path, ext, nbytes=64 * 1024):
    """Capture time from the first nbytes (64 KB default) of a JPEG/TIFF/RAW file, or None. Never raises on bad files."""
    if ext not in EXIF_READABLE:
        return None
    try:
        with open(path, "rb") as f:
            buf = f.read(nbytes)
    except OSError:
        return None
    if buf[:2] == b"\xff\xd8":  # JPEG: find the APP1 'Exif' segment
        i = 2
        while i + 4 < len(buf) and buf[i] == 0xFF:
            marker, seglen = buf[i + 1], struct.unpack_from(">H", buf, i + 2)[0]
            if marker == 0xE1 and buf[i + 4:i + 10] == b"Exif\x00\x00":
                return _tiff_date(buf, i + 10)
            if marker == 0xDA:
                break
            i += 2 + seglen
        return None
    return _tiff_date(buf, 0)


_NAME_DT = [
    re.compile(r"(?<!\d)(20\d\d|19\d\d)[-_.]?(\d\d)[-_.]?(\d\d)[-_ T.]?(\d\d)[-_.:]?(\d\d)[-_.:]?(\d\d)"),
    re.compile(r"(?<!\d)(20\d\d|19\d\d)[-_.]?(\d\d)[-_.]?(\d\d)(?!\d)"),
]


def name_date(name):
    """Capture time guessed from a file name, or None. Date-only names get noon."""
    for rx in _NAME_DT:
        m = rx.search(name)
        if m:
            g = [int(x) for x in m.groups()] + [12, 0, 0][: 6 - len(m.groups())]
            try:
                d = datetime(*g[:6])
                if 1990 <= d.year <= 2100:
                    return d
            except ValueError:
                continue
    return None


_SUFFIX = re.compile(r"(\.raw-\d+.*|\.mp|\.cover|\.original|-edited|-effects|-collage|\(\d+\)|~\d+| \(\d+\))$")


def shot_key(stem):
    """Base name for RAW/JPG pairing: lower case, Pixel '.RAW-01.MP.COVER' / '(1)' / '-edited' stripped."""
    s = stem.lower()
    while True:
        t = _SUFFIX.sub("", s)
        if t == s:
            return s
        s = t
