#!/usr/bin/env python3
"""dependabot_one_pr.py - rewrite a dependabot.yml so ALL version updates arrive as ONE monthly PR (2026-11-19).

    python3 dependabot_one_pr.py PATH/.github/dependabot.yml          print the new file
    python3 dependabot_one_pr.py PATH/.github/dependabot.yml --write  save it

Why: Jason 2026-11-19: "I don't want to get banned again on GitHub ... limit the PR notices into much
bigger and fewer ... grouped together". Per-ecosystem groups still meant one PR per ecosystem and folder
every week (garden-for-all opened 4 at once). GitHub's multi-ecosystem-groups put every ecosystem in one
group with one schedule: one PR per repo per month, 00:00 New York on the 1st, before the 6 AM pull.
Each update keeps its ecosystem, folder(s), ignore list and labels; per-ecosystem groups and schedules
are dropped (patterns ["*"] is required with multi-ecosystem-group). Security alerts still open their
own PRs (GitHub does not let a config group those across ecosystems).
"""
import sys
from pathlib import Path

import yaml

KEEP = ("package-ecosystem", "directory", "directories", "ignore", "allow", "labels", "registries",
        "versioning-strategy", "exclude-paths")


def convert(cfg: dict) -> str:
    """Return the new dependabot.yml text."""
    ups = []
    for u in cfg.get("updates", []):
        n = {k: u[k] for k in KEEP if k in u}
        n["patterns"] = ["*"]
        n["multi-ecosystem-group"] = "all-dependencies"
        ups.append(n)
    out = {
        "version": 2,
        "multi-ecosystem-groups": {"all-dependencies": {"schedule": {
            "interval": "monthly", "time": "00:00", "timezone": "America/New_York"}}},
        "updates": ups,
    }
    if "registries" in cfg:
        out["registries"] = cfg["registries"]
    head = ("# One Dependabot PR per repo per month: every ecosystem in one multi-ecosystem group,\n"
            "# 00:00 New York on the 1st (before the 6 AM pull). Owner's rule 2026-11-19: few, large PRs.\n"
            "# Made by Scratchpad tools/dependabot_one_pr.py; edit the ecosystem list here, keep the group.\n")
    body = yaml.safe_dump(out, sort_keys=False, default_flow_style=False)
    # safe_dump writes time: 00:00 unquoted, which YAML 1.1 readers take as the number 0. Quote it.
    body = body.replace("time: 00:00", 'time: "00:00"')
    return head + body


if __name__ == "__main__":
    p = Path(sys.argv[1])
    new = convert(yaml.safe_load(p.read_text(encoding="utf-8")))
    yaml.safe_load(new)  # must still parse
    if "--write" in sys.argv:
        p.write_text(new, encoding="utf-8")
        print(f"{p}: {new.count('package-ecosystem')} ecosystem entries, one group")
    else:
        print(new)
