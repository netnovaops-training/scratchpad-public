"""'the previous owner' -> 'the author' (Korean 이전 주인 -> 제작자, with the right particles)."""
import re
import subprocess
import sys
from pathlib import Path

ROOT = sys.argv[1]
files = subprocess.run(["git", "-c", "core.quotepath=off", "ls-files"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", check=False).stdout.splitlines()
BINARY = re.compile(r"\.(pdf|png|jpg|jpeg|webp|gif|ico|svg|apk|jar|gz|zip|tar|mp3|woff2?|ttf|keystore)$", re.IGNORECASE)
PAIRS = [
    ("FILES-FROM-PREVIOUS-OWNER", "FILES-FROM-AUTHOR"),
    ("from-the-previous-owner", "from-the-author"),
    ("THE PREVIOUS OWNER", "THE AUTHOR"),
    ("Previous Owner Signature Sign-Off", "Author Signature Sign-Off"),
    ("the previous owner's", "the author's"), ("The previous owner's", "The author's"),
    ("the previous owner", "the author"), ("The previous owner", "The author"),
    ("Previous owner's", "Author's"), ("Previous owner", "Author"), ("previous owner's", "author's"),
    ("previous owner", "author"),
]
# Korean: 주인 ends in a consonant, 제작자 in a vowel, so some particles change.
KO = [("이전 주인이", "제작자가"), ("이전 주인을", "제작자를"), ("이전 주인은", "제작자는"),
      ("이전 주인과", "제작자와"), ("이전 주인", "제작자")]
changed = []
for f in files:
    if BINARY.search(f):
        continue
    p = f"{ROOT}/{f}"
    try:
        raw = Path(p).read_bytes().decode("utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        continue
    s = raw
    for a, b in PAIRS + KO:
        s = s.replace(a, b)
    if s != raw:
        Path(p).write_text(s, encoding="utf-8", newline="")
        changed.append(f)
print(len(changed), "files changed")
