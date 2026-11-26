"""site_assets.py - the drop site's own style and script files, and its one strict header set (2026-11-24, step 7).

WHY (audit 2026-11-24 High 2a, Jason: "keep the old name and burn the old pages"): the device keys live in
IndexedDB on drop.home.example.net, the same origin as the library pages. Those pages ran inline <script>
and <style> with no CSP, so one injection on any of them could have used the keys. Now no page on the origin has
inline script, inline style or inline handlers: every page's CSS and JS is a file served here (or by its own E2E
module) and every response carries a strict CSP (SITE_CSP, set by app.site_headers when a route set none).

ROUTE
  GET /assets/<name>     a registered file (portal.css/js, sending.css/js, routes.css, shared.css); else 404.
                         Signed-in only like the rest of the site (Caddy forward_auth); no-cache; nosniff.
Files are produced by functions (the theme comes from drop.toml at import), so a page's SRI hash and the served
bytes always come from the same call.
OUTCOME: test_harden.py checks every route's CSP and that no served HTML has an inline script, style or handler.
"""
from __future__ import annotations

import base64
import hashlib
from collections.abc import Callable

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()

# One strict policy for the whole drop site. Pages that need less (the E2E pages: form-action 'none', Trusted
# Types without a policy) or one more source (the inbox: frame-src 'self' for the download frame) set their own,
# equal or stricter. frame-ancestors 'none' everywhere: nothing on this origin may be framed. The Abyss keyring
# popup needs its OPENER (window.opener + postMessage), not framing, so it only relaxes COOP (abyss_keyring.py).
SITE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
            "font-src 'self'; manifest-src 'self'; worker-src 'self'; frame-src 'self'; media-src 'self' blob:; "
            "form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
            "require-trusted-types-for 'script'")
SITE_HEADERS = {"Content-Security-Policy": SITE_CSP, "Cross-Origin-Opener-Policy": "same-origin",
                "Cross-Origin-Resource-Policy": "same-origin", "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()"}

_FILES: dict[str, tuple[Callable[[], str], str]] = {}
_TYPES = {"css": "text/css; charset=utf-8", "js": "text/javascript; charset=utf-8"}


def register(name: str, make: Callable[[], str]) -> None:
    """Serve make() at /assets/<name>; the type comes from the extension (.css or .js only)."""
    ext = name.rsplit(".", 1)[-1]
    if ext not in _TYPES:
        raise ValueError(f"only .css and .js files: {name}")
    _FILES[name] = (make, _TYPES[ext])


def body(name: str) -> bytes:
    return _FILES[name][0]().encode()


def sri(name: str) -> str:
    return "sha384-" + base64.b64encode(hashlib.sha384(body(name)).digest()).decode()


def css_tag(name: str) -> str:
    return f'<link rel="stylesheet" href="/assets/{name}" integrity="{sri(name)}">'


def js_tag(name: str) -> str:
    """A deferred classic script: runs after the page is parsed, in document order with other deferred scripts."""
    return f'<script defer src="/assets/{name}" integrity="{sri(name)}"></script>'


@router.get("/assets/{name}")
def asset(name: str):
    if name not in _FILES:
        return Response("not found", status_code=404, media_type="text/plain")
    return Response(body(name), media_type=_FILES[name][1],
                    headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"})
