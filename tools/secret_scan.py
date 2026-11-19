#!/usr/bin/env python3
"""secret_scan.py: find files that hold secret VALUES in plain text. Prints paths and kinds, never values.

Jason, 2026-11-13: "clean up all access to plain text files" holding secrets.

    py -3.14 -I secret_scan.py [ROOT ...]     default roots: www, D:\\ ; writes D:\\scratchpad\\SECRETS-FOUND.md

A hit is a value, not a name: KEY=<long random-looking value> in env/ini/conf/txt files, a pass://
reference is NOT a hit (that is the fix), plus well-known token shapes and private-key headers.
Skipped: binaries, books/PDFs, virtualenvs, build output, Docker backups, the Recycle Bin.
"""
from __future__ import annotations

import math
import re
import sys
from collections import Counter
from pathlib import Path

ROOTS = [Path(r"C:\Jasons New Site\www"), Path("D:\\")]
OUT = Path(r"D:\scratchpad\SECRETS-FOUND.md")
SKIP = re.compile(r"(?i)\\(node_modules|target|\.git|\.venv[^\\]*|venv|site-packages|__pycache__|\$RECYCLE\.BIN|"
                  r"System Volume Information|docker-backups|All-Documents|image-files|Audio-Files|wsl-archive)\\")
TEXT_EXT = {"", ".env", ".txt", ".ini", ".conf", ".cfg", ".json", ".yml", ".yaml", ".toml", ".ps1", ".py", ".sh",
            ".cmd", ".bat", ".js", ".mjs", ".rs", ".md", ".xml", ".csv", ".bak", ".old", ".pem", ".key", ".config"}
SHAPES = {
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "github token": re.compile(r"\b(ghp|gho|ghs|github_pat)_[A-Za-z0-9_]{30,}"),
    "anthropic key": re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"),
    "openai-style key": re.compile(r"\bsk-(proj-)?[A-Za-z0-9_-]{32,}"),
    "aws key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "google key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "slack/telegram token": re.compile(r"\b(xox[abp]-[A-Za-z0-9-]{20,}|\d{8,10}:AA[A-Za-z0-9_-]{30,})"),
    "url with password": re.compile(r"\b[a-z][a-z0-9+.-]*://[^\s:/@]+:[^\s@/]{6,}@"),
}
ASSIGN = re.compile(r"(?im)^\s*(?:export\s+)?([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASS|PASSWORD|PWD|AUTH|CREDENTIAL)[A-Z0-9_]*)"
                    r"\s*[=:]\s*['\"]?([^\s'\"#]{12,})")
PLACEHOLDER = re.compile(r"(?i)^(pass://|\$\{|\$\(|<|changeme|change_me|example|your[_-]|xxx|placeholder|replace|none|null|true|false)")


def entropy(s: str) -> float:
    c = Counter(s)
    return -sum(n / len(s) * math.log2(n / len(s)) for n in c.values())


def scan(p: Path) -> Counter:
    hits: Counter = Counter()
    try:
        if p.stat().st_size > 2_000_000:
            return hits
        t = p.read_text("utf-8", "strict")
    except (OSError, UnicodeDecodeError):
        return hits
    for kind, rx in SHAPES.items():
        n = len(rx.findall(t))
        if n:
            hits[kind] += n
    for _name, val in ASSIGN.findall(t):
        if not PLACEHOLDER.match(val) and entropy(val) > 3.3:
            hits["KEY=value"] += 1
    return hits


def main() -> int:
    roots = [Path(a) for a in sys.argv[1:]] or ROOTS
    rows = []
    for root in roots:
        for p in root.rglob("*"):
            s = str(p)
            if SKIP.search(s + "\\") or not p.is_file():
                continue
            if p.suffix.lower() not in TEXT_EXT and not p.name.startswith(".env"):
                continue
            h = scan(p)
            if h:
                rows.append((s, h))
    lines = ["# Plain-text secrets found (paths and kinds only)", "",
             ("Written by `tools/secret_scan.py`. Values are never shown. Fix: move the value to Proton Pass, "
             "leave a `pass://` reference."), "", "| File | What | Count |", "|---|---|---|"]
    for s, h in sorted(rows):
        lines.append(f"| `{s}` | {', '.join(sorted(h))} | {sum(h.values())} |")
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{'FAIL' if rows else 'PASS'} {len(rows)} files with plain-text secrets -> {OUT}")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
