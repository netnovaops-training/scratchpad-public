#!/usr/bin/env python3
"""Phone "Share" -> Drop. Makes the drop page an installable app that shows up in the phone's share sheet.

Jason, 2026-11-18: "when I click on share on my phone it gets pushed to /PDF or whatever paperless is on ...
sending a copy of the URL or doc of PDF ... it can accept compressed folders and automatically tagging".

HOW IT WORKS
- /manifest.webmanifest declares a Web Share Target (action /share, POST multipart). Once the drop page is
  installed (Chrome menu -> "Add to Home screen" / "Install app"), "Drop" appears in Android's share sheet.
  The share POST carries the household SSO cookie, so Caddy's forward_auth still decides who this is
  (X-Auth-Email); there is no new login and no new password.
- Files: each one goes through the EXISTING upload logic (app.route_file), called as a function, so the
  classifier, the per-person shelves, the write grants, de-duplication and the unsorted sidecar all apply
  unchanged. This module adds a door, not a second pipeline.
- Compressed folders: a .zip that is NOT a comic (a zip of images stays one comic, see classify.py) is
  unpacked here and every member is filed on its own. Members are read in memory-safe chunks, path parts
  are dropped (no "../" escapes), and at most ZIP_MAX_FILES members / ZIP_MAX_MB in total are taken.
- A shared link (a Gemini share link, an article): Gotenberg (its own container `gotenberg-web`, at
  GOTENBERG_URL, not Paperless's) prints the page to PDF with headless Chromium, then the PDF is filed like
  any upload.
  Only public http(s) hosts: the address is resolved and private, loopback, link-local and Tailscale
  ranges are refused, so a share cannot make the server fetch the house's own services (SSRF).
- Shared text with no link: wrapped in a minimal HTML page and printed to PDF the same way.
Answers with a small HTML page (the phone shows it), listing where each item went.
OUTCOME: (2026-11-18) first version, T0872. See the T0872 Review lines for the tests.
Gemini (2026-11-18, gemini_patch.py): share links wait 8s, are named "Gemini chat - ...", and a link that
only shows Gemini's home page (dead/private/cut-off share; Google answers 200) is refused, not filed.
PNG icons 192/512 (pwa_patch.py, 11-18): Android needs a PNG to install the share target.
CAPTCHA pages (captcha_patch.py, 11-18) are refused for every link, never solved.
Resumable sends (T0884, 11-18): files shared from the phone now go SW -> /sending -> /up/* (resumable.py,
atomic_ui.py) with a progress bar and resume after a signal drop; this POST /share stays as the fallback
and still handles links and text.
"""
from __future__ import annotations

import site_assets
import themes  # T0942
import onion  # 2026-11-23: .onion links over Tor
import torrent  # 2026-11-23: magnet links -> qBittorrent
import hashlib
import importlib  # T0942 step 3: icon module per theme
import html
import ipaddress
import json
import os
import pathlib
import re
import socket
import tempfile
import urllib.parse
import zipfile

import link_probe  # T0889 step 4: header-only look + IP-pinned download of direct file links
import link_route  # T0889 step 2: YouTube links -> MeTube (off unless METUBE_URL is set)

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

router = APIRouter()

# its own renderer, not Paperless's (see gotenberg_web_patch.py); required (T0927 slice 2), app.py checks it
GOTENBERG = os.environ.get("GOTENBERG_URL", "").rstrip("/")
# Renderer for OUR OWN HTML (shared text, readable article pages): convert/html.
# OUTCOME 2026-11-22 autopilot (T0889 step 5 live test): gotenberg-web answers 403 Forbidden to
# /forms/chromium/convert/html, because its --chromium-allow-list is ^https?://.* and convert/html renders
# file:///tmp/index.html. So shared TEXT (no link) has failed on gotenberg-web since it was split off.
# Paperless's "gotenberg" (file:///tmp only, JavaScript off) is the right one for HTML we built ourselves.
# Unset = GOTENBERG (old behaviour); set GOTENBERG_HTML_URL=http://gotenberg:3000 in compose at the swap.
GOTENBERG_HTML = os.environ.get("GOTENBERG_HTML_URL", "").rstrip("/") or GOTENBERG
ZIP_MAX_FILES = 200
ZIP_MAX_MB = 2048
URL_RE = re.compile(r"https?://\S+")

