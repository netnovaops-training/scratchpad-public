"""scan.py - virus check of ONE staged file by ClamAV (clamd) before intake files it (T0920, 2026-11-21).

Why (Jason 2026-11-19): "a way to sanitize input of files like what I have on my NAS, that's malware and virus
scanner ... prevent future and current and past threats". Every door (drop page, share sheet, resumable finish,
/upload) ends in app.route_file(), so the check lives there, once.

HOW: the clamav container (official clamav/clamav image; freshclam updates signatures by itself) listens on TCP
3310 on a private docker network only (no published port). intake streams the file's bytes to it (clamd INSTREAM)
and reads one answer. Streaming means clamav needs no access to intake's folders.
OUTCOME 2026-11-21: first design sent `zSCAN <path>` over a shared read-only mount; dropped before testing because
clamd runs as uid 100 and intake's staged files are readable by uid 1000 only, so every scan would have failed.

SIZE: clamd refuses streams over StreamMaxLength (100 MB in the image). Files over STREAM_MAX are NOT scanned:
state "skipped" (filed as before, the reason goes to the log). Those are almost always videos, which ClamAV would
only read the first MaxScanSize (400 MB) of anyway. Raise both in a mounted clamd.conf if wanted later.

    state, detail = scan(path)   "clean" | "infected" (detail = signature) | "skipped" (too big) | "error" (why)
    ENABLED                      True when CLAMD_HOST is set; unset = scanning off (old behaviour, old tests)

Variables (env): CLAMD_HOST (container name, e.g. clamav; empty = off), CLAMD_PORT (3310), CLAMD_TIMEOUT
(seconds, default 300), INTAKE_SCAN_MAX_MB (95). QUARANTINE: held files (INTAKE_QUARANTINE, default
/srv/unsorted/.quarantine). CHUNK: bytes per INSTREAM chunk. Nothing here ever deletes a file.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import struct
import time

CLAMD_HOST = os.environ.get("CLAMD_HOST", "").strip()
CLAMD_PORT = int(os.environ.get("CLAMD_PORT", "3310"))
CLAMD_TIMEOUT = float(os.environ.get("CLAMD_TIMEOUT", "300"))
STREAM_MAX = int(os.environ.get("INTAKE_SCAN_MAX_MB", "95")) * 1024 * 1024
ENABLED = bool(CLAMD_HOST)
QUARANTINE = pathlib.Path(os.environ.get("INTAKE_QUARANTINE", "/srv/unsorted/.quarantine"))
CHUNK = 1024 * 1024


def scan(path: pathlib.Path) -> tuple[str, str]:
    """Stream one file to clamd (INSTREAM). Never raises; a problem is ("error", short reason)."""
    try:
        size = pathlib.Path(path).stat().st_size
    except OSError as e:
        return "error", f"cannot read the staged file ({type(e).__name__})"
    if size > STREAM_MAX:
        return "skipped", f"{size // (1024 * 1024)} MB is over the {STREAM_MAX // (1024 * 1024)} MB scan limit"
    try:
        with socket.create_connection((CLAMD_HOST, CLAMD_PORT), timeout=10) as s, open(path, "rb") as fh:
            s.settimeout(CLAMD_TIMEOUT)
            s.sendall(b"zINSTREAM\0")
            while chunk := fh.read(CHUNK):
                s.sendall(struct.pack("!L", len(chunk)) + chunk)
            s.sendall(struct.pack("!L", 0))
            buf = b""
            while not buf.endswith(b"\0"):
                part = s.recv(4096)
                if not part:
                    break
                buf += part
    except OSError as e:
        return "error", f"clamd not answering ({type(e).__name__})"
    answer = buf.rstrip(b"\0").decode("utf-8", "replace")   # "stream: OK" / "stream: Name FOUND" / "... ERROR"
    result = answer.split(": ", 1)[-1]
    if result == "OK":
        return "clean", ""
    if result.endswith(" FOUND"):
        return "infected", result[:-6].strip()[:120]
    return "error", result[:160] or "empty answer"


def hold(tmp: pathlib.Path, name: str, email: str, state: str, detail: str, digest: str) -> pathlib.Path:
    """Move the staged file into QUARANTINE/<time>-<sha8>/ with a WHY.txt. Returns the folder. Never deletes."""
    folder = QUARANTINE / f"{time.strftime('%Y%m%d-%H%M%S')}-{digest[:8]}"
    folder.mkdir(parents=True, exist_ok=True)
    safe = pathlib.PurePosixPath(name).name or "file"
    shutil.move(str(tmp), str(folder / (safe + ".held")))   # .held: never opened by a viewer by its old type
    (folder / "WHY.txt").write_text(json.dumps({"state": state, "detail": detail, "email": email, "name": name,
                                                "sha256": digest, "time": time.strftime("%F %T")}, indent=1))
    return folder


def held_count() -> int:
    """How many uploads wait in quarantine (information for /healthz, not a failure)."""
    try:
        return sum(1 for d in QUARANTINE.iterdir() if d.is_dir())
    except OSError:
        return 0
