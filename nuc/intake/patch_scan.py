#!/usr/bin/env python3
"""patch_scan.py - T0920: in a COPY of intake, call scan.py at the top of app.route_file(). Never on the live folder.

    python3 patch_scan.py ~/sandbox/t0920/intake
"""
import sys
from pathlib import Path

ANCHOR = "    who = _person(email)\n    digest = sha256(tmp)\n"
HOOK = '''    who = _person(email)
    digest = sha256(tmp)

    # T0920 (2026-11-21): virus check BEFORE anything is classified or filed (scan.py; clamd over the private
    # docker network). Infected -> held in .quarantine with a WHY.txt; scanner down -> also held (kept safe,
    # never filed unchecked, never lost); over the stream limit -> "skipped", filed as before (scan.py SIZE).
    # CLAMD_HOST unset = scanning off (old behaviour).
    if scan.ENABLED:
        state, detail = scan.scan(tmp)
        if state in ("infected", "error"):
            scan.hold(tmp, name, email, state, detail, digest)
            if state == "infected":
                raise HTTPException(422, "This file looks unsafe (virus check), so it was held back and not filed.")
            raise HTTPException(503, "The virus check is not answering, so the file was kept safe and not filed yet.")
'''


def main(folder: Path) -> int:
    if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
        raise SystemExit("refusing to patch the LIVE intake folder")
    app = folder / "app.py"
    t = app.read_text(encoding="utf-8")
    if "scan.ENABLED" in t:
        raise SystemExit("already patched")
    if t.count(ANCHOR) != 1:
        raise SystemExit(f"anchor found {t.count(ANCHOR)} times, expected 1")
    t = t.replace(ANCHOR, HOOK, 1)
    t = t.replace("\nimport portal\n", "\nimport portal\nimport scan  # T0920 virus check\n", 1) \
        if "\nimport portal\n" in t else t
    if "import scan" not in t:
        raise SystemExit("could not add `import scan` (no plain `import portal` line)")
    compile(t, "app.py", "exec")
    app.write_text(t, encoding="utf-8")
    print("app.py: scan hook added in route_file, import added")
    return 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]).expanduser()))