# 2026-11-23: a theme with its own icons tags their addresses, so phones that cached the old picture fetch the new
# one (Jason saw the old Novashare logo after the swap); atomic gets "" and its manifest stays byte-for-byte the same.
# 2026-11-23 evening: a hash of the icon BYTES (was the module name, which stayed the same when the picture changed
# to purple/black, so phones would have kept the cached icon). atomic: still "" (its manifest stays byte-identical).
ICON_VER = (f"?v={hashlib.sha256(importlib.import_module(themes.current()['icon_module']).ICON_192).hexdigest()[:10]}"
            if themes.current().get("icon_module") else "")

MANIFEST = {
    "name": themes.current()["name"],
    "short_name": themes.current()["short_name"],
    "description": "Share files and links into the household library, Paperless and Kavita.",
    "start_url": "./",
    "scope": "./",
    "display": "standalone",
    "background_color": themes.current()["theme_color"],
    "theme_color": themes.current()["theme_color"],
    "icons": [{"src": "icon-192.png" + ICON_VER, "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
              {"src": "icon-512.png" + ICON_VER, "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
              {"src": "icon.svg" + ICON_VER, "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
    "share_target": {
        "action": "share",
        "method": "POST",
        "enctype": "multipart/form-data",
        "params": {"title": "title", "text": "text", "url": "url",
                   "files": [{"name": "files", "accept": ["*/*"]}]},
    },
}

ICON = (  # Jason 11-18: the share card is "Atomic Share"; the atom echoes his atom-to-supernova insignia
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96"><rect width="96" height="96" rx="20" fill="#0b1026"/>'
    '<g fill="none" stroke="#5ad1ff" stroke-width="4"><ellipse cx="48" cy="48" rx="34" ry="13"/>'
    '<ellipse cx="48" cy="48" rx="34" ry="13" transform="rotate(60 48 48)"/>'
    '<ellipse cx="48" cy="48" rx="34" ry="13" transform="rotate(120 48 48)"/></g>'
    '<circle cx="48" cy="48" r="8" fill="#ff9b3d"/></svg>')

# Network-first pass-through. Chrome wants a service worker for an installable app; this one caches
# nothing, so the page is never stale and no file ever sits in the phone's cache.
# T0884 (2026-11-18): the service worker now catches the share POST and keeps the files on the phone
# until /sending uploads them in resumable pieces; see atomic_ui.py. Plain POST /share below still works.
from atomic_ui import SW


@router.get("/manifest.webmanifest")
def manifest():
    # no-cache (2026-11-23): the browser re-checks the manifest every time, so a new name or icon is seen at once
    return Response(json.dumps(MANIFEST), media_type="application/manifest+json", headers={"Cache-Control": "no-cache"})


@router.get("/icon.svg")
def icon():
    mod = themes.current().get("icon_module")  # T0942 step 3
    return Response(importlib.import_module(mod).ICON_SVG if mod else ICON, media_type="image/svg+xml")


@router.get("/icon-192.png")
def icon_192():
    icons_png = importlib.import_module(themes.current().get("icon_module", "icons_png"))
    return Response(icons_png.ICON_192, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/icon-512.png")
def icon_512():
    icons_png = importlib.import_module(themes.current().get("icon_module", "icons_png"))
    return Response(icons_png.ICON_512, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/sw.js")
def sw():
    return Response(SW, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


def _public_url(url: str) -> str | None:
    """The URL if it is http(s) to a public address, else None (no fetching the house's own services)."""
    try:
        p = urllib.parse.urlsplit(url.strip())
        if p.scheme not in ("http", "https") or not p.hostname:
            return None
        for info in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80)):
            ip = ipaddress.ip_address(info[4][0])
            if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast
                    or ip in ipaddress.ip_network("100.64.0.0/10")):
                return None
        return url.strip()
    except (OSError, ValueError):
        return None


GEMINI_HOSTS = ("gemini.google.com", "g.co")
# Text on Gemini's home page; a dead, private or mistyped share link lands there with HTTP 200 (probe 11-18).
GEMINI_HOME = "where should we start"


# Bot checks that answer 200 (probe 11-18: Google AI Mode link -> reCAPTCHA). Compared without spaces.
BOT_WALLS = ("unusualtrafficfromyourcomputer", "i'mnotarobot", "verifyyouarehuman", "checkingifthesiteconnectionissecure")


def _is_gemini(url: str | None) -> bool:
    """True for a Gemini chat share link (g.co/gemini/share/..., gemini.google.com/share/...)."""
    if not url:
        return False
    p = urllib.parse.urlsplit(url)
    return (p.hostname or "") in GEMINI_HOSTS and "/share/" in p.path


def _pdf_text(pdf: bytes) -> str:
    """Plain text of a PDF, letters only spaced as printed (Gemini's font spaces every letter: 'H i ,  I 'm')."""
    import io

    import pypdf
    raw = " ".join((pg.extract_text() or "") for pg in pypdf.PdfReader(io.BytesIO(pdf)).pages)
    return re.sub(r"\s+", "", raw).lower()


def _slug(s: str, fallback: str) -> str:
    s = re.sub(r"[^A-Za-z0-9 ._-]+", "", s or "").strip()[:80]
    return s or fallback


def _in_tmp(tmp: pathlib.Path, name: str) -> pathlib.Path:
    """tmp/name, refused (ValueError) unless it stays directly inside tmp.

    OUTCOME 2026-11-20 (T0931, CodeQL py/path-injection on sync PR #27): every caller already passes a name
    from safe_name() or _slug(), which drop separators, so nothing could escape. This is the second wall,
    written as normpath + startswith so the scanner can see it.
    """
    base = os.path.normpath(str(tmp))
    p = os.path.normpath(os.path.join(base, name))
    if not p.startswith(base + os.sep) or os.sep in p[len(base) + 1:]:
        raise ValueError("file name leaves the upload folder")
    return pathlib.Path(p)


async def _to_pdf(url: str | None, text: str | None, title: str) -> tuple[bytes, str]:
    """Gotenberg/Chromium: a public URL, or plain text wrapped in HTML, printed to PDF."""
    async with httpx.AsyncClient(timeout=120) as c:
        if url:
            # multipart, not urlencoded: Gotenberg answered 415 to data= (sandbox test 11-18)
            r = await c.post(f"{GOTENBERG}/forms/chromium/convert/url",
                             files={"url": (None, url), "waitDelay": (None, "8s" if _is_gemini(url) else "3s")})
        else:
            body = ("<!doctype html><meta charset=utf-8><title>{t}</title><h1>{t}</h1><pre style='white-space:"
                    "pre-wrap;font:14px/1.5 sans-serif'>{b}</pre>").format(t=html.escape(title), b=html.escape(text or ""))
            r = await c.post(f"{GOTENBERG_HTML}/forms/chromium/convert/html",
                             files={"index.html": ("index.html", body.encode(), "text/html")})
    r.raise_for_status()
    return r.content, _slug(title, "shared") + ".pdf"


async def _file(data_path: pathlib.Path, name: str, email: str,
                hint: str | None = None, shelf: str | None = None, details: list | None = None) -> dict:
    """Hand one file to the /upload logic (app.route_file) and return its verdict.

    T0925 part 2 (A3/A8, 2026-11-19): was `await upload(UploadFile(open(path)))`, which copied the file a
    second time into /tmp and then hashed, called the model and moved it ON the event loop (every other
    phone froze meanwhile). Now the file on disk is routed (moved, not copied) in a worker thread.
    The file at data_path is gone afterwards when it was filed; it stays when an error is raised.
    details: internal error text goes here (for WHY.txt), never into the verdict the phone sees.
    OUTCOME 2026-11-20 (CodeQL py/stack-trace-exposure, sync PR #27): the verdict used to carry a "detail"
    key that callers removed before answering; the scanner could not see the removal, so it is now apart.
    """
    from app import route_file, safe_name  # late import: app imports this module
    from starlette.concurrency import run_in_threadpool
    try:
        res = await run_in_threadpool(route_file, data_path, safe_name(name), data_path.stat().st_size, email,
                                      hint, shelf)
        return {"name": name, **res}
    except Exception as e:  # noqa: BLE001  -- HTTPException or anything else: report it, keep going with the rest
        # T0931 (U14): an HTTPException's detail is written for people; any other error's text is internal
        # (paths, library names), so the phone gets plain words and the text goes to WHY.txt via `details`.
        if hasattr(e, "detail"):
            return {"name": name, "status": "error", "why": e.detail}
        if details is not None:
            details.append({"name": name, "detail": f"{type(e).__name__}: {e}"})
        return {"name": name, "status": "error", "why": "This file could not be filed."}


def _is_comic_zip(path: pathlib.Path) -> bool:
    import classify
    return classify.looks_like_comic(str(path), ".zip")


def _extract(z: zipfile.ZipFile, m: zipfile.ZipInfo, target: pathlib.Path) -> None:
    """Copy one zip member to target in 1 MB chunks (blocking; run in a thread, T0925 A3)."""
    with z.open(m) as src, target.open("wb") as dst:
        while chunk := src.read(1 << 20):
            dst.write(chunk)


async def _unzip_and_file(path: pathlib.Path, email: str, tmp: pathlib.Path,
                          details: list | None = None) -> list[dict]:
    from starlette.concurrency import run_in_threadpool
    out, total = [], 0
    with zipfile.ZipFile(path) as z:
        members = [m for m in z.infolist() if not m.is_dir()][:ZIP_MAX_FILES]
        for m in members:
            total += m.file_size
            if total > ZIP_MAX_MB * 1048576:
                out.append({"name": m.filename, "status": "skipped", "why": f"zip over {ZIP_MAX_MB}MB"})
                break
            from app import safe_name  # late import: app imports this module
            raw = pathlib.PurePosixPath(m.filename.replace("\\", "/")).name   # drop folders: no ../ escapes
            if not raw or raw.startswith("."):
                continue                       # hidden files (".DS_Store") were skipped before; still are
            name = safe_name(raw)              # T0931 item 9: and no control characters in the name
            target = _in_tmp(tmp, f"z{len(out)}-{name}")
            await run_in_threadpool(_extract, z, m, target)
            out.append(await _file(target, name, email, details=details))
    return out


_KIND = {"book": "a book", "comic": "a comic", "document": "paperwork", "recipe": "a recipe",
         "workout": "a workout", "video": "a video"}


def _plain(r: dict) -> str:
    """One result in plain words: the same rules as Atomic.plain in atomic.js (review 11-23 #5)."""
    k, p, dest, st = _KIND.get(r.get("kind"), "a file"), str(r.get("path") or ""), str(r.get("dest") or ""), r.get("status")
    person = str(r.get("person") or "")
    if st == "duplicate":
        return "Already there: this exact file was sent before, so it was not saved twice."
    if st == "kept":
        return "Not filed, but kept safe on the server for Jason to sort."
    if st == "skipped":
        return "Skipped: " + str(r.get("why") or "too big to unpack")
    if st == "refused":
        return "Not saved: " + str(r.get("why") or "this kind of link cannot be read")
    if st == "error":
        return ("Not saved: " + str(r.get("why") or "something went wrong")).rstrip(".") + "."
    if st == "filed":
        if r.get("held"):
            return f"Kept for you: {k}. You can read that library but not add to it, so Jason will file it."
        if "/pending/" in dest or "/.pending/" in p:
            return f"Kept for {person.capitalize() or 'you'}: {k} waits to be added to its app."
        if not r.get("dest"):
            return f"Saved, not sorted: {themes.current()['name']} could not tell what this is, so it waits for Jason."
        if r.get("kind") == "video":
            return "Filed: a video in the " + ("private" if "/private/" in p else "general") + " library."
        if r.get("kind") == "document":
            return "Filed: paperwork, sent to Paperless."
        return f"Filed: {k}" + (f" on {person.capitalize()}'s shelf" if person else "") + "."
    return str(r.get("why") or st or "done")


# Step 7 (2026-11-24, audit High 2a): this page's style was an inline <style>; it is /assets/shared.css now.
def _shared_css() -> str:
    return (themes.current()["root"] +
            "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);"
            "font:16px/1.5 system-ui,sans-serif}main{max-width:560px;margin:0 auto;padding:24px 16px}"
            "h1{font-size:1.35rem;margin:8px 0 16px}ul{list-style:none;padding:0;margin:0}"
            "li{background:var(--card);border-radius:12px;padding:12px 14px;margin:0 0 10px}"
            "li b{display:block;word-break:break-word}li small{color:var(--dim);display:block}"
            "li.ok b::before{content:'\\2713  ';color:var(--ok)}li.err b::before{content:'\\2715  ';color:var(--bad)}"
            "a.done{display:flex;justify-content:center;align-items:center;min-height:48px;margin-top:16px;"
            "border-radius:10px;background:var(--cyan);color:var(--bg);font-weight:600;text-decoration:none}"
            ":focus-visible{outline:3px solid var(--cyan);outline-offset:3px}")


site_assets.register("shared.css", _shared_css)


def _page(results: list[dict]) -> str:
    """The result page when the service worker did not take the share (review 11-23 #5: plain words, theme look).

    Was: raw "book.pdf: filed -> book", no theme, no dark mode, titled "Drop: done"."""
    t = themes.current()
    bad = any(r.get("status") in ("error", "refused") for r in results)
    rows = "".join("<li class='{}'><b>{}</b><small>{}</small></li>".format(
        "err" if r.get("status") in ("error", "refused") else "ok", html.escape(str(r.get("name", ""))),
        html.escape(_plain(r)[:300])) for r in results) or "<li><b>Nothing was shared.</b></li>"
    name = html.escape(t["name"])
    return ("<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<meta name=theme-color content='{t['theme_color']}'><title>{'Needs a look' if bad else 'Shared'} · {name}</title>"
            + site_assets.css_tag("shared.css") +
            f"<main><h1>{'Something needs a look' if bad else 'Shared'}</h1><ul>" + rows +
            "</ul><a class=done href='./'>Done</a></main>")


READ_MAX_MB = 5    # an article page bigger than this is not read; Chromium prints it as before


async def _readable(url: str, info: dict | None, tmp: pathlib.Path, details: list) -> tuple[str, bytes] | None:
    """T0889 step 5: (page title, PDF bytes) of the page's readable text, or None to use the Chromium print.

    Only for a probe that said text/html with status 200. The page is fetched once, IP-pinned
    (link_probe.fetch), so the article text comes from the address that was checked; Gotenberg then
    prints our own plain HTML (readable.page_html), never the site's page. Any failure here is not an
    error for the person sharing: it is noted in details and the old print runs.
    """
    import readable
    if not info or info.get("status") != 200 or info.get("content_type") not in ("text/html",
                                                                                  "application/xhtml+xml"):
        return None
    p = _in_tmp(tmp, "page.html")
    try:
        await link_probe.fetch(info["url"], p, READ_MAX_MB * 1048576)
        from starlette.concurrency import run_in_threadpool
        title, body, n = await run_in_threadpool(readable.extract, p.read_bytes())
    except (link_probe.Refused, link_probe.TooBig, OSError, ValueError) as e:
        details.append({"name": url, "detail": f"readable: {type(e).__name__}: {e}"})
        return None
    finally:
        p.unlink(missing_ok=True)
    if n < readable.MIN_CHARS:
        return None
    title = title or urllib.parse.urlsplit(url).hostname or "page"
    async with httpx.AsyncClient(timeout=120) as c:
        r = await c.post(f"{GOTENBERG_HTML}/forms/chromium/convert/html",
                         files={"index.html": ("index.html", readable.page_html(title, body, url).encode(),
                                               "text/html")})
    r.raise_for_status()                # httpx.HTTPError -> share()'s "could not be printed" row, as before
    return title, r.content


async def _direct_file(url: str, tmp: pathlib.Path, email: str,
                       details: list) -> tuple[dict | None, dict | None]:
    """T0889 step 4: if url is a direct file link (by its headers), download and file it.

    Returns (row, probe info). row None means "not a file we take" (a web page, an unknown type, or the probe got no answer): the caller
    then prints the page to PDF as before, so an unknown link is never dropped.
    A link whose name (or a redirect hop) resolves to a PRIVATE address is refused here, even though
    _public_url passed it: that is the DNS-rebinding case link_probe was built to catch (step 1 finding).
    A failed download is an "error" row, so share()'s finally keeps the link in .failed with WHY.txt.
    """
    try:
        info = await link_probe.probe(url)
    except link_probe.Refused as e:
        if "only public" in str(e):
            return {"name": url, "status": "refused", "why": "only public http(s) links are fetched"}, None
        details.append({"name": url, "detail": f"probe: {e}"})
        return None, None                    # no answer to HEAD: let the PDF printer try, as before step 4
    ext = link_route.file_kind(info)
    if not ext or info.get("status") not in (200, 206):
        return None, info
    name = link_route.file_name(info, ext)
    p = _in_tmp(tmp, "link-" + name)
    try:
        await link_probe.fetch(info["url"], p, link_route.LINK_MAX_MB * 1048576)
    except link_probe.TooBig as e:
        return {"name": url, "status": "refused",
                "why": f"{e}; download it on the phone and share the file instead (big files resume there)"}, info
    except link_probe.Refused as e:
        details.append({"name": url, "detail": f"fetch: {e}"})
        # the part file is gone, so leave the link itself: keep_failed keeps only folders with a file in them
        _in_tmp(tmp, "link-failed.url.txt").write_text(url + "\n")
        return {"name": url, "status": "error", "why": "The file could not be downloaded. Try again later."}, info
    row = await _file(p, name, email, details=details)
    row["name"] = f"{name} (from {urllib.parse.urlsplit(url).hostname})"
    return row, info


class _Skip(Exception):
    """Stop filing this shared item; the reason is already in results."""


@router.post("/share")
async def share(request: Request, x_auth_email: str | None = Header(default=None)):
    from app import safe_name  # late import: app imports this module
    email = (x_auth_email or "").strip().lower()
    if not email:
        return HTMLResponse(_page([{"name": "-", "status": "error", "why": "no identity from the gate"}]), 401)
    form = await request.form()
    title, text, url = (str(form.get(k) or "") for k in ("title", "text", "url"))
    files = [f for f in form.getlist("files") if getattr(f, "filename", None)]
    results: list[dict] = []
    details: list[dict] = []   # internal error text, WHY.txt only (see _file)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="share-"))
    try:
        for f in files:
            # T0931 item 9 (2026-11-20): safe_name (separators, NUL, control and line-separator characters)
            # instead of PurePosixPath(...).name; A10's rule stays: "", "." and ".." are filed as "shared".
            raw = pathlib.PurePosixPath(f.filename).name
            name = safe_name("shared" if raw in ("", ".", "..") else raw)
            sub = tmp / str(len(list(tmp.iterdir())))   # S1: one sub-folder per shared file (no name collisions)
            sub.mkdir()
            p = _in_tmp(sub, name)
            with p.open("wb") as out:
                while chunk := await f.read(1 << 20):
                    out.write(chunk)
            if name.lower().endswith(".zip") and zipfile.is_zipfile(p) and not _is_comic_zip(p):
                results += await _unzip_and_file(p, email, sub, details)
            else:
                results.append(await _file(p, name, email, details=details))
        if not files and (url or text):
            link = url or (URL_RE.search(text).group(0) if URL_RE.search(text) else "")
            safe = _public_url(link) if link else None
            if (mag := torrent.find_magnet(f"{url} {text}")):
                # 2026-11-23 (Jason: "more of a BitTorrent"): magnet -> qBittorrent on the Pi (torrent.py)
                from starlette.concurrency import run_in_threadpool as _in_thread
                results.append(await _in_thread(torrent.handle_magnet, mag, email, details))
            elif link and onion.is_onion(link):
                # 2026-11-23 (Jason: "If I send a tor link it saves it"): fetched over Tor (onion.py), Jason only.
                results.append(await onion.handle(link, tmp, email, details))
            elif link and not safe:
                results.append({"name": link, "status": "refused",
                                "why": "only public http(s) links are fetched"})
            elif safe and link_route.enabled() and link_route.youtube_kind(safe):
                # T0889 step 2 (2026-11-22): YouTube / YT Music / Shorts -> MeTube as mp3 or mp4 instead of a
                # PDF of the player page (that PDF was useless). Queued on MeTube and answered at once, so no
                # download runs on this request. A failure is an "error" row, so the finally below keeps the
                # link in .failed for Jason: never dropped. Off while METUBE_URL is unset (today's behaviour).
                row = await link_route.send_to_metube(safe, link_route.pick_format(safe, f"{text} {title}"))
                if "detail" in row:
                    details.append({"name": link, "detail": row.pop("detail")})
                results.append(row)
            elif safe and (got := await _direct_file(safe, tmp, email, details))[0] is not None:
                # T0889 step 4 (2026-11-22): a link straight to a PDF / Word file / photo is downloaded and
                # filed like an upload (Paperless / photos via route_file), not printed. None = not a file.
                results.append(got[0])
            else:
                info = got[1] if safe else None
                try:
                    # T0889 step 5 (2026-11-22): an article is printed as its readable text (readable.py);
                    # None = too little text or not HTML -> Chromium prints the whole page, as before.
                    art = await _readable(safe, info, tmp, details) if safe and not _is_gemini(safe) else None
                    if art:
                        title = title or art[0]
                    pdf, name = (art[1], _slug(title or art[0], "page") + ".pdf") if art else await _to_pdf(
                        safe, None if safe else text,
                        title or (urllib.parse.urlsplit(safe).hostname if safe else "note"))
                    from starlette.concurrency import run_in_threadpool
                    flat = await run_in_threadpool(_pdf_text, pdf) if safe else ""  # pypdf is CPU work (A3)
                    if safe and any(w in flat for w in BOT_WALLS) and len(flat) < 4000:
                        results.append({"name": link, "status": "refused",
                                        "why": "the site showed a robot check (CAPTCHA) instead of the page; "
                                               "Google and some sites always do this to servers. Instead: open the link on the phone, "
                                               "then Share > Print > Save as PDF and share the PDF here, or select the text and share the text"})
                        raise _Skip
                    if _is_gemini(safe):
                        if GEMINI_HOME.replace(" ", "") in flat and len(flat) < 2000:
                            results.append({"name": link, "status": "refused",
                                            "why": "Gemini showed its home page: this chat is not shared "
                                                   "publicly, was un-shared, or the link is cut off"})
                            raise _Skip
                        import datetime
                        today = datetime.datetime.now().astimezone().date().isoformat()  # server-local date, as date.today() was
                        name = "Gemini chat - " + _slug(title, today) + ".pdf"
                    p = _in_tmp(tmp, name)
                    p.write_bytes(pdf)
                    results.append(await _file(p, name, email, details=details))
                except _Skip:
                    pass
                except httpx.HTTPError as e:
                    results.append({"name": link or "text", "status": "error",
                                    "why": "The page could not be printed to PDF. Try again later."})
                    details.append({"name": link or "text", "detail": f"{type(e).__name__}: {e}"})
    finally:
        # T0931 (re-audit A5): this used to delete everything left in tmp, including files that failed to
        # file. Anything left after an error is now kept in .failed with a WHY.txt, like /up/finish does.
        import shutil

        from resumable import keep_failed
        if any(r.get("status") == "error" for r in results) and keep_failed(
                tmp, email, ", ".join(r.get("name", "?") for r in results), results + details):
            results.append({"name": "-", "status": "kept", "why": "not filed; kept safe on the server for Jason"})
        shutil.rmtree(tmp, ignore_errors=True)
    if request.headers.get("accept", "").startswith("application/json"):
        return JSONResponse(results)
    return HTMLResponse(_page(results))
