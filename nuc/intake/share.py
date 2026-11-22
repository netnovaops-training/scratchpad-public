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

import themes  # T0942
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

import httpx
from fastapi import APIRouter, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

router = APIRouter()

# its own renderer, not Paperless's (see gotenberg_web_patch.py); required (T0927 slice 2), app.py checks it
GOTENBERG = os.environ.get("GOTENBERG_URL", "").rstrip("/")
ZIP_MAX_FILES = 200
ZIP_MAX_MB = 2048
URL_RE = re.compile(r"https?://\S+")

MANIFEST = {
    "name": themes.current()["name"],
    "short_name": themes.current()["short_name"],
    "description": "Share files and links into the household library, Paperless and Kavita.",
    "start_url": "./",
    "scope": "./",
    "display": "standalone",
    "background_color": themes.current()["theme_color"],
    "theme_color": themes.current()["theme_color"],
    "icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
              {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
              {"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
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
    return Response(json.dumps(MANIFEST), media_type="application/manifest+json")


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
            r = await c.post(f"{GOTENBERG}/forms/chromium/convert/html",
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


def _page(results: list[dict]) -> str:
    rows = "".join(
        "<li><strong>{}</strong>: {} &rarr; {}<br><small>{}</small></li>".format(
            html.escape(str(r.get("name", ""))), html.escape(str(r.get("status", ""))),
            html.escape(str(r.get("kind", "") or "-")), html.escape(str(r.get("why", ""))[:300]))
        for r in results) or "<li>Nothing was shared.</li>"
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Drop: done</title><style>body{font:16px/1.5 system-ui;margin:16px;max-width:640px}"
            "li{margin:0 0 12px}small{color:#555}</style><h1>Shared</h1><ul>" + rows +
            "</ul><p><a href='./'>Back to Drop</a></p>")


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
            p = _in_tmp(tmp, name)
            with p.open("wb") as out:
                while chunk := await f.read(1 << 20):
                    out.write(chunk)
            if name.lower().endswith(".zip") and zipfile.is_zipfile(p) and not _is_comic_zip(p):
                results += await _unzip_and_file(p, email, tmp, details)
            else:
                results.append(await _file(p, name, email, details=details))
        if not files and (url or text):
            link = url or (URL_RE.search(text).group(0) if URL_RE.search(text) else "")
            safe = _public_url(link) if link else None
            if link and not safe:
                results.append({"name": link, "status": "refused",
                                "why": "only public http(s) links are fetched"})
            else:
                try:
                    pdf, name = await _to_pdf(safe, None if safe else text,
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
