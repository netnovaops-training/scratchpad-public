#!/usr/bin/env python3
"""test_photo_meta.py - unit tests for photo_meta.py (T1014). Run: python D:\\scratchpad\\tools\\test_photo_meta.py
Builds tiny JPEG/TIFF files in a temp folder, so no real photo is needed.
OUTCOME 2026-11-17: all pass; also checked by hand on M:\\Pictures\\Nikon\\DSC_0060.NEF (2025-08-02 19:05:25).
"""
import os
import struct
import tempfile
import unittest
from datetime import datetime

import photo_meta as pm


def tiff(dto=b"2021:05:06 07:08:09\x00", little=True):
    """Minimal TIFF: IFD0 with one ExifIFD pointer; Exif IFD with DateTimeOriginal."""
    e = "<" if little else ">"
    hdr = (b"II" if little else b"MM") + struct.pack(e + "HI", 42, 8)
    ifd0 = struct.pack(e + "H", 1) + struct.pack(e + "HHII", 0x8769, 4, 1, 26) + struct.pack(e + "I", 0)
    exif = struct.pack(e + "H", 1) + struct.pack(e + "HHII", 0x9003, 2, len(dto), 44) + struct.pack(e + "I", 0)
    return hdr + ifd0 + exif + dto


def jpeg(t):
    """JPEG with an APP1 Exif segment holding TIFF bytes t."""
    body = b"Exif\x00\x00" + t
    return b"\xff\xd8\xff\xe1" + struct.pack(">H", len(body) + 2) + body + b"\xff\xda\x00\x02"


class T(unittest.TestCase):
    def write(self, data, ext):
        fd, p = tempfile.mkstemp(suffix="." + ext)
        os.write(fd, data)
        os.close(fd)
        self.addCleanup(os.remove, p)
        return p

    def test_tiff_raw_little_and_big_endian(self):
        want = datetime(2021, 5, 6, 7, 8, 9)
        self.assertEqual(pm.exif_date(self.write(tiff(), "nef"), "nef"), want)
        self.assertEqual(pm.exif_date(self.write(tiff(little=False), "dng"), "dng"), want)

    def test_jpeg_app1(self):
        self.assertEqual(pm.exif_date(self.write(jpeg(tiff()), "jpg"), "jpg"), datetime(2021, 5, 6, 7, 8, 9))

    def test_bad_or_blank_dates(self):
        self.assertIsNone(pm.exif_date(self.write(tiff(b"0000:00:00 00:00:00\x00"), "nef"), "nef"))
        self.assertIsNone(pm.exif_date(self.write(b"not an image", "jpg"), "jpg"))
        self.assertIsNone(pm.exif_date(self.write(b"\x00" * 10, "mp4"), "mp4"))

    def test_shot_key(self):
        self.assertEqual(pm.shot_key("PXL_20230101_120000123.RAW-01.MP.COVER"), "pxl_20230101_120000123")
        self.assertEqual(pm.shot_key("PXL_20230101_120000123.RAW-02.ORIGINAL"), "pxl_20230101_120000123")
        self.assertEqual(pm.shot_key("DSC_0060-edited"), "dsc_0060")
        self.assertEqual(pm.shot_key("IMG_1234(1)"), "img_1234")
        self.assertEqual(pm.shot_key("IMG_1234 (2)"), "img_1234")
        self.assertEqual(pm.shot_key("DSC_0061"), "dsc_0061")

    def test_name_date(self):
        self.assertEqual(pm.name_date("20240412_191346"), datetime(2024, 4, 12, 19, 13, 46))
        self.assertEqual(pm.name_date("IMG-20200101-WA0001"), datetime(2020, 1, 1, 12, 0, 0))
        self.assertIsNone(pm.name_date("DSC_0060"))

    def test_kind(self):
        self.assertEqual(pm.kind("nef"), "raw")
        self.assertEqual(pm.kind("hif"), "image")
        self.assertEqual(pm.kind("mov"), "video")
        self.assertEqual(pm.kind("nksc"), "other")


if __name__ == "__main__":
    unittest.main()
