"""rej_unclean.py - apply public-copy .rej hunks to the private Scratchpad by undoing the placeholders first.

Why (2026-11-20): public_backport.py and the T0931 back-port leave a hunk as <file>.rej when its context holds
a public placeholder (192.168.1.x instead of the real LAN, user@example.com instead of a real address...).
This turns each placeholder back into its real value using the private change record
(public-copy-changes.private.txt: rule | real | placeholder | files), but ONLY where a placeholder maps to
exactly one real value in that file's rows (ambiguous ones, like several emails -> one placeholder, are left
alone and the hunk stays rejected). Then it retries `git apply` on the rewritten hunk.

    py -3.14 -I -X utf8 D:/scratchpad/tools/rej_unclean.py [--write] FILE.rej [...]

Without --write it only reports which .rej would apply. Never prints a real value.
OUTCOME: see the T0931 Review line of 2026-11-20.
"""
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

SRC = Path("D:/scratchpad")
RECORD = SRC / "public-copy-changes.private.txt"


def load_map():
    """placeholder -> {file or '*': set(real)}"""
    m = defaultdict(lambda: defaultdict(set))
    for line in RECORD.read_text(encoding="utf-8").splitlines():
        if line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(" | ")]
        if len(parts) < 4 or parts[0] == "port":
            continue
        _rule, real, ph, files = parts[0], parts[1], parts[2], parts[3]
        for f in files.split(", "):
            m[ph][f].add(real)
        m[ph]["*"].add(real)
    return m


def unclean(text, rel, m):
    """Replace each placeholder that has one real value for this file (or overall); longest first."""
    n = 0
    for ph in sorted(m, key=len, reverse=True):
        if ph not in text or len(ph) < 3:
            continue
        reals = m[ph].get(rel) or m[ph]["*"]
        if len(reals) == 1:
            # Whole words only. OUTCOME 2026-11-20: a plain replace turned "user" inside "usernames" and
            # "expanduser", and "the store" inside "the stored", into real values (3 broken lines, fixed by hand).
            import re
            rx = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(ph) + r"(?![A-Za-z0-9_])")
            real = next(iter(reals))
            text, k = rx.subn(lambda _m: real, text)
            n += k
    return text, n


def main(argv):
    write = "--write" in argv
    m = load_map()
    for rej in [a for a in argv if not a.startswith("--")]:
        rp = Path(rej)
        rel = rp.relative_to(SRC).as_posix()[:-4] if rp.is_absolute() else rej.replace("\\", "/")[:-4]
        body = rp.read_text(encoding="utf-8")
        # a .rej starts with "diff a/X b/X\t(rejected hunks)"; make it a normal patch header
        lines = body.splitlines(keepends=True)
        patch = f"--- a/{rel}\n+++ b/{rel}\n" + "".join(lines[1:])
        fixed, n = unclean(patch, rel, m)
        ok = subprocess.run(["git", "-C", str(SRC), "apply", "--check", "--recount", "--ignore-whitespace", "-"], input=fixed, text=True,
                            capture_output=True, check=False).returncode == 0
        print(f"{'APPLIES' if ok else 'still stuck'}  {rel}  ({n} placeholders undone)")
        if ok and write:
            subprocess.run(["git", "-C", str(SRC), "apply", "--recount", "--ignore-whitespace", "-"], input=fixed, text=True, check=True)
            rp.unlink()


if __name__ == "__main__":
    main(sys.argv[1:])
