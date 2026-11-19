import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
files = [root/"00-READ-ME-FIRST-KO.html", root/"00-READ-ME-FIRST.html", root/"WHAT-GETS-INSTALLED.ko.md", root/"WHY-THESE-PROGRAMS.ko.md"] + sorted((root/"START-HERE"/"ko").glob("*.md"))
bad = 0
for f in files:
    t = f.read_text(encoding="utf-8")
    links = re.findall(r'\]\(([^)\s]+)\)', t) + re.findall(r'href="([^"]+)"', t)
    for l in links:
        if l.startswith(("http", "#", "mailto")): continue
        p = (f.parent / l.split("#")[0]).resolve()
        if not p.exists():
            bad += 1; print("BROKEN", f.name, l)
    print(f.name, len(links), "links")
print("broken:", bad)
