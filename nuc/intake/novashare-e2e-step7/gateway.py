"""gateway.py - Novashare as the gateway: one table of where every kind of share goes, with live health (2026-11-23).

Jason, 11-23: "Can the Novashare be a gateway to each container just like litellm opens the APIs ... which
computer opens each container ... a secure pathway". Step B1 + B4 of the gateway plan
(~/.claude/plans/functional-greeting-horizon.md): the ROUTES table names, in order, each kind of shared item,
the service it is sent to, the computer and container that run it, the grant needed, and how it is reached
(docker network name on the NUC, or the Pi over the home network / Tailscale). /routes shows the table to
Jason with a live health dot per target, like LiteLLM's model list. /routes.json is the same as data.
This step DESCRIBES and CHECKS the routes; the code that sends (share.py, app.route_file, torrent.py, onion.py,
link_route.py) is unchanged. A later step makes share/route_file dispatch through this table.
Health checks: short timeouts (3 s), run together, never raise; a dot is green (answers), red (no answer) or grey
(switched off by configuration).
Variables: ROUTES (list of dicts), CHECKS (check name -> coroutine), ADMIN-only access (portal.ADMIN).
"""
from __future__ import annotations

import asyncio
import html
import os
import pathlib
import socket

import httpx
import site_assets
from fastapi import APIRouter, Header
from fastapi.responses import HTMLResponse, JSONResponse

router = APIRouter()
NUC, PI = "NUC", "Pi (Proton VPN)"


def _env(k: str, d: str = "") -> str:
    return os.environ.get(k, d).strip()


def routes() -> list[dict]:
    """The ordered table. Links are tried top to bottom; files after them. Library rows come from drop.toml."""
    import dropconfig
    rows = [
        {"name": "Magnet link", "match": "magnet:?xt=urn:btih / btmh", "send": "qBittorrent", "computer": PI,
         "container": "qbittorrent (in gluetun)", "via": _env("QBIT_URL") or "off", "grant": "Jason (others held)",
         "check": "qbittorrent"},
        {"name": "Tor link", "match": "http(s)://*.onion", "send": "Tor fetch -> PDF / file", "computer": NUC,
         "container": "tor-proxy (in gluetun-tor)", "via": _env("TOR_PROXY") or "off", "grant": "Jason only",
         "check": "tor"},
        {"name": "YouTube link", "match": "youtube.com, youtu.be, music.youtube.com", "send": "MeTube (mp3/mp4)",
         "computer": PI, "container": "metube (in gluetun)", "via": _env("METUBE_URL") or "off (printed as PDF)",
         "grant": "anyone with the drop", "check": "metube"},
        {"name": "File link", "match": "link to a PDF, Word file or photo", "send": "download, then file it",
         "computer": NUC, "container": "intake", "via": "public internet (IP-checked)", "grant": "as the file kind",
         "check": None},
        {"name": "Web page", "match": "any other http(s) link", "send": "readable text or page -> PDF",
         "computer": NUC, "container": "gotenberg-web", "via": _env("GOTENBERG_URL"), "grant": "as a document",
         "check": "gotenberg"},
        {"name": ".torrent file", "match": "*.torrent upload or share", "send": "qBittorrent", "computer": PI,
         "container": "qbittorrent (in gluetun)", "via": _env("QBIT_URL") or "off", "grant": "Jason (others held)",
         "check": "qbittorrent"},
        {"name": "Video", "match": "the Videos card (video files)", "send": "Jellyfin library", "computer": NUC,
         "container": "jellyfin (NAS videos/private)", "via": "/srv/media/general | private",
         "grant": _env("INTAKE_VIDEO_GRANT", "jellyfin"), "check": "mount:/srv/media/general"},
        # Abyss step 5 (2026-11-24): "Send as Abyss" on the share result page hands photos/videos to Abyss in the
        # browser (postMessage); Abyss encrypts them there. The server row is for the health dot and the record.
        {"name": "Abyss", "match": "Send as Abyss (photos, videos)", "send": "one-time view-only link",
         "computer": NUC, "container": "abyss", "via": _env("ABYSS_URL", "https://abyss.home.example.net"),
         "grant": "Jason only", "check": "abyss"},
    ]
    for kind, lib in dropconfig.LIBRARIES.items():
        where = lib.get("dest") or f"{lib.get('shelf')} shelf"
        target = {"document": ("Paperless", "paperless-ngx (NAS Paperless/consume)", "mount:/srv/consume"),
                  "book": ("Kavita books", "kavita (NAS shelves)", None), "comic": ("Kavita manga", "kavita", None),
                  "recipe": ("held for the recipe app", "pending folder", None),
                  "workout": ("held for the gym app", "pending folder", None)}.get(kind, (kind, "intake", None))
        rows.append({"name": kind.capitalize(), "match": f"classifier says {kind}", "send": target[0],
                     "computer": NUC, "container": target[1], "via": where, "grant": lib.get("grant"),
                     "check": target[2]})
    rows.append({"name": "Anything else", "match": "not sure what it is", "send": "unsorted (Jason sorts it)",
                 "computer": NUC, "container": "intake", "via": "/srv/unsorted", "grant": "-", "check": None})
    rows.append({"name": "Virus check", "match": "every file, before any route", "send": "ClamAV", "computer": NUC,
                 "container": "clamav", "via": _env("CLAMD_HOST") or "off", "grant": "-", "check": "clamav"})
    return rows


