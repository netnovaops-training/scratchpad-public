#!/usr/bin/env python3
"""make_tools_index.py - write TOOLS.md: one plain line per script in the Scratchpad (2026-11-19).

    python tools/make_tools_index.py            rewrite D:/scratchpad/TOOLS.md
    python tools/make_tools_index.py --check    exit 1 if TOOLS.md is out of date

Why: Jason 2026-11-19: "a summary of what each tool does in the scratchpad ... so GitHub has a way of
keeping an updated readable list of the scripts". The line for each script is its own first docstring /
header-comment line, so the list stays true when a script's header changes. TOOLS.md is copied to the
public repo by public_copy.py like any other tracked file.
"""
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "TOOLS.md"
EXT = {".py", ".sh", ".ps1", ".cmd", ".bat"}


def summary(p: Path) -> str:
    """The first meaningful line of the script's docstring or header comment."""
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix == ".py":
        try:
            doc = ast.get_docstring(ast.parse(text)) or ""
        except SyntaxError:
            doc = ""
        if doc.strip():
            return doc.strip().splitlines()[0]
    for line in text.splitlines()[:15]:
        s = line.strip()
        if s.startswith(("#!", "#requires", "@echo")) or not s:
            continue
        if s.startswith(("#", "::", "REM ", "rem ")):
            s = re.sub(r"^(#+|::|REM |rem )\s*", "", s)
            if s:
                return s
        else:
            break
    return "(no header comment yet)"


def build() -> str:
    """Markdown list of every tracked script, grouped by folder."""
    files = subprocess.run(["git", "-C", str(ROOT), "ls-files"], capture_output=True, text=True,
                           check=True).stdout.split("\n")
    groups: dict[str, list[str]] = {}
    for f in sorted(files):
        p = ROOT / f
        if p.suffix.lower() not in EXT or not p.is_file() or "/tests/" in f or f.startswith("docs/"):
            continue
        line = summary(p)
        line = re.sub(rf"^{re.escape(p.name)}\s*[-:]\s*", "", line)  # drop a repeated file name
        groups.setdefault(str(Path(f).parent).replace("\\", "/"), []).append(f"- `{p.name}`: {line}")
    out = ["# Scratchpad tools", "",
           ("One line per script, taken from each script's own header. Regenerate with "
            "`python tools/make_tools_index.py`."), ""]
    for folder in sorted(groups):
        out += [f"## {folder}", ""] + groups[folder] + [""]
    return "\n".join(out)


if __name__ == "__main__":
    new = build()
    if "--check" in sys.argv:
        old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        sys.exit(0 if old == new else 1)
    OUT.write_text(new, encoding="utf-8", newline="\n")
    print(f"{OUT}: {new.count(chr(10) + '- ')} scripts")
