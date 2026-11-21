#!/usr/bin/env python3
"""patch_t0962.py - T0962 points 1 and 3, in a COPY of intake only.

    python3 patch_t0962.py ~/sandbox/t0962/intake

1) /intake (Telegram + mail, via _intake_sync) filed files WITHOUT the ClamAV check: T0920 put the check in
   route_file, which the drop page, share sheet and resumable finish use, but /intake has its own body. Now
   _intake_sync runs the same check first: infected -> held + 422, scanner down -> held + 503, as route_file.
   The quarantine note records the person id and the door ("telegram:jason"), not the raw sender id.
3) scan.hold() named the folder <time>-<sha8>, so the same file held twice in one second shared a folder and the
   second WHY.txt overwrote the first. Now the folder is created with exist_ok=False and gets -2, -3 ... if taken.
2) /healthz ignored ClamAV, so the container showed healthy while every upload was being held. scan.ping()
   (zPING -> PONG, 3 s) now feeds /healthz: scanner down -> 503 "virus check not answering"; the body also says
   clamav ok/down/off and how many uploads are held.
Refuses on the live folder or if any old text is not found exactly once.
Variables: folder (the copy), ap / sc (file texts), swap() (exact-once replace).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser() if len(sys.argv) == 2 else None
if folder is None or not (folder / "app.py").is_file():
    raise SystemExit("usage: patch_t0962.py <copy of the intake folder>")
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")


def swap(text: str, old: str, new: str, name: str) -> str:
    if text.count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:60]!r}, found {text.count(old)}")
    return text.replace(old, new)


ap = (folder / "app.py").read_text(encoding="utf-8")
if "T0962" in ap:
    raise SystemExit("already patched")
ap = swap(ap, '''    """The blocking half of /intake: hash, classify, place (runs in a thread, T0925 A3)."""
    digest = sha256(tmp)
''', '''    """The blocking half of /intake: hash, virus check, classify, place (runs in a thread, T0925 A3)."""
    digest = sha256(tmp)
    # T0962 (2026-11-21): this door had no virus check (T0920 put it in route_file, which /intake does not use).
    # Same rule as route_file: infected -> held + 422, scanner down -> held + 503; nothing filed unchecked.
    if scan.ENABLED:
        state, detail = scan.scan(tmp)
        if state in ("infected", "error"):
            scan.hold(tmp, name, f"{source}:{who}", state, detail, digest)
            say(f"HELD {source} from {who}: {name} -- virus check {state}")
            if state == "infected":
                raise HTTPException(422, "This file looks unsafe (virus check), so it was held back and not filed.")
            raise HTTPException(503, "The virus check is not answering, so the file was kept safe and not filed yet.")
''', "app.py")
compile(ap, "app.py", "exec")

sc = (folder / "scan.py").read_text(encoding="utf-8")
sc = swap(sc, '''    folder = QUARANTINE / f"{time.strftime('%Y%m%d-%H%M%S')}-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
''', '''    # T0962 (2026-11-21): a NEW folder every time. exist_ok=True let the same file held twice in one second
    # share a folder, and the second WHY.txt overwrote the first.
    base = f"{time.strftime('%Y%m%d-%H%M%S')}-{digest[:8]}"
    QUARANTINE.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        folder = QUARANTINE / (base if n == 1 else f"{base}-{n}")
        try:
            folder.mkdir()
            break
        except FileExistsError:
            n += 1
''', "scan.py")
sc = swap(sc, '''def held_count() -> int:''', '''def ping(timeout: float = 3.0) -> bool:
    """T0962 point 2: is clamd answering? zPING -> PONG, short timeout so /healthz stays fast. Off = True."""
    if not ENABLED:
        return True
    try:
        with socket.create_connection((CLAMD_HOST, CLAMD_PORT), timeout=timeout) as s:
            s.settimeout(timeout)
            s.sendall(b"zPING\\0")
            return s.recv(16).rstrip(b"\\0\\n") == b"PONG"
    except OSError:
        return False


def held_count() -> int:''', "scan.py")
compile(sc, "scan.py", "exec")

# point 2: /healthz knows about the virus check
ap = swap(ap, '''    body = {"ok": not problems, "free_mb": free_mb, "problems": problems, "failed": failed,
            "allow_telegram": len(ALLOW), "allow_mail": len(MAIL_ALLOW)}
''', '''    # T0962 point 2 (2026-11-21): with the scanner down every upload is HELD, not filed, so "uploads could
    # land" is false: 503 with a plain reason. "held" counts quarantine folders (information only).
    clamav = "off"
    if scan.ENABLED:
        clamav = "ok" if scan.ping() else "down"
        if clamav == "down":
            problems.append("virus check not answering (uploads are held, not filed)")
    body = {"ok": not problems, "free_mb": free_mb, "problems": problems, "failed": failed,
            "allow_telegram": len(ALLOW), "allow_mail": len(MAIL_ALLOW), "clamav": clamav,
            "held": scan.held_count()}
''', "app.py")
compile(ap, "app.py", "exec")

(folder / "app.py").write_text(ap, encoding="utf-8")
(folder / "scan.py").write_text(sc, encoding="utf-8")
print("patched: app.py (/intake virus check), scan.py (unique quarantine folders)")
