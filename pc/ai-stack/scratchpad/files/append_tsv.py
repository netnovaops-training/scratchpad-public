"""Append new translations to lang/ko.tsv without disturbing existing lines.

- Keeps every existing line (including lines added by other people).
- Removes only translation lines whose English key is no longer in garden.sh.
- Appends lines from the given "EN => KO" file for strings that are missing.
"""
import sys
from pathlib import Path

tsv = Path(sys.argv[1]); src = Path(sys.argv[2])
sys.path.insert(0, str(tsv.parent))
import extract_strings

static, _ = extract_strings.collect()
found = {t for t, _ in static}
lines = tsv.read_bytes().decode("utf-8").split("\n")
have, kept, dropped = set(), [], []
for l in lines:
    if l and not l.startswith("#") and "\t" in l:
        en = l.split("\t", 1)[0]
        if en not in found:
            dropped.append(en); continue
        have.add(en)
    elif l.startswith("#=\t"):
        have.add(l[3:])
    kept.append(l)
while kept and kept[-1] == "":
    kept.pop()

new, neutral = [], []
for l in src.read_text(encoding="utf-8").split("\n"):
    if not l.strip():
        continue
    en, ko = l.split(" => ", 1)
    if en in have or en not in found:
        if en not in found: print("SKIP (not in garden.sh):", en)
        continue
    assert "\t" not in en + ko
    (neutral if ko == "#=" else new).append((en, ko))

out = kept + ["", "# Added for the NAS finder, NAS change and whole-folder copy screens:"]
out += [f"{e}\t{k}" for e, k in new] + [f"#=\t{e}" for e, _ in neutral]
tsv.write_bytes(("\n".join(out) + "\n").encode("utf-8"))
print("dropped orphans:", len(dropped)); [print("  -", d) for d in dropped]
print("appended:", len(new), "+", len(neutral), "same-in-both")
