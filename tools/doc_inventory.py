"""Read-only: document files under C:\\Jasons New Site\\www outside All-Documents (T0936 step 1b, 2026-11-20).
Writes a CSV: path, size, sha256, in_all_docs (same sha256 already in D:\\ai-stack\\All-Documents),
code_ref (file name appears in a code/text file of the www tree), git_tracked."""
import csv
import hashlib
import os
import subprocess
import sys
from pathlib import Path

WWW = Path(r"C:\Jasons New Site\www")
DOCS = Path(r"D:\ai-stack\All-Documents")
OUT = Path(sys.argv[1])
EXT = {".pdf", ".doc", ".docx", ".odt", ".rtf", ".epub", ".xlsx", ".xls", ".pptx", ".ppt", ".ods", ".odp"}
SKIP = {"node_modules", "target", ".git", "__pycache__", ".venv", "venv", "All-Documents",
        "All-Documents.old-20261120", "site-packages", "dist", "build"}
CODE_EXT = {".py", ".rs", ".js", ".ts", ".html", ".php", ".sh", ".ps1", ".cmd", ".yml", ".yaml", ".toml", ".json",
            ".md", ".conf", ".txt", ".css", ".kt", ".swift", ".go"}


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


have = set()
for root, _, files in os.walk(DOCS):
    for f in files:
        try:
            have.add(sha(os.path.join(root, f)))
        except OSError:
            pass

docs, code = [], []
for root, dirs, files in os.walk(WWW):
    dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
    for f in files:
        p = Path(root) / f
        ext = p.suffix.lower()
        if ext in EXT:
            docs.append(p)
        elif ext in CODE_EXT and p.stat().st_size < 2_000_000:
            code.append(p)

names = {p.name for p in docs}
referenced = set()
for c in code:
    try:
        t = c.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        continue
    for n in names:
        if n in t:
            referenced.add(n)


def tracked(p):
    r = subprocess.run(["git", "-C", str(p.parent), "ls-files", "--error-unmatch", p.name],
                       capture_output=True, check=False)
    return r.returncode == 0


with OUT.open("w", newline="", encoding="utf-8") as fh:
    w = csv.writer(fh)
    w.writerow(["path", "size", "sha256", "in_all_docs", "code_ref", "git_tracked"])
    for p in docs:
        s = sha(p)
        w.writerow([str(p), p.stat().st_size, s, s in have, p.name in referenced, tracked(p)])
print("documents found:", len(docs))
