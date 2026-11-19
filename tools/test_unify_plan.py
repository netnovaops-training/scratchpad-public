#!/usr/bin/env python3
"""test_unify_plan.py - checks unify_plan.build() on fake rows (no NAS needed). Run: python test_unify_plan.py
Covers: keeper choice (P: in YYYY\\MM beats Z:), RAW+JPG pairing (same folder; same name + capture time;
same name without time -> raw-check), RAW-only -> raw-convert with a JPG target, target name clashes,
Z:-only -> move-from-Z, size clash without a hash -> not-hashed, M:-only -> copy-from-M, sidecars left alone.
OUTCOME 2026-11-17: all pass.
"""
import csv
import tempfile
import unittest
from pathlib import Path

import unify_plan as up

P, Z, M = r"\\192.168.1.68\Pictures", r"\\192.168.1.68\photo", r"M:\Pictures"


def row(src, path, size, kind="image", mtime=1700000000):
    """Fake inventory row."""
    ext = path.rsplit(".", 1)[1].lower()
    root = {"P": P, "Z": Z, "M": M}[src]
    top = path[len(root) + 1:].split("\\")[0]
    return {"src": src, "path": path, "size": size, "mtime": mtime, "ext": ext, "kind": kind, "top": top}


class T(unittest.TestCase):
    def setUp(self):
        d = Path(tempfile.mkdtemp())
        up.HASHES, up.META, up.PLAN, up.SUMMARY = d / "h.csv", d / "m.csv", d / "plan.csv", d / "sum.txt"
        up.nas_hashes = dict  # the test supplies every hash itself
        up.log = lambda msg: None  # keep the real progress.log clean
        self.d = d

    def run_plan(self, rows, hashes, meta):
        for path, cols, data in ((up.HASHES, "sha256", hashes), (up.META, "date", meta)):
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["path", "size", "mtime", cols])
                for p, v in data.items():
                    w.writerow([p, 0, 0, v])
        up.build(rows)
        with open(up.PLAN, newline="", encoding="utf-8") as f:
            return {r["path"]: r for r in csv.DictReader(f)}

    def test_rules(self):
        rows = [
            row("P", P + r"\2014\05\a.jpg", 100),
            row("Z", Z + r"\Takeout-Library\2014\a.jpg", 100),          # dup of P copy
            row("Z", Z + r"\Takeout-Library\2015\b.jpg", 200),          # Z only
            row("M", M + r"\Nikon\DSC_0001.NEF", 300, "raw"),            # RAW, JPG in same folder
            row("M", M + r"\Nikon\DSC_0001.JPG", 50),
            row("P", P + r"\Nikon\DSC_0002.NEF", 310, "raw"),            # RAW, JPG elsewhere, times agree
            row("Z", Z + r"\Takeout-Library\2016\DSC_0002.jpg", 60),
            row("P", P + r"\Nikon\DSC_0003.NEF", 320, "raw"),            # RAW, JPG elsewhere, no time
            row("P", P + r"\Edits\DSC_0003.jpg", 70),
            row("P", P + r"\Nikon\DSC_0004.NEF", 330, "raw"),            # RAW only -> convert
            row("P", P + r"\2016\03\DSC_0004.jpg", 80),                   # name clash at target, other shot
            row("Z", Z + r"\Takeout-Library\x.json", 5, "other"),
            row("Z", Z + r"\2014\05\a.jpg", 100),                        # same path as the P copy
            row("Z", Z + r"\Immich\c.jpg", 900),                          # size clash, hash missing
            row("P", P + r"\Edits\c2.jpg", 900),
        ]
        hashes = {P + r"\2014\05\a.jpg": "h1", Z + r"\Takeout-Library\2014\a.jpg": "h1",
                  Z + r"\2014\05\a.jpg": "h1", P + r"\Edits\c2.jpg": "h9"}
        meta = {P + r"\Nikon\DSC_0002.NEF": "exif:2016-01-01T10:00:00",
                Z + r"\Takeout-Library\2016\DSC_0002.jpg": "exif:2016-01-01T10:00:01",
                P + r"\Nikon\DSC_0004.NEF": "exif:2016-03-09T08:00:00",
                P + r"\2016\03\DSC_0004.jpg": "exif:2016-03-01T08:00:00"}
        out = self.run_plan(rows, hashes, meta)
        a = {p: r["action"] for p, r in out.items()}
        self.assertEqual(a[P + r"\2014\05\a.jpg"], "keep")
        self.assertEqual(a[Z + r"\Takeout-Library\2014\a.jpg"], "duplicate")
        self.assertEqual(out[Z + r"\Takeout-Library\2014\a.jpg"]["keep_path"], P + r"\2014\05\a.jpg")
        self.assertEqual(a[Z + r"\Takeout-Library\2015\b.jpg"], "move-from-Z")
        self.assertTrue(out[Z + r"\Takeout-Library\2015\b.jpg"]["target"].startswith(P + "\\2015\\"))
        self.assertEqual(a[M + r"\Nikon\DSC_0001.NEF"], "raw-with-jpg")
        self.assertEqual(a[M + r"\Nikon\DSC_0001.JPG"], "copy-from-M")
        self.assertEqual(a[P + r"\Nikon\DSC_0002.NEF"], "raw-with-jpg")
        self.assertEqual(out[P + r"\Nikon\DSC_0002.NEF"]["match_how"], "same name + capture time")
        self.assertEqual(a[P + r"\Nikon\DSC_0003.NEF"], "raw-check")
        self.assertEqual(out[P + r"\Nikon\DSC_0003.NEF"]["target"], P + r"\_T1014-raw-check\DSC_0003.NEF")
        self.assertEqual(a[P + r"\Nikon\DSC_0004.NEF"], "raw-convert")
        self.assertEqual(out[P + r"\Nikon\DSC_0004.NEF"]["target"], P + r"\2016\03\DSC_0004_1.jpg")
        self.assertEqual(a[Z + r"\Takeout-Library\x.json"], "not-photo")
        self.assertEqual(a[Z + r"\2014\05\a.jpg"], "duplicate")
        self.assertEqual(out[Z + r"\2014\05\a.jpg"]["proof"], "sha256")
        self.assertEqual(a[Z + r"\Immich\c.jpg"], "not-hashed")
        self.assertEqual(a[P + r"\Edits\c2.jpg"], "relocate-in-P")
        self.assertEqual(out[Z + r"\Takeout-Library\2015\b.jpg"]["proof"], "unique size")
        self.assertIn("raw-convert", up.SUMMARY.read_text(encoding="utf-8"))


class NasPaths(unittest.TestCase):
    def test_round_trip(self):
        import unify_photos as u
        p = P + r"\Takeout-Library\2020\1090.jpg"
        self.assertEqual(u.to_nas(p), "/volume1/Pictures/Takeout-Library/2020/1090.jpg")
        self.assertEqual(u.from_nas(u.to_nas(p)), p)
        self.assertEqual(u.to_nas(Z + r"\Immich\x.jpg"), "/volume1/photo/Immich/x.jpg")
        with self.assertRaises(ValueError):
            u.to_nas(M + r"\x.jpg")

    def test_file_ext_strips_smb_mapped_cr(self):
        import unify_photos as u
        self.assertEqual(u.file_ext(P + "\\Takeout-RawOnly\\IMG_1.dng"), "dng")
        self.assertEqual(u.file_ext(P + "\\a.JPG"), "jpg")


if __name__ == "__main__":
    unittest.main()
