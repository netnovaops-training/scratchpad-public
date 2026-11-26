"""Read-only: which NUC documents are missing from the documents master (T0936 step 1c/2, 2026-11-24).

Input:  the NUC inventory TSV made by nuc/nuc_doc_inventory.sh (sha256, bytes, mtime_utc, root, path).
Hashes: every file under D:\\ai-stack\\All-Documents (the master, Jason's answer 2026-11-21).
Output: <out_dir>/nuc-missing-from-alldocs.tsv (one row per unique NUC hash not in the master, newest mtime
        wins as the source path) and a short summary on stdout (counts only, no file names).

Nothing is copied, moved or deleted here; step 3 (copy the missing files in) is a separate script.
Outcome log: first run 2026-11-24 autopilot, results in the T0936 Review line of that day.
"""
import csv
import hashlib
import os
import sys
from pathlib import Path

MASTER = Path(r"D:\ai-stack\All-Documents")


def sha(p):
    """sha256 of one file, read in 1 MB chunks."""
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def master_hashes():
    """Every hash in the master folder (all file types: the NUC set is a subset of types anyway)."""
    have, files, errs = set(), 0, 0
    for root, _, names in os.walk(MASTER):
        for n in names:
            try:
                have.add(sha(os.path.join(root, n)))
                files += 1
            except OSError:
                errs += 1
    return have, files, errs


def main():
    """Compare the NUC TSV against the master and write the missing list."""
    tsv, out_dir = Path(sys.argv[1]), Path(sys.argv[2])
    have, mfiles, merrs = master_hashes()
    nuc = {}
    with open(tsv, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            # Only ~/documents rows: /mnt/vault/documents was proven an exact copy (2026-11-24 08:33 run).
            if r["root"] != "/home/user/documents":
                continue
            cur = nuc.get(r["sha256"])
            if cur is None or r["mtime_utc"] > cur["mtime_utc"]:
                nuc[r["sha256"]] = r
    missing = [r for h, r in nuc.items() if h not in have]
    out = out_dir / "nuc-missing-from-alldocs.tsv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["sha256", "bytes", "mtime_utc", "path"])
        for r in sorted(missing, key=lambda r: r["path"]):
            w.writerow([r["sha256"], r["bytes"], r["mtime_utc"], r["path"]])
    by_top = {}
    for r in missing:
        top = r["path"].split("/")[4] if r["path"].count("/") > 4 else "(root)"
        by_top[top] = by_top.get(top, 0) + 1
    print(f"master files {mfiles} (unreadable {merrs}), unique hashes {len(have)}")
    print(f"nuc unique hashes {len(nuc)}, already in master {len(nuc) - len(missing)}, missing {len(missing)}, "
          f"missing bytes {sum(int(r['bytes']) for r in missing)}")
    print("missing per NUC person folder:", dict(sorted(by_top.items())))


if __name__ == "__main__":
    main()
