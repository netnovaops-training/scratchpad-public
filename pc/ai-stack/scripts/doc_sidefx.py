#!/usr/bin/env python3
"""doc_sidefx.py: redo ONLY the "Side effects" section of docs that say "none" (2026-11-02).

    python scripts/doc_sidefx.py --list            # which docs would be redone, run nothing
    python scripts/doc_sidefx.py kb/notify.py ...  # these source files only
    python scripts/doc_sidefx.py --all             # every doc whose Side effects says none

WHY. 2026-11-01 accuracy check (D:\\ai-doc-trial\\ACCURACY-2026-11-01.md): 202 docs said "Side effects:
none"; of 5 read against the code, 4 were wrong (missed file writes, a webhook POST, a scheduled task).
The first-pass prompt asked for six sections at once; this one asks for the single section, with the
rule spelled out. Jason approved the rerun 2026-11-02.

What it does to a doc: replaces the text under "## Side effects" and adds one front-matter line
`side-effects-pass: <date>`. Nothing else changes (hash, other sections, hand corrections stay).
Skips docs with a `corrected:` line (a person already fixed them), empty source files, and sources
longer than doc_backfill.MAX_CHARS.

CHECK AFTER THE MODEL ANSWERS. If it says "none" but the source contains a risky command (same
keyword list as the accuracy check), the doc is NOT trusted: the section keeps the old text and the
front matter gets `side-effects: unverified`. Log: D:\\ai-doc-trial\\sidefx.log, PASS/FAIL lines only.
Never edits source files. Does not commit or push.
"""
import datetime
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import doc_backfill as db  # noqa: E402  (ask(), clean(), MAX_CHARS, SECRETY, DOCS folder)

LOG = r"D:\ai-doc-trial\sidefx.log"
RISK = re.compile(
    r"(rm\s+-|Remove-Item|\bdel\s|rmdir|os\.remove|unlink|shutil\.rmtree|DELETE\s+FROM|DROP\s+TABLE|"
    r"docker\s+(pull|run|compose\s+up|build|restart|stop|kill)|curl\s|wget\s|Invoke-WebRequest|Invoke-RestMethod|"
    r"requests\.(post|put|delete)|httpx\.(post|put|delete)|pip\s+install|apt(-get)?\s+install|npm\s+install|"
    r"systemctl\s+(restart|stop)|Restart-Service|Stop-Service|Stop-Process|taskkill|Register-ScheduledTask|schtasks|"
    r"Set-Content|Out-File|Add-Content|\.write\(|write_text|open\([^)]*[\"'][wa]|shutil\.(copy|move)|Copy-Item|Move-Item|"
    r"smtplib|sendmail|send_message|Send-MailMessage|webhook)", re.I)

PROMPT = """List the SIDE EFFECTS of the source file below. A side effect is anything the file does outside
itself when it runs: writes, creates, overwrites or deletes a file or folder; sends a network request
or message; starts, stops or restarts a process, container or service; installs or pulls something;
creates or removes a scheduled task or registry entry; changes a setting.

Rules:
- Check EVERY line. Include optional paths (behind a switch or flag) and say which switch triggers them.
- One short bullet per side effect, naming the real file, URL variable, command or task from the file.
- Use only facts visible in the file. Never invent a name.
- Write "- None: reads only, checked every line." ONLY if the file truly does none of the above.
- Output ONLY the bullet lines. No heading, no preface, no code fence.

FILE: {name}
-----
{body}
-----"""


def log(line):
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), line))
    print(line)


def says_none(section):
    return bool(re.match(r"\s*[-*]?\s*(none|no side effects)", section.strip().lower()))


def targets():
    """(source path, doc path) for every doc whose Side effects says none and nobody has corrected."""
    out = []
    for root, _, files in os.walk(db.OUT):
        for f in files:
            if not f.endswith(".md"):
                continue
            dp = os.path.join(root, f)
            d = open(dp, encoding="utf-8", errors="replace").read()
            m = re.search(r"^file:\s*(.+)$", d, re.M)
            s = re.search(r"## Side effects\n(.*?)(?=\n## )", d, re.S)
            if not (m and s) or not says_none(s.group(1)) or re.search(r"^corrected:", d, re.M):
                continue
            if "side-effects-pass:" in d or "side-effects: unverified" in d:
                continue   # already redone: a rerun picks up where it stopped
            out.append((m.group(1).strip(), dp))
    return sorted(out)


def process(rel, dp):
    src = os.path.join(db.REPO, rel.replace("/", os.sep))
    body = open(src, encoding="utf-8", errors="replace").read()
    if not body.strip():
        return "skip"
    if len(body) > db.MAX_CHARS:
        log("SKIP too-long %s" % rel)
        return "skip"
    ans = db.clean(db.ask(PROMPT.format(name=rel, body=body)))
    ans = "\n".join(l for l in ans.splitlines() if l.strip())
    if not ans or db.SECRETY.search(ans) or not ans.lstrip().startswith(("-", "*")):
        log("FAIL no-usable-answer %s" % rel)
        return "fail"
    doc = open(dp, encoding="utf-8").read()
    stamp = datetime.date.today().isoformat()
    if says_none(ans) and RISK.search(body):
        # The model repeated "none" but the file has a risky command: do not trust either text.
        doc = doc.replace("\n---\n\n", "\nside-effects: unverified (model said none; source has %s)\n---\n\n"
                          % RISK.search(body).group(1).strip()[:30].replace("\n", " "), 1)
        open(dp, "w", encoding="utf-8").write(doc)
        log("UNVERIFIED %s" % rel)
        return "unverified"
    doc = re.sub(r"(## Side effects\n).*?(?=\n## )", lambda m: m.group(1) + ans + "\n", doc, count=1, flags=re.S)
    doc = doc.replace("\n---\n\n", "\nside-effects-pass: %s\n---\n\n" % stamp, 1)
    open(dp, "w", encoding="utf-8").write(doc)
    log("PASS %s (%d bullets)" % (rel, ans.count("\n") + 1))
    return "done"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    todo = targets()
    if "--all" not in sys.argv:
        wanted = {a.replace("\\", "/") for a in args}
        todo = [t for t in todo if t[0] in wanted]
    if "--list" in sys.argv:
        for rel, _ in todo:
            print(rel)
        print("%d docs" % len(todo))
        return 0
    counts = {}
    for rel, dp in todo:
        r = process(rel, dp)
        counts[r] = counts.get(r, 0) + 1
    log("DONE %s of %d docs" % (", ".join("%s=%d" % kv for kv in sorted(counts.items())), len(todo)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
