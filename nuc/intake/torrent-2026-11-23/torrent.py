"""torrent.py - send shared magnet links and .torrent files to qBittorrent (2026-11-23).

Jason, 11-23: "I want more of a BitTorrent than a Tor browser". The first route of the Novashare gateway plan:
  - a magnet link (in the shared text or URL) -> qBittorrent /api/v2/torrents/add (urls=)
  - a .torrent file (upload or share)          -> the same call with the file (torrents=)
qBittorrent runs on the Pi inside the Proton VPN container (gluetun, kill switch); it saves into the NAS share
Downloads/Torrents/ (category "novashare"), where the NUC's download-scan virus-checks finished files.
Login: QBIT_USER / QBIT_PASS come from Proton Pass through ~/bin/dc (secrets.refs), never from a file. The NUC's
no-login exception in qBittorrent was removed the same day (API without login -> 403).
Who: Jason (INTAKE_ADMIN) is queued at once; anyone else's torrent is held in PENDING_ROOT/<person>/torrent/
for Jason to approve (same rule as videos without the grant).
Sync on purpose (httpx.Client): route_file runs in a worker thread; share.py calls it through run_in_threadpool.
Result rows: queued | held | error (an error keeps the item in .failed, never dropped).
Env: QBIT_URL (http://192.168.1.69:8080), QBIT_USER, QBIT_PASS, QBIT_CATEGORY (novashare),
QBIT_SAVEPATH (/downloads/Torrents/), PENDING_ROOT.
"""
from __future__ import annotations

import os
import pathlib
import re
import time

import httpx

QBIT = os.environ.get("QBIT_URL", "").rstrip("/")
USER = os.environ.get("QBIT_USER", "")
PASSWORD = os.environ.get("QBIT_PASS", "")
CATEGORY = os.environ.get("QBIT_CATEGORY", "novashare")
SAVEPATH = os.environ.get("QBIT_SAVEPATH", "/downloads/Torrents/")
PENDING = pathlib.Path(os.environ.get("PENDING_ROOT", "/srv/pending"))
MAGNET_RE = re.compile(r"magnet:\?[^\s<>\"']*xt=urn:bt(?:ih|mh):[^\s<>\"']+", re.I)
MAX_TORRENT = 10 * 1048576            # a .torrent file is metadata; anything bigger is not one


def enabled() -> bool:
    return bool(QBIT and USER and PASSWORD)


def find_magnet(text: str | None) -> str | None:
    """The first magnet link in a shared text/URL, or None."""
    m = MAGNET_RE.search(text or "")
    return m.group(0) if m else None


def _client() -> httpx.Client:
    c = httpx.Client(base_url=QBIT, timeout=20, headers={"Referer": QBIT})
    r = c.post("/api/v2/auth/login", data={"username": USER, "password": PASSWORD})
    if r.status_code != 200 or "SID" not in c.cookies:
        c.close()
        raise RuntimeError(f"qBittorrent login failed ({r.status_code})")
    c.post("/api/v2/torrents/createCategory", data={"category": CATEGORY, "savePath": SAVEPATH})  # 409 = exists
    return c


def _add(data: dict, files: dict | None = None) -> tuple[bool, str]:
    c = _client()          # not `with`: httpx refuses to enter a client that already sent the login request
    try:
        r = c.post("/api/v2/torrents/add", data={**data, "category": CATEGORY, "savepath": SAVEPATH}, files=files)
    finally:
        c.close()
    body = r.text.strip()
    return r.status_code == 200 and body.lower().startswith("ok"), f"{r.status_code} {body[:60]}"


def _hold(who: str | None, name: str, payload: bytes) -> pathlib.Path:
    d = PENDING / (who or "unassigned") / "torrent"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
    p.write_bytes(payload)
    return p


def _admin(email: str) -> tuple[bool, str | None]:
    import app
    import portal
    who = app._person(email)
    return who == portal.ADMIN, who


def handle_magnet(magnet: str, email: str, details: list) -> dict:
    """One result row for a shared magnet link."""
    label = "magnet link"
    is_admin, who = _admin(email)
    if not is_admin:
        _hold(who, "link.magnet", (magnet + "\n").encode())
        return {"name": label, "status": "held", "kind": "torrent",
                "why": "Kept for Jason: torrents are added by Jason for now."}
    if not enabled():
        return {"name": label, "status": "error", "why": "The torrent client is not connected yet."}
    try:
        ok, detail = _add({"urls": magnet})
    except (httpx.HTTPError, RuntimeError) as e:
        ok, detail = False, f"{type(e).__name__}: {e}"
    if ok:
        return {"name": label, "status": "queued", "kind": "torrent",
                "why": "Added to qBittorrent (through the VPN). Finished files land in Downloads/Torrents and are virus-checked."}
    details.append({"name": label, "detail": f"qbittorrent: {detail}"})
    return {"name": label, "status": "error", "why": "qBittorrent did not take it. It is kept for Jason."}


def handle_file(path: pathlib.Path, name: str, email: str) -> dict:
    """One result for a .torrent file (called from app.route_file, in a worker thread). Consumes the file."""
    data = path.read_bytes()[:MAX_TORRENT + 1]
    if len(data) > MAX_TORRENT or not data.startswith(b"d"):
        return {"status": "error", "kind": "torrent", "dest": None, "why": "This is not a valid .torrent file."}
    is_admin, who = _admin(email)
    if not is_admin:
        p = _hold(who, name, data)
        path.unlink(missing_ok=True)
        return {"status": "held", "kind": "torrent", "dest": str(p.parent), "held": True,
                "why": "Kept for Jason: torrents are added by Jason for now."}
    if not enabled():
        raise RuntimeError("qBittorrent is not configured")          # route_file's caller keeps the file
    ok, detail = _add({}, files={"torrents": (name, data, "application/x-bittorrent")})
    if not ok:
        raise RuntimeError(f"qBittorrent refused the torrent: {detail}")
    path.unlink(missing_ok=True)
    return {"status": "queued", "kind": "torrent", "dest": "qbittorrent",
            "why": "Added to qBittorrent (through the VPN). Finished files land in Downloads/Torrents and are virus-checked."}
