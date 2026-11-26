"""T0917 step 3: one keep list for every :before-* image tag (NUC, PC, Pi). Deletes nothing.

Input: lines "machine|repo:tag|id|created|size" (docker images). The keep date is the date in the
tag (before-YYYYMMDD, before-<name>-YYYYMMDD, before-YYYY-MM-DD) or else the image's created date,
plus 30 days (rule: keep the old version 30+ days). Output: markdown table to stdout.
"""
import re, sys
from datetime import date, timedelta

rows = []
for line in open(sys.argv[1], encoding="utf-8"):
    m, ref, iid, created, size = line.rstrip("\n").split("|")
    tag = ref.split(":", 1)[1]
    d = re.search(r"(20\d\d)-?(\d\d)-?(\d\d)", tag)
    src = "tag"
    if not d:
        d = re.search(r"(20\d\d)-(\d\d)-(\d\d)", created); src = "created"
    day = date(int(d[1]), int(d[2]), int(d[3]))
    rows.append((m, ref, iid, size, day, day + timedelta(days=30), src))
rows.sort(key=lambda r: (r[5], r[0], r[1]))
print("# T0917 keep list: :before-* image tags (made 2026-11-24 by autopilot, deletes nothing)\n")
print("Rule: keep each old image at least 30 days after its swap. After 'keep until', it MAY be removed by hand")
print("(`docker image rm <repo:tag>`), only if no running container uses that image ID and the service has had")
print("no rollback since. Also keep: NUC ~/archive/2026-11-19 snapshot (2.6 GB) until 2026-12-19.\n")
print("| machine | image:tag | id | size | date (from) | keep until |")
print("|---|---|---|---|---|---|")
for m, ref, iid, size, day, until, src in rows:
    print(f"| {m} | {ref} | {iid[:12]} | {size} | {day} ({src}) | {until} |")
today = date(2026, 11, 24)
print(f"\nTotals: {len(rows)} tags; past keep-until on {today}: {sum(1 for r in rows if r[5] < today)}.")
