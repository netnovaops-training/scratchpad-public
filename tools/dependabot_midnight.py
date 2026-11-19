#!/usr/bin/env python3
"""dependabot_midnight.py - move every Dependabot schedule to 00:00 New York time (2026-11-19).

    python3 dependabot_midnight.py PATH/.github/dependabot.yml [--write]

Why: Jason 2026-11-19: run the grouped Dependabot updates at midnight, so the 6 AM GitHub pull
(github_pull.py) already has them to work on. Only the time and timezone change; the weekly day and
the groups stay as they are (fewer, larger PRs). Prints the changed lines; --write saves.
"""
import re
import sys
from pathlib import Path


def fix(text: str) -> str:
    """Return text with every schedule at 00:00 America/New_York."""
    text = re.sub(r'time:\s*"\d\d:\d\d"', 'time: "00:00"', text)
    text = re.sub(r"timezone:\s*[A-Za-z_/]+", "timezone: America/New_York", text)
    # block-style schedules without a timezone line: add one under the time line, same indent
    out = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        out.append(line)
        m = re.match(r"^(\s*)time:\s*\"00:00\"\s*$", line)
        if m and not any("timezone:" in l for l in lines[max(0, i - 4):i + 4]):
            out.append(f"{m.group(1)}timezone: America/New_York")
    return "\n".join(out)


if __name__ == "__main__":
    p = Path(sys.argv[1])
    old = p.read_text(encoding="utf-8")
    new = fix(old)
    changed = sum(1 for a, b in zip(old.split("\n"), new.split("\n")) if a != b) + abs(
        len(new.split("\n")) - len(old.split("\n")))
    print(f"{p}: {changed} line(s) changed")
    if "--write" in sys.argv and new != old:
        p.write_text(new, encoding="utf-8")
