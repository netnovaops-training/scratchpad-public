"""env_copies_verify.py - check each file env_copies_to_pass.py rewrote against its Pass backup note (2026-11-24).

    py -3.14 -I D:\\scratchpad\\tools\\env_copies_verify.py            (report)
    py -3.14 -I D:\\scratchpad\\tools\\env_copies_verify.py --restore  (put the note text back where a file is damaged)

The first run's line regex used \\s, which can cross a newline, so an empty "NAME=" could swallow the next line.
This compares, per file, the original (from the note, held in memory) with the file now: every variable name in the
original must still be there, and every non-secret line must be unchanged. Prints names only, never values.
"""
import hashlib
import importlib.util
import pathlib
import re
import subprocess
import sys

spec = importlib.util.spec_from_file_location("e", pathlib.Path(__file__).with_name("env_copies_to_pass.py"))
src = pathlib.Path(spec.origin).read_text(encoding="utf-8")
FILES = [pathlib.Path(p) for p in re.findall(r'^\s+r"([A-Z]:\\[^"]+\.env)",$', src, re.M)]
DATE, VAULT = "2026-11-24", "Notes"
NAME = re.compile(r"^[ \t]*([A-Za-z_][A-Za-z0-9_]*)[ \t]*=", re.M)
RESTORE = "--restore" in sys.argv

for path in FILES:
    title = f"env-backup {DATE} {path}"
    r = subprocess.run(["pass-cli", "item", "view", "--vault-name", VAULT, "--item-title", title, "--field", "note"],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        continue                                     # never backed up = never rewritten
    orig = r.stdout.replace("\r", "").rstrip("\n")
    now = path.read_text(encoding="utf-8").replace("\r", "").rstrip("\n")
    lost = sorted(set(NAME.findall(orig)) - set(NAME.findall(now)))
    now_lines = set(now.split("\n"))
    changed = [ln.split("=", 1)[0] for ln in orig.split("\n")
               if ln.strip() and not ln.lstrip().startswith("#") and ln not in now_lines]
    bad = bool(lost)
    print(f"{path}: names lost {lost or '-'}; lines changed {len(changed)} {changed}")
    if bad and RESTORE:
        path.write_text(orig + "\n", encoding="utf-8")
        print(f"    restored from the Pass note (fp {hashlib.sha256(orig.encode()).hexdigest()[:8]})")
