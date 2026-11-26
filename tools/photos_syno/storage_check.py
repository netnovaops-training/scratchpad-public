#!/usr/bin/env python3
"""storage_check.py - between-batch NAS storage guard (coordinator 11-24: stop at once on any storage warning).

check() returns (ok, detail): ok is False when /proc/mdstat differs from the baseline taken at 04:2x on 11-24
(md4 SSD cache already [2/1] [U_], md0-3 all [UUUU]) or when /var/log/messages has a new storage warning line
(btrfs error/warning, I/O error, md/raid degrade or failure, cache state change, disk/SMART fail) since `since`.
Known before we started (not a new warning): the 04:03:54 "Cache (alloc_cache_1) RAID state changes from Normal
to Degrade" and the nvme1n1 lookup lines at 04:02.
OUTCOME 2026-11-24: written for the step 3-5 batching rule.
"""
import re

from nas import script

BASE_MD = {"md2": "[4/4] [UUUU]", "md3": "[4/4] [UUUU]", "md4": "[2/1] [U_]", "md1": "[4/4] [UUUU]",
           "md0": "[4/4] [UUUU]"}
WARN = re.compile(r"btrfs.*(error|warn|corrupt)|I/O error|degrad|raid.*(fail|crash)|disk.*fail|smart.*fail|"
                  r"cache.*(state|fail|error)|read-only|remount|ext4.*error|md.*faulty", re.I)
# recurring since 10-31 every few days, not a storage fault (checked 11-24):
KNOWN = re.compile(r"Failed to update temperature")


def check(since):
    """since: 'YYYY-MM-DDTHH:MM:SS' local NAS time. Returns (ok, text)."""
    rc, out, err = script("cat /proc/mdstat; echo ===LOG; cat /var/log/messages 2>/dev/null | tail -5000", as_root=True,
                          timeout=120)
    if rc != 0:
        return False, f"check failed rc {rc}: {err[-200:]}"
    md, log = out.split("===LOG", 1)
    problems = []
    cur = None
    for line in md.splitlines():
        m = re.match(r"^(md\d+) :", line)
        if m:
            cur = m.group(1)
            continue
        m = re.search(r"(\[\d+/\d+\] \[[U_]+\])", line)
        if cur and m:
            if BASE_MD.get(cur) != m.group(1):
                problems.append(f"mdstat {cur} {m.group(1)} (baseline {BASE_MD.get(cur)})")
            cur = None
    if re.search(r"resync|recovery|reshape", md):
        problems.append("mdstat shows resync/recovery")
    for line in log.splitlines():
        ts = line[:19]
        if ts > since and WARN.search(line) and not KNOWN.search(line):
            problems.append("log: " + line[:220])
    return not problems, "\n".join(problems) if problems else "storage ok (mdstat = baseline, no new warnings)"


if __name__ == "__main__":
    import sys
    print(check(sys.argv[1] if len(sys.argv) > 1 else "2026-11-24T04:05:00"))
