#!/usr/bin/env python3
"""patch_t0927s3.py - T0927 slice 3 in a COPY of intake: one virus gate and one placement step for every door.

    python3 patch_t0927s3.py ~/sandbox/t0927s3/intake

Audit phase 4 item 6 ("one file_path() pipeline: scan -> classify -> grant -> place; all doors use it"), first
step, behaviour-preserving: the two bodies that file a file (route_file = drop page, share sheet, resumable
finish, /upload; _intake_sync = /intake for Telegram + mail) each had their own copy of the virus check and of
"place it, or put it in unsorted with a sidecar". That duplication is how /intake went without the virus check
until T0962. Now both call:
  _virus_gate(tmp, name, digest, held_as, door)   infected -> held + 422, scanner down -> held + 503 (+ a HELD log
                                                   line for every door; before only /intake logged it)
  _place_or_unsorted(tmp, name, digest, verdict, sidecar)   library dest, or unsorted + .intake.json sidecar
Sorting (classify / the video rule) and the write-grant check stay per door, exactly as before.
NOT changed (a question for Jason, not an overnight change): /intake runs no write-grant check (route_file does).
Refuses on the live folder, an already patched file, or old text not found exactly once.
Variables: folder (the copy), s (app.py text), swap() (exact-once replace), GATE / PLACE (the new helpers).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser() if len(sys.argv) == 2 else None
if folder is None or not (folder / "app.py").is_file():
    raise SystemExit("usage: patch_t0927s3.py <copy of the intake folder>")
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
s = (folder / "app.py").read_text(encoding="utf-8")
if "_virus_gate" in s:
    raise SystemExit("already patched")


def swap(old, new):
    global s
    if s.count(old) != 1:
        raise SystemExit(f"expected exactly one copy of {old[:60]!r}, found {s.count(old)}")
    s = s.replace(old, new)


HELPERS = '''def _virus_gate(tmp: pathlib.Path, name: str, digest: str, held_as: str, door: str) -> None:
    """T0927 slice 3: the ONE virus check every door runs before anything is classified or filed (T0920, T0962).
    Infected -> held in .quarantine + 422; scanner down -> held + 503 (kept safe, never filed unchecked, never
    lost); over the stream limit -> "skipped", filed as before. CLAMD_HOST unset = scanning off. `held_as` is what
    the WHY.txt records (a door and a person id, never an email: T0962 point 5)."""
    if not scan.ENABLED:
        return
    state, detail = scan.scan(tmp)
    if state in ("infected", "error"):
        scan.hold(tmp, name, held_as, state, detail, digest)
        say(f"HELD {door}: {name} -- virus check {state}")
        if state == "infected":
            raise HTTPException(422, "This file looks unsafe (virus check), so it was held back and not filed.")
        raise HTTPException(503, "The virus check is not answering, so the file was kept safe and not filed yet.")


def _place_or_unsorted(tmp: pathlib.Path, name: str, digest: str, verdict: dict, sidecar: dict) -> dict:
    """T0927 slice 3: the ONE placement step. Into the verdict's library, or into unsorted WITH a .intake.json
    sidecar (the reason it could not be placed is the only thing that makes the pile actionable later)."""
    if verdict["dest"]:
        res = place(tmp, pathlib.Path(verdict["dest"]), name, digest, verdict)
    else:
        res = place(tmp, UNSORTED, name, digest, verdict)
        side = pathlib.Path(res["path"]).with_suffix(".intake.json")
        side.write_text(json.dumps(
            {**verdict, **sidecar, "sha256": digest, "at": time.strftime("%Y-%m-%d %H:%M:%S")},
            indent=2), encoding="utf-8")
    say(f"  {res['status']}: {res['path']}")
    return res


def _intake_sync('''

# --- _intake_sync: the shared gate and placement
swap('''def _intake_sync(''', HELPERS)
swap('''    # T0962 (2026-11-21): this door had no virus check (T0920 put it in route_file, which /intake does not use).
    # Same rule as route_file: infected -> held + 422, scanner down -> held + 503; nothing filed unchecked.
    if scan.ENABLED:
        state, detail = scan.scan(tmp)
        if state in ("infected", "error"):
            scan.hold(tmp, name, f"{source}:{who}", state, detail, digest)
            say(f"HELD {source} from {who}: {name} -- virus check {state}")
            if state == "infected":
                raise HTTPException(422, "This file looks unsafe (virus check), so it was held back and not filed.")
            raise HTTPException(503, "The virus check is not answering, so the file was kept safe and not filed yet.")
''', '''    # T0962 (2026-11-21): this door had no virus check (T0920 put it in route_file, which /intake does not use).
    # T0927 slice 3: both doors now call the same _virus_gate, so they cannot drift apart again.
    _virus_gate(tmp, name, digest, f"{source}:{who}", f"{source} from {who}")
''')
swap('''    if verdict["dest"]:
        res = place(tmp, pathlib.Path(verdict["dest"]), name, digest, verdict)
    else:
        # Unsorted, WITH a sidecar. The reason it could not be placed is
        # the only thing that makes the pile actionable later.
        res = place(tmp, UNSORTED, name, digest, verdict)
        side = pathlib.Path(res["path"]).with_suffix(".intake.json")
        side.write_text(json.dumps(
            {**verdict, "sender": sender, "source": source,
             "caption": caption, "sha256": digest,
             "at": time.strftime("%Y-%m-%d %H:%M:%S")},
            indent=2), encoding="utf-8")

    say(f"  {res['status']}: {res['path']}")
    return {**res, **verdict, "size": size, "sha256": digest}
''', '''    res = _place_or_unsorted(tmp, name, digest, verdict, {"sender": sender, "source": source, "caption": caption})
    return {**res, **verdict, "size": size, "sha256": digest}
''')
# --- route_file: the same gate and placement
swap('''    if scan.ENABLED:
        state, detail = scan.scan(tmp)
        if state in ("infected", "error"):
            # T0962 point 5 (Jason 11-21): the note keeps the person id, not the email address.
            scan.hold(tmp, name, f"drop:{who or 'unknown'}", state, detail, digest)
            if state == "infected":
                raise HTTPException(422, "This file looks unsafe (virus check), so it was held back and not filed.")
            raise HTTPException(503, "The virus check is not answering, so the file was kept safe and not filed yet.")
''', '''    # T0962 point 5 (Jason 11-21): the note keeps the person id, not the email address.
    # T0927 slice 3: the shared gate (same as /intake).
    _virus_gate(tmp, name, digest, f"drop:{who or 'unknown'}", f"drop from {who or 'unknown'}")
''')
swap('''    if verdict["dest"]:
        res = place(tmp, pathlib.Path(verdict["dest"]), name, digest, verdict)
    else:
        res = place(tmp, UNSORTED, name, digest, verdict)
        side = pathlib.Path(res["path"]).with_suffix(".intake.json")
        side.write_text(json.dumps(
            {**verdict, "uploader": email, "source": "drop",
             "sha256": digest,
             "at": time.strftime("%Y-%m-%d %H:%M:%S")},
            indent=2), encoding="utf-8")

    say(f"  {res['status']}: {res['path']}")
    return {**res, **verdict, "size": size}
''', '''    res = _place_or_unsorted(tmp, name, digest, verdict, {"uploader": email, "source": "drop"})
    return {**res, **verdict, "size": size}
''')
compile(s, "app.py", "exec")
(folder / "app.py").write_text(s, encoding="utf-8")
print("patched: app.py (_virus_gate + _place_or_unsorted shared by route_file and _intake_sync)")
