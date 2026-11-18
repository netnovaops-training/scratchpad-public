#!/usr/bin/env python3
"""todo_renumber.py - order the whole to-do list by what comes next, then renumber T0001.. in that order.

Jason, 2026-11-18: "every item arranged numerically by order of what's next to be done ... ordered by importance
and recommended, not shuffled ... not jumping around to different subjects. Please renumber them."

Run on the NUC:  python3 ~/learning-src/todo_renumber.py           dry run: prints the first 40 and checks
                 python3 ~/learning-src/todo_renumber.py --write   writes (shared lock, backups first)
Order (subjects stay together; todo_reorder.py's parse/render are reused):
  item key   [first] pin, then P1 < P2 < P3 < PARKED, then necessary < optional < wish, then [rec] first;
             ties keep today's order, so a project's step-by-step items stay in sequence
  groups     sections inside a project, projects inside a computer, and the computers themselves are ordered
             by their most important open item (todo_reorder.best)
  numbers    open items T0001.. in file order; then ticked items left in TODO-ALL.md; then TODO-DONE.md items
References: every T#### that is an old id is rewritten in one pass (no chains) in TODO-ALL.md, TODO-DONE.md,
  todo-code/*.md (files renamed via a temp name), autopilot-skip.txt, QUESTIONS.md, docs/**/*.md, audit/**/*.md.
  Each item gets "Was: T#### (renumbered 2026-11-18)"; the full map is TODO-RENUMBER-2026-11-18.csv.
Safety: under ~/.todo-all.lock; refuses if the autopilot lock is fresh; checks no id is lost or doubled and
  that the item count is unchanged; backups go to ~/archive/<date>/renumber/ before anything is written.
OUTCOME: (2026-11-18) written for the request above; result in the To-do system walkthrough.
"""
import csv
import fcntl
import re
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import todo_reorder as tr  # noqa: E402

HOME = Path.home()
NH = HOME / "nuc-homelab"
LIST, DONE = NH / "TODO-ALL.md", NH / "TODO-DONE.md"
CODE = NH / "todo-code"
LOCK = HOME / ".todo-all.lock"
TAG = "2026-11-18"
ID = re.compile(r"^- \[( |x|X)\] \*\*(T\d{4,})\*\*")
REF = re.compile(r"(?<![A-Za-z0-9])T(\d{4})(?![0-9])")
NEED = {"necessary": 0, "optional": 1, "wish": 2}


def key(first_line):
    """Lower = do sooner. Done items last."""
    if re.match(r"^- \[[xX]\]", first_line):
        return (9, 9, 9)
    tags = re.findall(r"\[([A-Za-z0-9-]+)\]", first_line[6:])
    pin = 0 if "first" in tags else 1
    pri = min((tr.P[t] for t in tags if t in tr.P), default=5)
    return (pin, pri, min((NEED[t] for t in tags if t in NEED), default=1), 0 if "rec" in tags else 1)


def best(keys):
    keys = list(keys)
    top = min(keys, default=(9, 9, 9, 9))
    return top + (-keys.count(top),)


def order(comps):
    for comp in comps:
        for proj in comp[2]:
            for sec in proj[2]:
                sec[2].sort(key=lambda b: key(b[0]))
            proj[2].sort(key=lambda s: best(key(b[0]) for b in s[2]))
        comp[2].sort(key=lambda p: best(key(b[0]) for s in p[2] for b in s[2]))
    comps.sort(key=lambda c: best(key(b[0]) for p in c[2] for s in p[2] for b in s[2]))


