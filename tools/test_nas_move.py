#!/usr/bin/env python3
"""test_nas_move.py - nas_move.py + nas_restore.py on temp folders (the rename path; the NAS reflink path was
tested by hand). Run: python test_nas_move.py
OUTCOME 2026-11-17: all pass.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import nas_move
import nas_restore


class T(unittest.TestCase):
    def test_move_refuse_restore(self):
        d = tempfile.mkdtemp()
        p = lambda *a: os.path.join(d, *a)
        for name, data in (("keep.jpg", b"aaaa"), ("dup.jpg", b"aaaa"), ("orphan.jpg", b"bb")):
            Path(p(name)).write_bytes(data)
        ops = [{"n": 1, "act": "duplicate", "src": p("dup.jpg"), "dst": p("hold", "dup.jpg"), "size": 4,
                "need": p("keep.jpg"), "need_size": 4},
               {"n": 2, "act": "duplicate", "src": p("orphan.jpg"), "dst": p("hold", "orphan.jpg"), "size": 2,
                "need": p("gone.jpg"), "need_size": 2},
               {"n": 3, "act": "relocate-in-P", "src": p("keep.jpg"), "dst": p("2020", "01", "keep.jpg"),
                "size": 4, "need": None, "need_size": None}]
        with open(p("ops.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(o) for o in ops))
        nas_move.main([p("ops.jsonl"), p("j.jsonl")])
        self.assertTrue(os.path.exists(p("hold", "dup.jpg")))
        self.assertTrue(os.path.exists(p("orphan.jpg")))  # kept copy missing: refused, untouched
        self.assertTrue(os.path.exists(p("2020", "01", "keep.jpg")))
        nas_move.main([p("ops.jsonl"), p("j.jsonl")])  # rerun: ok ops skipped, refused one refused again
        with mock.patch("os.path.expanduser", lambda s: p("restore.log")):
            nas_restore.main([p("j.jsonl")])
        for name in ("keep.jpg", "dup.jpg", "orphan.jpg"):
            self.assertTrue(os.path.exists(p(name)), name)
        self.assertFalse(os.path.exists(p("hold", "dup.jpg")))


if __name__ == "__main__":
    unittest.main()
