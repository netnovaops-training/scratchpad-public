"""Nightly: new and changed documents in All-Documents go to the NUC and into Paperless.

CHANGED 2026-11-12 (Jason: "a unified document folder to feed hermes ... that stays synced",
"without duplicating the files"): the source is now ai-stack\\All-Documents, where every document
was MOVED (sorted Books / PDF / Word / Text / Markdown / Slides / Spreadsheets / Projects). The old
first step, collect_documents.py COPYING work docs into ai-stack\\documents, made a second copy of
everything; it was retired to D:\\ai-stack-archive\\archive\\scripts\\ on 2026-11-12, and the copies
in ai-stack\\documents were folded in (exact copies to the Recycle Bin as "reviewed - ...", stale
code/notes snapshots to D:\\ai-stack-archive\\documents-leftovers). Only Garden / Robin material is left
in ai-stack\\documents, untouched on Jason's rule. Log: D:\\doc-gather\\moves-fold.csv.

Chain (each step is safe to repeat):
  1. tar over ssh           sends only files changed since the last run to the NUC, into
                            ~/documents/jason/All-Documents/ . That folder is inside the mounts the
                            NUC containers already have (/shelves, /data/documents, /srv/books,
                            /books/jason), so they see it with no compose change; /data/documents/
                            jason is owner-only in INDEX_OWNERS.
  2. ~/load_all.sh on NUC   paperless_load.py per top folder: uploads files Paperless does not
                            have yet, tag = the folder (PDF, Word, ...). Since 2026-11-12 Books/
                            is left to Kavita and pdf/docx over 25 MB are skipped; then
                            paperless_people.py adds the person tag and locks "private" documents
                            away from Hermes's account.
One way only (PC -> NUC). A file deleted or renamed on the PC is not removed on the NUC.
Hermes needs no separate feed: its docs_search reads Paperless live, so a document is findable
as soon as Paperless has indexed it (a minute or two for text, longer for big PDFs).

Run by the Windows task "DocsToPaperless". Log: D:\\paperless-feed\\sync.log.
No shell=True anywhere; the tar stream is built in Python and written to ssh's stdin.
"""
from __future__ import annotations

import datetime
import os
import re
import subprocess
import sys
import tarfile

HERE = os.path.dirname(os.path.abspath(__file__))

DOCS = r"D:\ai-stack\All-Documents"  # 2026-11-20: moved to D: (the one documents home); the old ai-stack path is a junction
REMOTE = "~/documents/jason/All-Documents"
# New stamp file for the new source, so the first run sends everything (moved files keep their
# old modified times, which the previous stamp would have treated as already sent).
STAMP = r"D:\paperless-feed\last_sync_all_documents.txt"
LOG = r"D:\paperless-feed\sync.log"
NUC = "server@192.168.1.86"
# Secret-shaped files never leave the PC, even if one lands in All-Documents again (2026-11-12:
# two BitLocker recovery keys and a wpa_supplicant.conf had). Word's "~$" lock files are junk.
# 2026-11-22 (T0008 step 2, autopilot): added "secrets?|credentials?|passwd", SSH key names
# (id_rsa, id_ed25519, id_ecdsa), key/vault file types (.kdbx .pfx .p12 .ovpn .ppk) and .env.<x>.
# WHY: the 11-16 check found All-Documents/Projects/PC-to-Linux-2026-10/secrets.txt matched none
# of the old words, so it was sent and two copies sit on the NUC. "token" was left OUT on purpose:
# ordinary document titles use it and the old list never blocked by such a loose word.
# TRIED FIRST: a bare "secrets?" also blocked 4 book PDFs ("Hacking Exposed ... Secrets & Solutions",
# "Hacking Secret Ciphers with Python"), which would stop new copies reaching Kavita. So secret(s)
# and credential(s) only match as a whole dash/dot/underscore-separated part of the name
# (secrets.txt, my_secrets.env, aws-credentials.json); titles with spaces pass.
# Old line kept in D:\archive\2026-11-22\ai-stack\scripts\sync_documents.py.before-T0008.
NEVER_SEND = re.compile(r"(?i)bitlocker|recovery[ _-]?(key|code)|wpa_supplicant|\.env$|\.env\.\w+$|\.pem$|\.key$|^~\$|"
                        r"backup[ _-]?codes?|master[ _-]?key|communication[ _-]?key|password|passwd|"
                        r"(^|[_.-])(secrets?|credentials?)([_.-]|$)|^id_(rsa|ed25519|ecdsa)(\.pub)?$|"
                        r"\.(kdbx|pfx|p12|ovpn|ppk)$|^config-.*\.xml$")


def log(msg: str) -> None:
    line = "{} {}".format(datetime.datetime.now().isoformat(timespec="seconds"), msg)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def last_sync() -> float:
    try:
        return float(open(STAMP).read().strip())
    except (OSError, ValueError):
        return 0.0


def main() -> int:
    started = datetime.datetime.now().timestamp()
    since = last_sync()
    # Also send anything the NUC does not have yet. A file moved or renamed on the PC keeps its old
    # modified time, so the time stamp alone never sent it (20 such files found 2026-11-12).
    ls = subprocess.run(["ssh", NUC, f"find {REMOTE} -type f -printf '%P\\n'"],
                        capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    remote = set(ls.stdout.splitlines()) if ls.returncode == 0 else None
    changed = []
    for dp, _dns, fns in os.walk(DOCS):
        for fn in fns:
            p = os.path.join(dp, fn)
            if NEVER_SEND.search(fn):
                continue
            rel = os.path.relpath(p, DOCS).replace("\\", "/")
            if os.path.getmtime(p) > since or (remote is not None and rel not in remote):
                changed.append(p)
    log(f"changed since last sync: {len(changed)}")
    if changed:
        ssh = subprocess.Popen(
            ["ssh", NUC, f"mkdir -p {REMOTE} && tar -xf - -C {REMOTE}"],
            stdin=subprocess.PIPE,
        )
        with tarfile.open(fileobj=ssh.stdin, mode="w|") as tar:
            for p in changed:
                tar.add(p, arcname=os.path.relpath(p, DOCS).replace("\\", "/"))
        ssh.stdin.close()
        if ssh.wait() != 0:
            log("FAILED: tar over ssh")
            return 1
    # 2026-11-12: also refresh the code scratchpad's copy of this PC's scripts (best effort; a
    # failure there is logged and never stops the documents sync).
    try:
        sp = subprocess.run([sys.executable, "-I", os.path.join(HERE, "sync_scratchpad.py")],
                            capture_output=True, text=True, timeout=600, check=False)
        log("scratchpad sync exit {}: {}".format(sp.returncode, sp.stdout.strip().splitlines()[-1:] or ""))
    except (OSError, subprocess.TimeoutExpired) as e:
        log(f"scratchpad sync skipped: {type(e).__name__}")
    if "--no-paperless" in sys.argv:  # file sync only (used for the 2026-11-12 first run/test)
        with open(STAMP, "w") as f:
            f.write(str(started))
        return 0
    r = subprocess.run(["ssh", NUC, "bash ~/load_all.sh"], capture_output=True, text=True, check=False)
    summary = [ln for ln in r.stdout.splitlines() if "files," in ln or "FAIL" in ln][-12:]
    log("loader exit {}; {}".format(r.returncode, " | ".join(summary) or "no output"))
    if r.returncode == 0:
        with open(STAMP, "w") as f:
            f.write(str(started))
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