async def _get(url: str, **kw) -> bool:
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(url, **kw)
        return r.status_code < 500
    except httpx.HTTPError:
        return False


async def _tcp(host: str, port: int) -> bool:
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), 3)
        w.close()
        return True
    except (OSError, asyncio.TimeoutError):
        return False


async def check(name: str | None) -> str:
    """green | red | grey for one check name."""
    if not name:
        return "grey"
    if name == "qbittorrent":
        q = _env("QBIT_URL")
        return ("green" if await _get(q + "/api/v2/app/version") else "red") if q else "grey"
    if name == "tor":
        p = _env("TOR_PROXY")
        if not p:
            return "grey"
        hostport = p.split("://", 1)[-1]
        host, _, port = hostport.partition(":")
        return "green" if await _tcp(host, int(port or 9050)) else "red"
    if name == "metube":
        m = _env("METUBE_URL")
        return ("green" if await _get(m) else "red") if m else "grey"
    if name == "gotenberg":
        g = _env("GOTENBERG_URL")
        return "green" if g and await _get(g + "/health") else "red"
    if name == "clamav":
        import scan
        return ("green" if await asyncio.to_thread(scan.ping) else "red") if scan.ENABLED else "grey"
    if name == "abyss":                     # Abyss step 5: green only on 200 from /healthz
        a = _env("ABYSS_HEALTH_URL", "http://abyss:8300").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(a + "/healthz")
            return "green" if r.status_code == 200 else "red"
        except httpx.HTTPError:
            return "red"
    if name.startswith("mount:"):
        d = pathlib.Path(name[6:])
        return "green" if (d / ".mounted").exists() else "red"
    return "grey"


async def table() -> list[dict]:
    rows = routes()
    dots = await asyncio.gather(*(check(r["check"]) for r in rows))
    return [{**{k: v for k, v in r.items() if k != "check"}, "health": d} for r, d in zip(rows, dots)]


def _allowed(email: str | None) -> bool:
    import app
    import portal
    return bool(email) and app._person(email.strip().lower()) == portal.ADMIN


@router.get("/routes.json")
async def routes_json(x_auth_email: str | None = Header(default=None)):
    if not _allowed(x_auth_email):
        return JSONResponse({"error": "Jason only"}, status_code=403)
    return JSONResponse({"host": socket.gethostname(), "routes": await table()})


# Step 7 (2026-11-24, audit High 2a): the page style was an inline <style> (and one style= attribute); it is the file
# /assets/routes.css now, so the strict site CSP (style-src 'self') holds here too.
def _routes_css() -> str:
    import themes
    return (themes.current()["root"] +
        "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);"
        "font:15px/1.45 system-ui,sans-serif}main{max-width:980px;margin:0 auto;padding:20px 16px}"
        "h1{font-size:1.3rem;margin:4px 0 4px}p{color:var(--dim);margin:0 0 14px}"
        ".wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:620px}"
        "th,td{text-align:left;padding:10px 8px;border-bottom:1px solid #2c2142;vertical-align:top}"
        "th{color:var(--dim);font-weight:600;font-size:13px;text-transform:uppercase;letter-spacing:.04em}"
        "small{display:block;color:var(--dim);font-size:13px}"
        ".dot{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:8px;vertical-align:1px}"
        ".green{background:var(--ok)}.red{background:var(--bad)}.grey{background:#6b6385}"
        "a{color:var(--cyan)}" + ".foot{margin-top:14px}")


site_assets.register("routes.css", _routes_css)


@router.get("/routes", response_class=HTMLResponse)
async def routes_page(x_auth_email: str | None = Header(default=None)):
    import themes
    if not _allowed(x_auth_email):
        return HTMLResponse("<p>Jason only.</p>", status_code=403)
    t = themes.current()
    rows = await table()
    label = {"green": "answers", "red": "not answering", "grey": "off / not checked"}
    body = "".join(
        f"<tr><td><span class='dot {r['health']}' title='{label[r['health']]}'></span>{html.escape(r['name'])}"
        f"<small>{html.escape(r['match'])}</small></td><td>{html.escape(r['send'])}<small>{html.escape(str(r['via']))}</small></td>"
        f"<td>{html.escape(r['computer'])}<small>{html.escape(r['container'])}</small></td>"
        f"<td>{html.escape(str(r['grant']))}</td></tr>" for r in rows)
    name = html.escape(t["name"])
    return HTMLResponse(
        "<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<meta name=theme-color content='{t['theme_color']}'><title>Routes · {name}</title>"
        + site_assets.css_tag("routes.css") +
        f"<main><h1>{name} routes</h1><p>Where each kind of share goes, on which computer and container, "
        "and whether it answers right now. Green: answers. Red: not answering. Grey: off.</p>"
        "<div class=wrap><table><tr><th>Share</th><th>Sent to</th><th>Computer · container</th><th>Who</th></tr>"
        + body + "</table></div><p class=foot><a href='./'>Back</a> · <a href='routes.json'>as data</a></p></main>",
        headers={"Cache-Control": "no-store"})
