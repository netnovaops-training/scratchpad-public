#!/usr/bin/env python3
"""test_make_catalog.py - checks for tools/make_catalog.py (stdlib only, 2026-11-23).

    py -3.14 -I tools/test_make_catalog.py

Outcome checked: the generator runs in both editions into a temp folder (the repo's own CATALOG.md is not
touched), every tracked .py/.sh/.ps1 script appears exactly once in the private edition, and the public
edition holds none of the private markers (drive paths, Tailscale/LAN addresses, Gmail, DuckDNS/Zapto).
"""
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parent
GEN = TOOLS / "make_catalog.py"
PRIVATE_MARKERS = ["C:\\", "D:\\", "100.", "192.168.", "@" "gmail", "duckdns", "zapto"]


def run_gen(*args):
    """Run the generator the way Jason does (py -I), fail the test on a non-zero exit."""
    r = subprocess.run([sys.executable, "-I", str(GEN), *args], capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"make_catalog.py {' '.join(args)} failed: {r.stdout}{r.stderr}")
    return r.stdout


class CatalogTest(unittest.TestCase):
    """Both editions, written once into a temp folder and checked."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        base = Path(cls.tmp.name)
        cls.priv, cls.pub = base / "private", base / "public"
        run_gen("--out", str(cls.priv))
        run_gen("--public", str(cls.pub))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_files_written(self):
        """Each edition has CATALOG.md and a self-contained catalog/index.html with the search box."""
        for d in (self.priv, self.pub):
            self.assertTrue((d / "CATALOG.md").is_file())
            page = (d / "catalog" / "index.html").read_text(encoding="utf-8")
            self.assertIn('id="q"', page)
            self.assertNotRegex(page, r'<(script|link)[^>]+(src|href)="https?://')  # no external resources

    def test_every_script_once(self):
        """Every tracked script's path appears in exactly one entry of the private edition."""
        files = subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(ROOT), "ls-files"],
                               capture_output=True, text=True, encoding="utf-8", check=True).stdout.split("\n")
        scripts = {f for f in files if Path(f).suffix.lower() in {".py", ".sh", ".ps1"}
                   and "/__pycache__/" not in f and not f.startswith("docs/")}
        md = (self.priv / "CATALOG.md").read_text(encoding="utf-8")
        listed = re.findall(r"\*\*File:\*\* `([^`]+)`", md)
        self.assertEqual(len(listed), len(set(listed)), "a script is listed twice")
        self.assertEqual(set(listed), scripts)

    def test_public_has_no_private_markers(self):
        """The public edition never shows drive paths, private addresses, Gmail or the dynamic-DNS hosts."""
        for name in ("CATALOG.md", "catalog/index.html"):
            text = (self.pub / name).read_text(encoding="utf-8")
            for marker in PRIVATE_MARKERS:
                self.assertNotIn(marker.lower(), text.lower(), f"{marker!r} in public {name}")

    def test_public_is_subset(self):
        """The public edition lists no more scripts than the private one (public_copy leaves folders out)."""
        count = lambda d: len(re.findall(r"^### ", (d / "CATALOG.md").read_text(encoding="utf-8"), re.M))
        self.assertLessEqual(count(self.pub), count(self.priv))


if __name__ == "__main__":
    unittest.main(verbosity=2)
