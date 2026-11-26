#!/usr/bin/env python3
"""doc_backfill_nuc.py - first-draft docs for the NUC and Pi scripts in the Scratchpad, by the LOCAL model.

Why (Jason 2026-11-21: "all code all variables" in the Scratchpad, viewed like a magazine): 75 of the 80 NUC/Pi
code files in nuc/ and pi/ had no doc, so their flip-book pages say "Doc: NOT YET". This reuses the PC pipeline
ai-stack/scripts/doc_backfill.py (same model qwen3-coder-docs on the PC GPU, same style rules, same safety rails:
never edits code, History built from git so it cannot be invented, names not in the file -> needs-review,
secret-shaped answers not saved, reviewed: no) and changes only:
  - REPO/OUT: D:\\scratchpad, docs/<scratchpad path>.md (the path manga.py reads)
  - the prompt asks for one more section, "## Variables" (a Name | What it is table), and every Name in that
    table must appear in the file, else the doc is flagged needs-review (VARCHECK below)

    py -3.14 -I D:\\scratchpad\\tools\\doc_backfill_nuc.py --list     which files would be drafted
    py -3.14 -I D:\\scratchpad\\tools\\doc_backfill_nuc.py [N]        draft up to N files (default all), resumable

A draft is NOT a finished doc: Claude (or Jason) reads it against the code, fixes it, and flips `reviewed: yes`.
Log: D:\\ai-doc-trial\\backfill-nuc.log (PASS/FAIL lines). Writes nothing outside docs/; no commit, no push.

Variables: BACKFILL (path of the PC pipeline module), db (that module, re-pointed), FILES (drafting order),
VAR_ROW (pattern of one Variables table row).
OUTCOME 2026-11-21: see the overnight RUN LOG (first batch).
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

BACKFILL = Path(r"C:\Jasons New Site\www\ai-stack\scripts\doc_backfill.py")
spec = importlib.util.spec_from_file_location("doc_backfill", BACKFILL)
db = importlib.util.module_from_spec(spec)
spec.loader.exec_module(db)

HOME = Path(r"D:\scratchpad")
db.REPO = str(HOME)
db.OUT = str(HOME / "docs")
db.LOG = r"D:\ai-doc-trial\backfill-nuc.log"
db.STYLE = str(BACKFILL.parent.parent / "docs" / "COMMENTING-STYLE.md")   # the PC pipeline's rules (Jason owns)
db.PROMPT = db.PROMPT.replace(
    "## Key details\n## Side effects",
    "## Key details\n## Variables\n## Side effects").replace(
    "Do NOT write History",
    "Under Variables write a Markdown table with the header | Name | What it is | listing every constant, setting "
    "and environment variable the file defines or reads, using the exact names from the file; one short line each. "
    "Do NOT write History")
db.PROMPT_MERGE = db.PROMPT.split("FILE: {name}")[0] + db.PROMPT_MERGE.split("FILE: {name}", 1)[1]
VAR_ROW = re.compile(r"^\|\s*`?([^|`]+?)`?\s*\|", re.M)

sys.path.insert(0, str(HOME / "tools"))
import manga  # noqa: E402  (nuc_rows: every tracked NUC/Pi code file; documented yes/no)

_orig_process = db.process


def process(rel: str) -> str:
    """db.process + the Variables check: a Name not found in the file marks the draft needs-review."""
    res = _orig_process(rel)
    doc = HOME / "docs" / (rel + ".md")
    if res == "done" and doc.is_file():
        text = doc.read_text(encoding="utf-8")
        src = (HOME / rel).read_text(encoding="utf-8", errors="replace")
        sec = re.search(r"## Variables\n(.+?)(?:\n## |\Z)", text, re.S)
        names = [n.strip() for n in VAR_ROW.findall(sec.group(1))] if sec else []
        names = [n for n in names if n.lower() not in ("name", "") and not set(n) <= set("-: ")]
        missing = [n for n in names for part in [n.split(",")[0].strip()] if part and part not in src]
        if not sec:
            text = text.replace("needs-review: no", "needs-review: yes, no Variables section", 1)
        elif missing:
            text = re.sub(r"needs-review: no", "needs-review: yes, variables not in the file: " + " ".join(missing[:8]),
                          text, count=1)
        doc.write_text(text, encoding="utf-8")
        db.log(f"VARS {rel}: {len(names)} rows, {len(missing)} not in the file")
    return res


def main(argv: list[str]) -> int:
    files = [r["scratchpad_path"] for r in manga.nuc_rows() if r["documented"] == "no"]
    if "--list" in argv:
        print("\n".join(files))
        print(f"{len(files)} file(s) without a doc")
        return 0
    limit = int(argv[0]) if argv and argv[0].isdigit() else len(files)
    counts: dict[str, int] = {}
    for rel in files[:limit]:
        r = process(rel)
        counts[r] = counts.get(r, 0) + 1
    print("summary:", counts)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