def main(write):
    lk = open(LOCK, "a")
    fcntl.flock(lk, fcntl.LOCK_EX)
    ap = HOME / ".todo-autopilot.lock"
    if write and ap.exists() and time.time() - ap.stat().st_mtime < 110 * 60:
        sys.exit("STOP: the to-do autopilot is running (fresh ~/.todo-autopilot.lock); try after it finishes")
    old_text = LIST.read_text(encoding="utf-8")
    pre, comps = tr.parse(old_text)
    order(comps)
    blocks = [b for c in comps for p in c[2] for s in p[2] for b in s[2]]
    old_ids = [ID.match(b[0]).group(2) for b in blocks]
    if len(set(old_ids)) != len(old_ids):
        sys.exit("STOP: duplicate ids in TODO-ALL.md: %s" % sorted({i for i in old_ids if old_ids.count(i) > 1}))
    # Numbers follow work order, not file position: tier first ([first] pins, then P1, P2, P3, PARKED), and
    # inside a tier grouped by project (projects ranked across ALL computers by their best item), then section,
    # then the item order above. Why: grouping by computer first numbered the PC's [first] item after ~500
    # NUC items (dry run 11-18), and todo_merge.py keeps one heading per computer, so the file must stay grouped.
    proj_rank, sec_rank, pos = {}, {}, {}
    projs = sorted(((c, p) for c in comps for p in c[2]), key=lambda cp: best(key(b[0]) for s in cp[1][2] for b in s[2]))
    for r, (c, p) in enumerate(projs):
        for sr, s in enumerate(p[2]):
            for bi, b in enumerate(s[2]):
                proj_rank[id(b)], sec_rank[id(b)], pos[id(b)] = r, sr, bi
    open_b = sorted((b for b in blocks if b[0].startswith("- [ ]")),
                    key=lambda b: (key(b[0])[:2], proj_rank[id(b)], sec_rank[id(b)], pos[id(b)]))
    done_b = [b for b in blocks if not b[0].startswith("- [ ]")]
    done_text = DONE.read_text(encoding="utf-8") if DONE.exists() else ""
    done_ids = [m.group(2) for m in (ID.match(l) for l in done_text.split("\n")) if m]
    seq = [ID.match(b[0]).group(2) for b in open_b + done_b] + [i for i in done_ids if i not in old_ids]
    mapping = {old: "T%04d" % n for n, old in enumerate(seq, 1)}
    if len(set(mapping.values())) != len(mapping):
        sys.exit("STOP: new ids collide")

    def sub(text):
        return REF.sub(lambda m: mapping.get("T" + m.group(1), m.group(0)), text)

    titles = {}
    for b in blocks:
        old = ID.match(b[0]).group(2)
        titles[old] = re.sub(r"^- \[.\] \*\*T\d+\*\*\s*", "", b[0])[:100]
        b[:] = [sub(l) for l in b]
        b.append("  Was: %s (renumbered %s)" % (old, TAG))
    pre = [sub(l) for l in pre]
    for c in comps:
        c[1][:] = [sub(l) for l in c[1]]
        for p in c[2]:
            p[1][:] = [sub(l) for l in p[1]]
            for s in p[2]:
                s[1][:] = [sub(l) for l in s[1]]
    new_text = tr.render(pre, comps)
    new_ids = [m.group(2) for m in (ID.match(l) for l in new_text.split("\n")) if m]
    assert len(new_ids) == len(old_ids) and len(set(new_ids)) == len(new_ids), "item count changed"
    print("open items %d, ticked in list %d, in TODO-DONE %d; computers in order: %s" % (
        len(open_b), len(done_b), len(done_ids), ", ".join(c[0][3:] for c in comps)))
    for b in open_b[:40]:
        print("  %s  (was %s)" % (b[0][:110], b[-1].split()[1]))
    if not write:
        print("dry run: nothing written (add --write)")
        return
    bak = HOME / "archive" / time.strftime("%Y-%m-%d") / "renumber"
    bak.mkdir(parents=True, exist_ok=True)
    others = [NH / "autopilot-skip.txt", NH / "QUESTIONS.md"] + sorted((NH / "docs").rglob("*.md")) + \
        sorted((NH / "audit").rglob("*.md"))
    for f in [LIST, DONE] + [f for f in others if f.exists()]:
        if f.exists():
            dst = bak / f.relative_to(NH)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, dst)
    if CODE.is_dir():
        shutil.copytree(CODE, bak / "todo-code", dirs_exist_ok=True)
    LIST.write_text(new_text, encoding="utf-8")
    if done_text:
        DONE.write_text(sub(done_text), encoding="utf-8")
    for f in others:
        if f.exists():
            f.write_text(sub(f.read_text(encoding="utf-8")), encoding="utf-8")
    if CODE.is_dir():
        moves = []
        for f in sorted(CODE.glob("T*.md")):
            new = mapping.get(f.stem)
            body = sub(f.read_text(encoding="utf-8"))
            tmp = f.with_name("_renum_" + f.name)
            tmp.write_text(body, encoding="utf-8")
            f.unlink()  # its copy is in the backup folder above
            moves.append((tmp, CODE / ((new or f.stem) + ".md")))
        for tmp, dst in moves:
            tmp.rename(dst)
    with open(NH / ("TODO-RENUMBER-%s.csv" % TAG), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["old", "new", "title"])
        for old in seq:
            w.writerow([old, mapping[old], titles.get(old, "(TODO-DONE.md)")])
    print("written; backups in", bak)


if __name__ == "__main__":
    main("--write" in sys.argv)
