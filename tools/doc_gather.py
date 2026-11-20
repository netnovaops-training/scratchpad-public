"""Move (or copy) www documents into D:\\ai-stack\\All-Documents (T0936 step 1b, 2026-11-20).

Rules (plan approved by Jason): files code reads or git tracks are COPIED (they stay); the rest are MOVED:
copy, sha256 check, then the original goes to the Recycle Bin (restorable). Same-sha files already in
All-Documents are skipped. Every action is logged to D:\\doc-gather\\moves-20261120.csv (action, from, to, sha256).
    py -3.14 -I -X utf8 doc_gather.py INVENTORY.csv [--write]
"""
import csv
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

DOCS = Path(r"D:\ai-stack\All-Documents")
LOG = Path(r"D:\doc-gather\moves-20261120.csv")
WWW = Path(r"C:\Jasons New Site\www")


def dest_for(src: Path) -> Path:
    rel = src.relative_to(WWW)
    parts = rel.parts
    if parts[:2] == ("ai-stack", "documents"):
        return DOCS / "Robin" / Path(*parts[2:])
    if parts[0] == "ai-stack" and parts[1].startswith("Setup documentation"):
        return DOCS / "Projects" / "Garden" / Path(*parts[1:])
    if parts[0] == "Casey":
        return DOCS / "Casey" / Path(*parts[1:])
    if parts[0] == "Family":
        return DOCS / "Family" / Path(*parts[1:])
    if parts[0] == "reports":
        return DOCS / "Projects" / "Reports" / Path(*parts[1:])
    return DOCS / "Projects" / Path(*parts)


def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def recycle(p: Path):
    import os
    ps = ("Add-Type -AssemblyName Microsoft.VisualBasic; "
          "[Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($env:RECYCLE_ME, 'OnlyErrorDialogs', 'SendToRecycleBin')")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, env={**os.environ, "RECYCLE_ME": str(p)})


def main():
    inv, write = Path(sys.argv[1]), "--write" in sys.argv
    rows = list(csv.DictReader(inv.open(encoding="utf-8")))
    counts = {"move": 0, "copy": 0, "skip-dup": 0, "exists": 0, "fail": 0}
    log = []
    for r in rows:
        src = Path(r["path"])
        if r["in_all_docs"] == "True":
            counts["skip-dup"] += 1
            continue
        keep = r["code_ref"] == "True" or r["git_tracked"] == "True"
        dst = dest_for(src)
        action = "copy" if keep else "move"
        if dst.exists():
            if not src.exists() or sha(dst) != r["sha256"]:
                counts["exists"] += 1
                continue
            already = True              # copied by an earlier, interrupted run: finish the move
        else:
            already = False
        counts[action] += 1
        if write:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not already:
                shutil.copy2(src, dst)
            if sha(dst) != r["sha256"]:
                counts["fail"] += 1
                dst.unlink()
                continue
            if action == "move":
                recycle(src)
            log.append([action, str(src), str(dst), r["sha256"]])
    if write:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        new = not LOG.exists()
        with LOG.open("a", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["action", "from", "to", "sha256"])
            w.writerows(log)
    print(("DONE " if write else "DRY ") + str(counts))


if __name__ == "__main__":
    main()
