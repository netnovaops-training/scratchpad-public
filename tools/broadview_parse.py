#!/usr/bin/env python3
"""Parse Broadview FCU eStatement PDFs into transactions, and prove each month adds up (2026-11-17, money import).

Usage:  python broadview_parse.py OUT.json PDF [PDF ...]
Needs pdftotext (poppler). Reads text with -layout, never writes next to the PDFs.

Per statement: the period (gives the year), the SUMMARY OF DEPOSIT ACCOUNTS (beginning/ending per account
suffix, e.g. x0163), and each account section ("FREE CHECKING - ******0163"). A transaction line starts with
MM/DD; indented lines under it continue the description; the first money value (-?1,234.56) at the end is
the amount, a second one is the running balance. Check: sum(amounts) == ending - beginning for every account;
mismatches are listed in "problems" so nothing unproven gets imported.
Output JSON: {"statements": [{file, start, end, accounts: {suffix: {begin, end, sum, ok, txns: [...]}}}],
"problems": [...]}. Each txn: date, amount, desc, imported_id ("bvfcu-<suffix>-<date>-<n>-<cents>": stable
across reruns, so a re-import never duplicates). Account numbers are kept as last-4 suffixes only.
OUTCOME: (2026-11-17) written to back-fill Actual's Free Checking (0163) and Primary Savings (8847) from the
25 statements in All-Documents; see T1009 / the money import Review line for the verified result.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

PDFTOTEXT = r"C:\Users\USER\poppler\poppler-24.08.0\Library\bin\pdftotext.exe"
MONEY = r"-?[\d,]+\.\d{2}"
PERIOD = re.compile(r"Statement Period:\s*(\d{2})/(\d{2})/(\d{4})\s+through\s+(\d{2})/(\d{2})/(\d{4})")
SUMMARY_ROW = re.compile(rf"^x(\d{{4}})\s+({MONEY})\s+({MONEY})\s+({MONEY})\s+({MONEY})")
# 2025-11 on: "FREE CHECKING - ******0163"; before that the full account number is printed: take its last 4 only.
SECTION = re.compile(r"^\s*([A-Z][A-Z ]+?)\s+-\s+[\*\d]*?(\d{4})\b")
TXN = re.compile(rf"^(\d{{2}})/(\d{{2}})\s+(.*?)\s+({MONEY})(?:\s+({MONEY}))?\s*$")
SKIP = re.compile(r"Previous Balance|Ending Balance|Beginning Balance|Member Number|Statement Period|Page:", re.IGNORECASE)


def num(s):
    """'1,234.56' -> 1234.56"""
    return float(s.replace(",", ""))


def parse(pdf):
    """One statement PDF -> dict with period, accounts and their transactions."""
    text = subprocess.run([PDFTOTEXT, "-layout", str(pdf), "-"], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=False).stdout
    m = PERIOD.search(text)
    if not m:
        return None
    start = f"{m.group(3)}-{m.group(1)}-{m.group(2)}"
    end = f"{m.group(6)}-{m.group(4)}-{m.group(5)}"
    year_of = {m.group(1): m.group(3), m.group(4): m.group(6)}
    accts, cur, last = {}, None, None
    for line in text.split("\n"):
        s = SUMMARY_ROW.match(line.strip())
        if s:
            accts.setdefault(s.group(1), {"begin": num(s.group(2)), "end": num(s.group(5)), "txns": [], "name": ""})
            continue
        sec = SECTION.match(line)
        if sec and sec.group(2) in accts:
            cur = sec.group(2)
            accts[cur]["name"] = accts[cur]["name"] or sec.group(1).strip()
            last = None
            continue
        if cur is None or SKIP.search(line):
            if SKIP.search(line):
                last = None
            continue
        t = TXN.match(line.strip()) if re.match(r"^\s*\d{2}/\d{2}\s", line) else None
        if t:
            mm, dd = t.group(1), t.group(2)
            last = {"date": f"{year_of.get(mm, m.group(6))}-{mm}-{dd}", "amount": num(t.group(4)),
                    "desc": re.sub(r"\s+", " ", t.group(3)).strip()}
            accts[cur]["txns"].append(last)
        elif last is not None and line.startswith("   ") and line.strip() and not re.search(MONEY + r"\s*$", line):
            if len(line.strip()) < 60:
                last["desc"] += " " + re.sub(r"\s+", " ", line.strip())
        elif not line.strip():
            continue
        else:
            last = None
    for sfx, a in accts.items():
        a["sum"] = round(sum(x["amount"] for x in a["txns"]), 2)
        a["ok"] = abs(a["sum"] - round(a["end"] - a["begin"], 2)) < 0.005
        seen = {}
        for x in a["txns"]:
            key = (x["date"], round(x["amount"] * 100))
            seen[key] = seen.get(key, 0) + 1
            x["imported_id"] = "bvfcu-{}-{}-{}-{}".format(sfx, x["date"], seen[key], round(x["amount"] * 100))
    return {"file": Path(pdf).name, "start": start, "end": end, "accounts": accts}


def main(argv):
    """Parse every PDF given (dedupe by statement period), write JSON, print a per-month check table."""
    out, pdfs = argv[0], argv[1:]
    by_period, problems = {}, []
    for p in pdfs:
        st = parse(p)
        if not st:
            problems.append("no statement period: " + Path(p).name)
            continue
        by_period.setdefault(st["start"], st)
    for st in sorted(by_period.values(), key=lambda s: s["start"]):
        for sfx, a in st["accounts"].items():
            flag = "ok" if a["ok"] else "MISMATCH"
            print("{} {} x{} {!s:<16} txns {:3d} begin {:9.2f} end {:9.2f} sum {:9.2f} {}".format(
                st["start"], st["end"], sfx, a["name"][:16], len(a["txns"]), a["begin"], a["end"], a["sum"], flag))
            if not a["ok"]:
                problems.append("{} x{} sum {:.2f} != {:.2f}".format(st["start"], sfx, a["sum"], a["end"] - a["begin"]))
    Path(out).write_text(json.dumps({"statements": sorted(by_period.values(), key=lambda s: s["start"]),
                                     "problems": problems}, indent=1), encoding="utf-8")
    print("problems:", problems or "none")


if __name__ == "__main__":
    main(sys.argv[1:])
