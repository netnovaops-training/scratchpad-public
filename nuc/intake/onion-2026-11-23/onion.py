"""onion.py - save a shared Tor (.onion) link, fetched over Tor (2026-11-23).

Jason, 11-23: "If I send a tor link it saves it with novashare". A .onion name never resolves in normal DNS, so
share.py's _public_url refused every one ("only public http(s) links are fetched"). Now /share hands a .onion
link to handle() here:
  - fetched through TOR_PROXY (socks5h://gluetun-tor:9050: the tor-proxy container inside the Proton VPN
    container, so it is Tor over VPN; socks5h = Tor resolves the name, nothing is looked up locally);
  - redirects followed by hand, at most MAX_HOPS, and ONLY to other .onion hosts (a redirect out to the clear
    web is refused: it would leave Tor's protection for a host the person never chose);
  - a file (PDF, Word, photo: link_route.file_kind) is filed like an upload (share._file -> virus gate, sorting);
  - a web page becomes a PDF of its READABLE TEXT only (readable.extract -> readable.page_html -> the local
    Gotenberg convert/html, JavaScript off). The onion page's own HTML is never rendered, so its scripts,
    trackers and remote images never run;
  - Jason only (INTAKE_ADMIN), like the Tor Browser button: others get a refusal.
The full onion address is kept out of intake.log (the result row says "from an onion site").
Env: TOR_PROXY (empty = Tor links off), LINK_MAX_MB (shared with clear-web links).
Variables: MAX_HOPS, TIMEOUT, url/host, info (status, content_type, url, filename), p (downloaded bytes in tmp).
"""
from __future__ import annotations

import html
import os
import pathlib
import re
import urllib.parse

import httpx

TOR_PROXY = os.environ.get("TOR_PROXY", "").strip()
MAX_HOPS = 5
TIMEOUT = httpx.Timeout(90, connect=90)   # onion circuits are slow to build


class Refused(Exception):
    """The link is not fetched (the message is safe to show the person)."""


def is_onion(url: str) -> bool:
    """True for an http(s) URL whose host is a .onion name."""
    try:
        u = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return False
    return u.scheme in ("http", "https") and (u.hostname or "").endswith(".onion")


def _filename(cd: str | None) -> str:
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', cd or "", re.I)
    return urllib.parse.unquote(m.group(1)).strip() if m else ""


async def fetch(url: str, dest: pathlib.Path, max_bytes: int) -> dict:
    """GET url over Tor into dest. Returns {status, content_type, url, filename}. Raises Refused."""
    if not TOR_PROXY:
        raise Refused("Tor links are not switched on")
    async with httpx.AsyncClient(proxy=TOR_PROXY, timeout=TIMEOUT, follow_redirects=False, trust_env=False) as c:
        for _ in range(MAX_HOPS + 1):
            if not is_onion(url):
                raise Refused("the onion site redirected to an address outside Tor; not followed")
            async with c.stream("GET", url, headers={"User-Agent": "Mozilla/5.0", "Accept": "*/*"}) as r:
                if r.status_code in (301, 302, 303, 307, 308) and r.headers.get("location"):
                    url = urllib.parse.urljoin(url, r.headers["location"])
                    continue
                if r.status_code != 200:
                    raise Refused(f"the onion site answered {r.status_code}")
                got = 0
                with dest.open("wb") as out:
                    async for chunk in r.aiter_bytes(1 << 20):
                        got += len(chunk)
                        if got > max_bytes:
                            raise Refused(f"over the {max_bytes // 1048576} MB limit for links")
                        out.write(chunk)
                return {"status": 200, "url": url, "filename": _filename(r.headers.get("content-disposition")),
                        "content_type": (r.headers.get("content-type") or "").split(";")[0].strip().lower()}
        raise Refused("too many redirects")


async def handle(url: str, tmp: pathlib.Path, email: str, details: list) -> dict:
    """One result row for a shared .onion link (see the header). Never raises for the person's sake."""
    import share
    import app
    import link_route
    import portal
    import readable
    from starlette.concurrency import run_in_threadpool

    if app._person(email) != portal.ADMIN:
        return {"name": "onion link", "status": "refused", "why": "Tor links are not enabled for this account."}
    raw = share._in_tmp(tmp, "onion.bin")
    try:
        info = await fetch(url, raw, link_route.LINK_MAX_MB * 1048576)
    except Refused as e:
        return {"name": "onion link", "status": "refused", "why": str(e)}
    except httpx.HTTPError as e:
        details.append({"name": "onion link", "detail": f"tor fetch: {type(e).__name__}"})
        share._in_tmp(tmp, "onion-link.url.txt").write_text(url + "\n")   # keep_failed needs a file to keep
        return {"name": "onion link", "status": "error", "why": "The onion site could not be reached over Tor. Try again later."}

    ext = link_route.file_kind(info)
    if ext:
        name = link_route.file_name(info, ext)
        p = share._in_tmp(tmp, "onion-" + name)
        raw.rename(p)
        row = await share._file(p, name, email, details=details)
        row["name"] = f"{name} (from an onion site)"
        return row
    if info["content_type"] not in ("text/html", "application/xhtml+xml", "text/plain"):
        return {"name": "onion link", "status": "refused", "why": "This kind of onion content is not saved."}
    page = raw.read_bytes()
    if info["content_type"] == "text/plain":
        title, body = "onion page", "<pre>" + html.escape(page.decode("utf-8", "replace")[:500000]) + "</pre>"
    else:
        title, body, _n = await run_in_threadpool(readable.extract, page)
        title = title or "onion page"
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{share.GOTENBERG_HTML}/forms/chromium/convert/html",
                         files={"index.html": ("index.html", readable.page_html(title, body, url).encode(), "text/html")})
    r.raise_for_status()
    name = share._slug(title, "onion-page") + ".pdf"
    p = share._in_tmp(tmp, name)
    p.write_bytes(r.content)
    row = await share._file(p, name, email, details=details)
    row["name"] = f"{name} (from an onion site)"
    return row
