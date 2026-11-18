#!/usr/bin/env python3
"""Turn broadview.json (broadview_parse.py) into Actual import batches (2026-11-17, money import).

Usage: python broadview_batches.py broadview.json OUTDIR SUFFIX FROM TO [SIZE]
Writes OUTDIR/<suffix>-<n>.json: [{date, amount, imported_id, payee_name, notes}], dates FROM..TO inclusive.
payee_name = the description without the bank's prefix ("Point Of Sale Withdrawal") and without the street
address (cut at the first token that starts with 3+ digits); notes = the full original text.
OUTCOME: (2026-11-17) used for the back-fill of Free Checking (0163) / Primary Savings (8847) in Actual.
"""
import json
import re
import sys
from pathlib import Path

PREFIX = re.compile(r"^(Point Of Sale (Withdrawal|Deposit)|External (Withdrawal|Deposit)|Withdrawal|Deposit)\s+", re.I)


def payee(desc):
    """Short payee from a raw statement description."""
    d = PREFIX.sub("", desc).strip()
    words = []
    for w in d.split():
        if re.match(r"^\d{3,}", w) and words:
            break
        words.append(w)
    return " ".join(words)[:60] or desc[:60]


def main(src, outdir, sfx, start, end, size=250):
    """Write the batches; print counts per batch."""
    data = json.loads(Path(src).read_text(encoding="utf-8"))
    rows = []
    for st in data["statements"]:
        a = st["accounts"].get(sfx)
        if a:
            rows += [{"date": t["date"], "amount": t["amount"], "imported_id": t["imported_id"],
                      "payee_name": payee(t["desc"]), "notes": t["desc"][:120]}
                     for t in a["txns"] if start <= t["date"] <= end]
    Path(outdir).mkdir(parents=True, exist_ok=True)
    for n in range(0, len(rows), size):
        part = rows[n:n + size]
        f = Path(outdir) / ("%s-%d.json" % (sfx, n // size + 1))
        f.write_text(json.dumps(part, separators=(",", ":")), encoding="utf-8")
        print(f.name, len(part), part[0]["date"], part[-1]["date"], "sum %.2f" % sum(r["amount"] for r in part))


if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], a[2], a[3], a[4], int(a[5]) if len(a) > 5 else 250)
