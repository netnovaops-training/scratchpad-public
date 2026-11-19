"""archive_by_inventory.py: move what SCRATCHPAD.md marks "outdated" / "one time use" out of ai-stack.

Written 2026-11-12 (Claude). Jason: "move un used code to d in archive". Reads the DECISIONS table in
ai-stack\\scripts\\build_code_inventory.py (the same table that wrote SCRATCHPAD.md), so the list
and the move can never disagree.

    python -I archive_by_inventory.py plan | apply | undo

  outdated      -> D:\\ai-stack-archive\\outdated\\<name>
  one time use  -> D:\\ai-stack-archive\\one-time-use\\<name>
  their docs/code mirror (docs\\code\\<name> or <name>.md) -> <same place>\\_docs\\
  pi-digits\\bak-before-* copies -> D:\\ai-stack-archive\\outdated\\pi-digits-backups\\
Moves across drives: copy, compare file count and bytes, then remove the original. Logged to
D:\\ai-stack-archive\\moves-inventory.csv for undo. Garden / Robin entries are never in the table.
"""
import csv
import os
import shutil
import sys

AI = r"C:\Jasons New Site\www\ai-stack"
ARCH = r"D:\ai-stack-archive"
LOG = os.path.join(ARCH, "moves-inventory.csv")
sys.path.insert(0, os.path.join(AI, "scripts"))
import build_code_inventory as inv


def size(p):
    if os.path.isfile(p):
        return 1, os.path.getsize(p)
    n = b = 0
    for dp, _d, fns in os.walk(p):
        for f in fns:
            n += 1
            b += os.path.getsize(os.path.join(dp, f))
    return n, b


def pairs():
    out = []
    for name, (status, _why) in inv.DECISIONS.items():
        if status not in inv.WHERE_TO:
            continue
        dest = inv.WHERE_TO[status]
        src = os.path.join(AI, name)
        if os.path.exists(src):
            out.append((src, os.path.join(dest, name)))
        for doc in (os.path.join(AI, "docs", "code", name), os.path.join(AI, "docs", "code", name + ".md")):
            if os.path.exists(doc):
                out.append((doc, os.path.join(dest, "_docs", os.path.basename(doc))))
    pd = os.path.join(AI, "pi-digits")
    for d in sorted(os.listdir(pd)) if os.path.isdir(pd) else []:
        if d.startswith("bak-before-"):
            out.append((os.path.join(pd, d), os.path.join(ARCH, "outdated", "pi-digits-backups", d)))
    return out


def move(src, dst):
    if os.path.exists(dst):
        raise FileExistsError(dst)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    before = size(src)
    if os.path.isdir(src):
        shutil.copytree(src, dst, symlinks=True)
    else:
        shutil.copy2(src, dst)
    if size(dst) != before:
        raise OSError("copy check failed for " + src)
    if os.path.isdir(src):
        shutil.rmtree(src)
    else:
        os.remove(src)


def main(mode):
    if mode == "undo":
        with open(LOG, encoding="utf-8") as fh_:
            for src, dst in reversed(list(csv.reader(fh_))):
                if os.path.exists(dst) and not os.path.exists(src):
                    move(dst, src)
        print("UNDO done")
        return 0
    ps = pairs()
    if mode == "plan":
        for s, d in ps:
            print(f"{os.path.relpath(s, AI)!s:<60} -> {d}")
        print(f"PLAN {len(ps)} entries")
        return 0
    ok = fail = 0
    with open(LOG, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        for s, d in ps:
            try:
                move(s, d)
                w.writerow([s, d])
                ok += 1
            except OSError as e:
                fail += 1
                print("FAIL", s, type(e).__name__, e)
            fh.flush()
    print(f"APPLY moved {ok}, failed {fail} (log {LOG})")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
