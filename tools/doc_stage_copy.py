"""Copy the documents missing from the master into a STAGING folder (T0936 step 3, 2026-11-24).

Why staging and not the master: sync_documents.py pushes D:\\ai-stack\\All-Documents nightly to the NUC and
into Paperless, so copying other people's files straight into the master would feed them back into Paperless
and Hermes (found 2026-11-24 10:50, autopilot). Answer on T0936 (recommended default): stage outside the
master; moving into the master waits for Jason's Ask (a/b/c).

Inputs (made by doc_compare_nuc.py and the NAS hash run, both read-only, 2026-11-24):
  <cmp>/nuc-missing-from-alldocs.tsv        sha256, bytes, mtime_utc, path (/home/user/documents/<person>/...)
  <cmp>/paperless-missing-from-alldocs.tsv  sha256, in_nuc_docs, path (./0000010.pdf under Paperless originals)
Output:
  <stage>/nuc/<person>/...   and   <stage>/paperless/<name>   (Paperless rows already on the NUC are skipped)
  <stage>/manifest.tsv       one row per file: source, sha256 expected/got, bytes, staged path, status
  stdout: counts only, never file names (they are personal).

How: one `tar -cf -` stream per source over ssh (Tailscale addresses), read with tarfile in stream mode; each
file's sha256 is computed WHILE it is written, so "verify hashes" needs no second read. Nothing is deleted,
moved or overwritten: an existing staged path is reported 'exists' and left alone. Read-only on NUC and NAS.

Usage: python doc_stage_copy.py <cmp_dir> <stage_dir> [--dry-run]
Outcome log: see the T0936 Review line of 2026-11-24 for the first real run.
"""
import csv
import hashlib
import os
import subprocess
import sys
import tarfile
from pathlib import Path

NUC = "server@100.64.0.10"
NUC_ROOT = "/home/user/documents"
NAS = "jay@100.64.0.12"
NAS_ROOT = "/volume1/Paperless/media/documents/originals"
BAD = '<>:"\\|?*'  # characters Windows refuses in a file name; replaced with _ (the manifest keeps the original)


def safe_rel(name):
    """Turn a tar member name into a safe relative Windows path, or None if it tries to escape the folder."""
    parts = [p for p in name.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    return Path(*["".join("_" if c in BAD or ord(c) < 32 else c for c in p).rstrip(" .") or "_" for p in parts])


def read_lists(cmp_dir):
    """Return {sha: rel_path} for the NUC rows and for the Paperless-only rows."""
    nuc, pap = {}, {}
    with open(cmp_dir / "nuc-missing-from-alldocs.tsv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            p = r["path"]
            if not p.startswith(NUC_ROOT + "/"):
                raise SystemExit("unexpected NUC path root in missing list")
            nuc[r["sha256"]] = p[len(NUC_ROOT) + 1:]
    with open(cmp_dir / "paperless-missing-from-alldocs.tsv", encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            # 52 rows are also on the NUC (in_nuc_docs True): the NUC copy above already stages them.
            if r["in_nuc_docs"] == "True" or r["sha256"] in nuc:
                continue
            pap[r["sha256"]] = r["path"][2:] if r["path"].startswith("./") else r["path"]
    return nuc, pap


def stream(host, root, rels, dest, label, manifest):
    """Pull rels from host:root as one tar stream into dest; hash each file as it is written."""
    want = {}
    for sha, rel in rels.items():
        want.setdefault(rel, set()).add(sha)
    names = b"".join(rel.encode("utf-8") + b"\0" for rel in want)
    # -T - reads the null-separated list from stdin; GNU tar on both ends (NUC 1.35, NAS DSM 1.34, checked 11-24).
    cmd = ["ssh", "-o", "ConnectTimeout=15", host, f"nice -n 19 tar -C '{root}' --null -T - -cf -"]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    proc.stdin.write(names)
    proc.stdin.close()
    seen = set()
    counts = {"ok": 0, "hash_mismatch": 0, "exists": 0, "unsafe": 0, "missing": 0, "not_listed": 0}
    with tarfile.open(fileobj=proc.stdout, mode="r|") as tf:
        for m in tf:
            if not m.isfile():
                continue
            rel = m.name[2:] if m.name.startswith("./") else m.name
            if rel not in want:
                counts["not_listed"] += 1
                continue
            seen.add(rel)
            sp = safe_rel(rel)
            if sp is None:
                counts["unsafe"] += 1
                manifest.writerow([label, rel, "|".join(want[rel]), "", m.size, "", "unsafe"])
                continue
            out = dest / sp
            if out.exists():
                counts["exists"] += 1
                manifest.writerow([label, rel, "|".join(want[rel]), "", m.size, str(out), "exists"])
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            src = tf.extractfile(m)
            tmp = out.with_name(out.name + ".part")
            with open(tmp, "wb") as f:
                for c in iter(lambda: src.read(1 << 20), b""):
                    h.update(c)
                    f.write(c)
            got = h.hexdigest()
            if got in want[rel]:
                tmp.rename(out)
                os.utime(out, (m.mtime, m.mtime))
                counts["ok"] += 1
                status = "ok"
            else:
                # Keep the .part file for inspection; never rename a file whose hash does not match.
                counts["hash_mismatch"] += 1
                status = "hash_mismatch"
            manifest.writerow([label, rel, "|".join(want[rel]), got, m.size, str(out), status])
    err = proc.stderr.read().decode("utf-8", "replace")
    rc = proc.wait()
    for rel in want:
        if rel not in seen:
            counts["missing"] += 1
            manifest.writerow([label, rel, "|".join(want[rel]), "", "", "", "missing_on_source"])
    # tar exit 2 with "Cannot stat" = a listed file vanished since the inventory; counted above, not fatal.
    print(f"{label}: tar rc {rc}, stderr lines {len([l for l in err.splitlines() if l.strip()])}, {counts}")
    return counts


def main():
    """Stage NUC then Paperless files and write the manifest."""
    cmp_dir, stage = Path(sys.argv[1]), Path(sys.argv[2])
    nuc, pap = read_lists(cmp_dir)
    print(f"to stage: nuc {len(nuc)} hashes, paperless-only {len(pap)} hashes")
    if "--dry-run" in sys.argv:
        return
    stage.mkdir(parents=True, exist_ok=True)
    with open(stage / "manifest.tsv", "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["source", "path", "sha256_expected", "sha256_got", "bytes", "staged", "status"])
        stream(NUC, NUC_ROOT, nuc, stage / "nuc", "nuc", w)
        stream(NAS, NAS_ROOT, pap, stage / "paperless", "paperless", w)


if __name__ == "__main__":
    main()
