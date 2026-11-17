#!/usr/bin/env python3
"""sync_scratchpad.py: copy this PC's script folders to the NUC for the code scratchpad
(example.org/scratchpad/, the Rust app in rust/scratchpad).

    python -I scripts/sync_scratchpad.py            # send, replacing the NUC copy
    python -I scripts/sync_scratchpad.py --list     # only print what would be sent

Written 2026-11-12 (step 3 of Jason's cleanup). The scratchpad runs on the always-on NUC so share
links keep working when this PC is off; the NUC's own scripts (learning-src, bin) are mounted
there directly. This sends a read-only copy of the PC side to ~/scratchpad-src/pc/<label>/, with
the AI-written docs from docs/code beside them in ~/scratchpad-src/pc/_docs/<label>/.

The whole pc/ folder is rebuilt each run (sent to pc.new, then swapped in), so a script deleted or
renamed here disappears there too. Only script files are sent. Left out, as Jason asked: anything
mentioning Garden for All or Robin, and file names that look like secrets. The app re-checks both.
Also run at the end of sync_documents.py (the nightly DocsToPaperless task).
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import sys
import tarfile

AI = r"C:\Jasons New Site\www\ai-stack"
DOCS = os.path.join(AI, "docs", "code")
NUC = "server@192.168.1.86"
# (label, folder, recurse, docs sub-folder under docs/code or None)
SOURCES = [
    ("ai-stack-scripts", os.path.join(AI, "scripts"), True, "scripts"),
    ("ai-stack", AI, False, ""),
    ("peewee-tools", os.path.join(AI, "peewee-tools"), True, "peewee-tools"),
    ("scratchpad", os.path.join(AI, "scratchpad", "files"), True, None),
    ("secrets-tools", r"D:\secrets-tools", True, None),
    ("doc-gather", r"D:\doc-gather", False, None),
]
EXT = {".py", ".sh", ".bash", ".ps1", ".psm1", ".cmd", ".bat", ".rs", ".js", ".mjs", ".ts",
       ".sql", ".vbs", ".pl", ".rb", ".go", ".lua"}
SKIP_DIRS = {"__pycache__", "node_modules", ".git", "target", ".venv", "venv", "duplicates",
             "proton-pass-cli-docs", "drive-staging"}
GARDEN = re.compile(r"(?i)garden|gfa-|for-robin|robin-|jun_|\bjuns?\b")
SECRETISH = re.compile(r"(?i)token|secret|password|passwd|credential|\.env|id_rsa|id_ed25519")
MAX = 512 * 1024


def files():
    for label, root, recurse, docs in SOURCES:
        if not os.path.isdir(root):
            continue
        for dp, dns, fns in os.walk(root):
            dns[:] = [] if not recurse else [d for d in dns if d not in SKIP_DIRS and not d.startswith(".") and not GARDEN.search(d)]
            for f in fns:
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, root).replace("\\", "/")
                if os.path.splitext(f)[1].lower() not in EXT or SECRETISH.search(f) or GARDEN.search(rel):
                    continue
                if os.path.getsize(p) > MAX:
                    continue
                try:
                    text = open(p, encoding="utf-8", errors="replace").read()
                except OSError:
                    continue
                if GARDEN.search(text):
                    continue
                yield label, rel, p, docs


def main() -> int:
    buf = io.BytesIO()
    n = d = 0
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for label, rel, p, docs in files():
            if "--list" in sys.argv:
                print(label, rel)
            tar.add(p, arcname="%s/%s" % (label, rel))
            n += 1
            if docs is not None:
                md = os.path.join(DOCS, docs, *rel.split("/")) + ".md"
                if os.path.isfile(md) and not GARDEN.search(open(md, encoding="utf-8", errors="replace").read()):
                    tar.add(md, arcname="_docs/%s/%s.md" % (label, rel))
                    d += 1
    print("scripts %d, docs %d" % (n, d))
    if "--list" in sys.argv:
        return 0
    cmd = ("set -e; cd ~ && mkdir -p scratchpad-src && rm -rf scratchpad-src/pc.new && mkdir scratchpad-src/pc.new "
           "&& tar -xzf - -C scratchpad-src/pc.new && rm -rf scratchpad-src/pc.old "
           "&& { [ -d scratchpad-src/pc ] && mv scratchpad-src/pc scratchpad-src/pc.old || true; } "
           "&& mv scratchpad-src/pc.new scratchpad-src/pc && rm -rf scratchpad-src/pc.old")
    r = subprocess.run(["ssh", NUC, cmd], input=buf.getvalue(), capture_output=True)
    print("PASS sent to NUC" if r.returncode == 0 else "FAIL ssh/tar exit %d" % r.returncode)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
