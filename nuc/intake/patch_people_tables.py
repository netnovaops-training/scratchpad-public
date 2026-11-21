#!/usr/bin/env python3
"""patch_people_tables.py - T0927 slice 1b: in a COPY of intake, replace the people tables in the code with
imports from dropconfig (so the household emails leave the code). Run inside the copy, never on the live folder.

    python3 patch_people_tables.py ~/sandbox/t0927/intake

portal.py: WHO, PRIVATE_OK, NAMES -> `from dropconfig import WHO, PRIVATE_OK, NAMES`
classify.py: PEOPLE, SHELF        -> `from dropconfig import PEOPLE, SHELF`
The names stay the same, so app.py (portal.WHO, portal.PRIVATE_OK) and classify's own uses need no change.
Line ranges come from ast (start of the assignment to its end), so a dict spread over many lines is removed
whole. Refuses to run on ~/nuc-homelab/intake, and refuses if a table is missing or already patched.
Afterwards no email address may remain in portal.py or classify.py (checked; count printed, never the address).
"""
import ast
import re
import sys
from pathlib import Path

PLAN = {"portal.py": ("WHO", "PRIVATE_OK", "NAMES"), "classify.py": ("PEOPLE", "SHELF")}
NOTE = ("# T0927 (2026-11-21): the people tables live in drop.toml now (dropconfig.py; no emails in the code).\n"
        "# Same names as before, so every use of {names} keeps working. Edit people in drop.toml, not here.\n")


def patch(path: Path, names: tuple) -> None:
    src = path.read_text(encoding="utf-8")
    if "from dropconfig import" in src:
        raise SystemExit(f"{path.name}: already patched")
    spans = []
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and getattr(node.targets[0], "id", "") in names:
            spans.append((node.lineno, node.end_lineno, node.targets[0].id))
    if {s[2] for s in spans} != set(names):
        raise SystemExit(f"{path.name}: expected {names}, found {[s[2] for s in spans]}")
    lines = src.split("\n")
    first = min(s[0] for s in spans)
    for start, end, _ in sorted(spans, reverse=True):
        del lines[start - 1:end]
    imp = NOTE.format(names=", ".join(names)) + f"from dropconfig import {', '.join(names)}  # noqa: E402"
    lines.insert(first - 1, imp)
    out = "\n".join(lines)
    compile(out, path.name, "exec")
    left = len(re.findall(r"[a-z0-9._%+-]+@[a-z0-9-]+\.[a-z.]{2,}", out, re.I))
    path.write_text(out, encoding="utf-8")
    print(f"{path.name}: {len(spans)} tables replaced by one import; email-like strings left: {left}")


def main(folder: Path) -> int:
    if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
        raise SystemExit("refusing to patch the LIVE intake folder; use a copy")
    for fname, names in PLAN.items():
        patch(folder / fname, names)
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).expanduser()))
