#!/usr/bin/env python3
"""split_todo_note.py: split the Proton Pass "To Do" note into numbered part notes (2026-10-31).

WHY. The single "To Do" note grew past ~31,000 characters, and Windows will not accept a longer
command line, so `pass-cli item update` started failing (it failed once at 33,000). Splitting by
difficulty tier keeps every note small and lets the site give each part its own page.

    python split_todo_note.py          # DRY RUN: prints the plan and part sizes, changes nothing
    python split_todo_note.py --go     # backs up, creates the 3 part notes, rewrites "To Do" as a short index

Parts:   1 = header + Saturday plan + EASY      2 = MEDIUM + HARD      3 = EXPERT + (context) + PARKED
Notes:   "To Do - Part 1", "To Do - Part 2", "To Do - Part 3", and "To Do" becomes the index with
         a link to every part page on the site.
Safe to re-run? NO: after the split the original text is gone from "To Do", so the script refuses
(it looks for the tier markers) and tells you where the backup is. The note text is plain task
text, never a secret, but it is still never echoed here beyond section names and sizes.
"""
import os
import re
import subprocess
import sys

VAULT = "Notes"
SITE = "https://example.org"
BACKUP = r"D:\homelab-docs\todo-note-backup-pre-split-2026-10-31.txt"
ENV = dict(os.environ, PROTON_PASS_KEY_PROVIDER="fs")

PARTS = [
    (1, "EASY + Saturday plan", None, "--- MEDIUM"),
    (2, "MEDIUM + HARD", "--- MEDIUM", "--- EXPERT"),
    (3, "EXPERT + PARKED", "--- EXPERT", None),
]


def pass_cli(*args):
    p = subprocess.run(["pass-cli", *args], env=ENV, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, p.stdout, p.stderr


def read_note(title):
    code, out, _ = pass_cli("item", "view", "--vault-name", VAULT, "--item-title", title, "--field", "note")
    return out if code == 0 else None


def split(text):
    lines = text.splitlines()
    idx = {}
    for key in ("--- MEDIUM", "--- EXPERT"):
        hits = [i for i, l in enumerate(lines) if l.startswith(key)]
        if len(hits) != 1:
            raise SystemExit("FAIL expected exactly one line starting %r, found %d. Already split?" % (key, len(hits)))
        idx[key] = hits[0]
    cuts = {1: (0, idx["--- MEDIUM"]), 2: (idx["--- MEDIUM"], idx["--- EXPERT"]), 3: (idx["--- EXPERT"], len(lines))}
    return {n: "\n".join(lines[a:b]).rstrip() + "\n" for n, (a, b) in cuts.items()}


def links_block():
    return ("Parts on the site: Part 1 (EASY) %s/todo/part-1/ | Part 2 (MEDIUM, HARD) %s/todo/part-2/ | "
            "Part 3 (EXPERT, PARKED) %s/todo/part-3/ | everything %s/todo/ | docs %s/documents/\n"
            "Index note: To Do. Add a new item to the part that matches its difficulty tier."
            % (SITE, SITE, SITE, SITE, SITE))


def part_text(n, label, body):
    return "=== TO DO - PART %d of 3 (%s) ===\n%s\n\n%s" % (n, label, links_block(), body)


def index_text():
    return ("=== HOMELAB TO-DO (index) ===\n"
            "The list is split into three notes so each stays small. New items go in the part for their tier.\n"
            "  To Do - Part 1: Saturday plan + EASY       %s/todo/part-1/\n"
            "  To Do - Part 2: MEDIUM + HARD              %s/todo/part-2/\n"
            "  To Do - Part 3: EXPERT + PARKED            %s/todo/part-3/\n"
            "  Everything on one page:                    %s/todo/\n"
            "  Saved documentation (offline copies):      %s/documents/\n"
            "Current Project and Completed are separate notes. Start a cleared session with /resume-work.\n"
            % (SITE, SITE, SITE, SITE, SITE))


def write_note(title, text):
    if read_note(title) is not None:
        code, _, err = pass_cli("item", "update", "--vault-name", VAULT, "--item-title", title, "--field", "note=" + text)
    else:
        code, _, err = pass_cli("item", "create", "note", "--vault-name", VAULT, "--title", title, "--note", text)
    return code == 0


def main():
    go = "--go" in sys.argv
    text = read_note("To Do")
    if not text:
        print("FAIL could not read the To Do note")
        return 1
    parts = split(text)
    for n, label, _, _ in PARTS:
        print("part %d (%s): %d characters, %d lines" % (n, label, len(part_text(n, label, parts[n])), parts[n].count("\n")))
    if not go:
        print("DRY RUN only. Re-run with --go to write the notes.")
        return 0
    os.makedirs(os.path.dirname(BACKUP), exist_ok=True)
    with open(BACKUP, "w", encoding="utf-8") as f:
        f.write(text)
    print("backup written: %s" % BACKUP)
    for n, label, _, _ in PARTS:
        ok = write_note("To Do - Part %d" % n, part_text(n, label, parts[n]))
        print("%s To Do - Part %d" % ("PASS" if ok else "FAIL", n))
        if not ok:
            print("stopping: the original To Do note is untouched")
            return 1
    ok = write_note("To Do", index_text())
    print("%s To Do (index)" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
