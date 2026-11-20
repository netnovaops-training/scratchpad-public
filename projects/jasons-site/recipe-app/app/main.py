"""The standalone /recipe app -- browse, one recipe at a time, random pick,
and meal planning, backed by Kitchen's own /internal/recipe-app/* API (see
config.py for why this app has no Tandoor login of its own).

A single static page + fetch()-driven JS, no build tooling -- the same
choice Kitchen itself made everywhere else in this stack (see the original
plan's own reasoning: this project has never needed npm, and a page this
size does not justify being the first time it does). Navigation between
screens uses the History API so it never fully reloads.
"""
import hashlib
import io
import json
from functools import lru_cache
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw

from . import config, diet, gemini

APP_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

# NOT root_path=config.ROOT_PATH -- CAUGHT LIVE (2026-08-24): Caddy's
# `handle_path /recipe/*` already strips "/recipe" before forwarding (it
# sends X-Forwarded-Prefix separately, purely so a proxy-aware app can
# rebuild the public URL). Setting root_path on the FastAPI constructor
# forces it onto every request's scope unconditionally -- Starlette then
# slices that many characters off the FRONT of the already-stripped path
# before route matching, regardless of whether the path still has that
# prefix on it. "/static/app.js" (7 = len("/recipe") chars sliced off)
# became "app.js", which matches nothing -- every static asset and every
# /api/* call 404d through Caddy while working fine hit directly, which
# is exactly the split those two behaviors would produce. config.ROOT_PATH
# is still used below, just as a plain string for the template's own
# browser-facing links -- that was never the broken half.
app = FastAPI(title=config.SITE_NAME)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


def _asset_version(path: Path) -> str:
    """A short hash of a static file's actual bytes, for a cache-busting
    `?v=` on its <script>/<link> tag.

    Jason's own report (2026-08-26): a fix to app.js (auto-searching a
    missing ingredient's picture on open) was live on the server -- the
    running container had the right file, the API returned the right
    data -- and he still could not use it, on the SAME recipe, right
    after being told it was fixed. bare `/static/app.js` with no version
    on it is exactly the shape a browser (this app is installed as a PWA
    on his phone, which caches more eagerly than a normal tab) can keep
    serving from its own cache indefinitely with no request to this
    server at all -- server-side logs would show nothing, because
    nothing ever left his phone. Hashing the real file CONTENT, not its
    mtime, means the version only ever changes when the file actually
    does -- a `docker cp` that touches mtime without changing bytes
    (or a filesystem that does not preserve mtime across the copy at
    all) must not force a cache-bust that never actually happens, same
    as one genuinely must whenever the content changes.
    """
    return hashlib.md5(path.read_bytes()).hexdigest()[:10]


ASSET_JS_V = _asset_version(APP_DIR / "static" / "app.js")
ASSET_CSS_V = _asset_version(APP_DIR / "static" / "style.css")

TIMEOUT = 30.0
IMPORT_TIMEOUT = 120.0  # a slow source page + an LLM cleanup pass, not a quick fetch
# A REAL import (fetch the page + Phi-4 cleanup) specifically, longer than
# IMPORT_TIMEOUT above -- Jason's own report (2026-08-26): a real page
# (thekitchn.com's fried rice) took longer than 120s and this route had
# no way to tell "the import actually succeeded, just slowly" from "it
# broke" -- see api_import()'s own comment.
IMPORT_URL_TIMEOUT = 240.0


async def _kitchen(method: str, path: str, who: str = "", timeout: float = TIMEOUT,
                   **kwargs) -> httpx.Response:
    headers = {"X-Internal-Secret": config.KITCHEN_SECRET}
    if who:
        # Whoever Caddy's forward_auth vouched for on THIS request -- Kitchen's
        # own _who() reads the same header off whatever request reaches it,
        # regardless of how many hops it took, so attribution (who imported
        # this, who kept it) survives going through this app instead of
        # collapsing to "nobody" the way it would with no header at all.
        headers["X-Auth-Email"] = who
    async with httpx.AsyncClient(base_url=config.KITCHEN_URL, timeout=timeout) as client:
        return await client.request(method, path, headers=headers, **kwargs)


def _who(request: Request) -> str:
    return (request.headers.get("X-Auth-Email") or "").strip().lower()


@app.exception_handler(httpx.TimeoutException)
async def _kitchen_timeout(request: Request, exc: httpx.TimeoutException):
    """A safety net, not the primary fix -- Jason's own reports
    (2026-08-26), two in a row on two different routes ("swap in a
    recipe does nothing" was slowness with no feedback; "the save to
    library does nothing" was this exact crash): _kitchen() waiting on
    Kitchen for a real model call or a slow Tandoor push can take longer
    than any timeout chosen for it, and an UNCAUGHT httpx.TimeoutException
    used to reach the client as a bare "Internal Server Error" -- plain
    text, not JSON, which every page here calls .json() on and gets a
    parse error for instead of ever seeing the real problem. /api/import
    and /api/import/{id}/keep catch this themselves for a more specific,
    useful message (Kitchen's own work often finished anyway, seconds
    after this gave up waiting on it); this is what every OTHER route
    that talks to Kitchen falls back on if it is not caught closer to
    where it happened.
    """
    return JSONResponse(
        {"error": "Kitchen is taking longer to answer than expected. Try again in a moment."},
        status_code=504)


@app.exception_handler(httpx.RequestError)
async def _kitchen_unreachable(request: Request, exc: httpx.RequestError):
    """The sibling the handler above needed, and the reason is instructive.

    That one catches httpx.TimeoutException -- Kitchen was there and thinking
    for too long. It does NOT catch the other half: Kitchen not being there
    at all, or going away mid-request. A restart during an import raises
    httpx.RemoteProtocolError ("Server disconnected without sending a
    response"), which is a RequestError and not a TimeoutException, so it
    sailed past and reached the browser as a bare "Internal Server Error" --
    plain text, which the page calls .json() on and reports as

        Unexpected token 'I', "Internal S"... is not valid JSON

    Jason saw exactly that while importing, during a stretch when I was
    rebuilding the Kitchen container repeatedly. The message named the wrong
    layer entirely and told him nothing about what had happened.

    502 rather than 500: this service is fine, the one behind it is not, and
    that distinction is the whole content of the answer.
    """
    return JSONResponse(
        {"error": "Kitchen went away mid-request -- usually a restart. "
                  "Nothing was lost; try again in a moment."},
        status_code=502)


@app.get("/healthz")
async def healthz():
    return {"ok": True}


# ---------------------------------------------------------------------------
#  Installable as an app -- Jason's own request (2026-08-24): "a pop-up...
#  to check to see if they would like to install the website as a desktop
#  app... once they install it... the website don't check again." Chrome's
#  own `beforeinstallprompt` (app.js) is what actually asks and remembers
#  that -- it just needs a real manifest + a registered service worker to
#  fire at all, neither of which this app had before. Same minimal-service-
#  worker trick Kitchen's own main.py already uses for ITS installable
#  pages ("Does nothing but exist. Installability needs a REGISTERED
#  worker with a fetch handler").
# ---------------------------------------------------------------------------
@app.get("/manifest.json")
async def manifest():
    data = {
        "name": config.SITE_NAME,
        "short_name": config.SITE_SHORT_NAME,
        "start_url": f"{config.ROOT_PATH}/#/",
        "scope": f"{config.ROOT_PATH}/",
        "display": "standalone",
        # Jason's own request (2026-08-25): "I want the page to be
        # locked to portrait." The manifest's own "orientation" is the
        # real, reliable way to do this for an INSTALLED app -- the OS
        # itself holds it in portrait, no JS involved and nothing that
        # can silently fail. The Screen Orientation API's lock() call
        # only works inside the Fullscreen API in most browsers and is
        # unsupported on iOS Safari entirely, so it is not this app's
        # primary mechanism -- only a same-effort fallback for whoever
        # is still using it in a normal browser tab, not installed yet.
        "orientation": "portrait",
        "background_color": "#050a12",
        "theme_color": "#050a12",
        # SVG first (crisp at any size, what most browsers actually use),
        # PNG entries alongside it -- see icon_192()/icon_512()'s own
        # comment for why: desktop installability wants a real raster
        # icon at standard sizes, which "any"-sized SVG alone hasn't
        # reliably satisfied everywhere.
        "icons": [
            {"src": f"{config.ROOT_PATH}/icon.svg", "sizes": "any",
             "type": "image/svg+xml", "purpose": "any"},
            {"src": f"{config.ROOT_PATH}/icon-192.png", "sizes": "192x192",
             "type": "image/png", "purpose": "any"},
            {"src": f"{config.ROOT_PATH}/icon-512.png", "sizes": "512x512",
             "type": "image/png", "purpose": "any"},
        ],
    }
    return Response(json.dumps(data), media_type="application/manifest+json")


@app.get("/icon.svg")
async def icon():
    # A crossed fork and knife, the same restrained line-art language
    # theme.css's own .ico svg icons already use (stroked, rounded caps,
    # no fill) -- on a solid --bg square so it reads on any home screen
    # or taskbar, light or dark.
    svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 192 192">'
          '<rect width="192" height="192" rx="40" fill="#050a12"/>'
          '<g transform="translate(96,96)" fill="none" stroke="#5aa2e8" '
          'stroke-width="9" stroke-linecap="round" stroke-linejoin="round">'
          '<g transform="rotate(-24)">'
          '<line x1="0" y1="-54" x2="0" y2="54"/>'
          '<path d="M-15 -54 L-15 -28 Q-15 -16 0 -16 Q15 -16 15 -28 L15 -54"/>'
          '<line x1="-7.5" y1="-54" x2="-7.5" y2="-28"/>'
          '<line x1="7.5" y1="-54" x2="7.5" y2="-28"/>'
          '</g>'
          '<g transform="rotate(24)">'
          '<path d="M0 54 L0 -18 Q0 -54 17 -58 L17 -18 Z"/>'
          '</g></g></svg>')
    return Response(svg, media_type="image/svg+xml")


# CAUGHT LIVE (2026-08-25), Jason's own report: "the install to desktop
# didn't work." The manifest's only icon was SVG, `purpose: "any"` --
# valid per spec, but desktop Chrome/Edge's own installability check has
# a long history of being stricter about vector-only icons than mobile
# Chrome is, wanting a real raster image at standard sizes (192/512) for
# the taskbar/start-menu entry specifically. Not provably THE cause
# without a real browser to test in, but a known, documented risk factor
# worth removing outright rather than leaving in place on a guess.
#
# Redrawn in Pillow rather than converting icon.svg's own file -- no SVG
# rasterizer (cairosvg, rsvg-convert) was available in this image, and
# adding one pulls in real system libraries; Pillow is pure wheels. Not
# a pixel-identical copy of the SVG's curves (PIL's ImageDraw has no
# native bezier support) -- straight lines and rounded rectangles
# standing in for the two curved bits (the knife's bolster, the fork's
# neck), which reads the same at icon size. Drawn once at a high
# internal resolution and downsampled, not built at the exact target
# size, so the diagonal edges anti-alias cleanly instead of looking
# jagged the way drawing straight at 192px would.
_ICON_BG = (5, 10, 18)      # #050a12
_ICON_STROKE = (90, 162, 232)  # #5aa2e8


def _draw_utensil(canvas_size: int, fork: bool) -> Image.Image:
    """One utensil, upright, on its own transparent square -- rotated and
    pasted onto the real icon afterward. `canvas_size` is generous
    headroom around the utensil itself so rotating never clips a corner.

    Iterated against an actual rendered PNG before this shape was kept
    (a first pass -- an oversized rounded-rectangle bolster, a separate
    neck bar for the fork -- rendered as an unrecognizable blob, caught
    by looking at the real output rather than trusting the geometry on
    paper) -- a plain tapered triangle for the knife blade and three
    bare tines for the fork, no separate neck/bolster shape, reads
    cleanly as a crossed fork and knife at icon size."""
    img = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cx = canvas_size // 2
    w = max(3, round(canvas_size * 0.045))
    top = round(canvas_size * 0.06)
    bottom = round(canvas_size * 0.94)
    if fork:
        d.line([(cx, top), (cx, bottom)], fill=_ICON_STROKE, width=w)
        tine_bottom = round(canvas_size * 0.32)
        tine_spacing = round(canvas_size * 0.10)
        cap_half = tine_spacing + w // 2
        d.rounded_rectangle(
            [(cx - cap_half, tine_bottom - w // 2), (cx + cap_half, tine_bottom + w // 2)],
            radius=w // 2, fill=_ICON_STROKE)
        for dx in (-tine_spacing, 0, tine_spacing):
            d.line([(cx + dx, top), (cx + dx, tine_bottom)], fill=_ICON_STROKE, width=w)
    else:
        d.line([(cx, top), (cx, bottom)], fill=_ICON_STROKE, width=w)
        blade_bottom = round(canvas_size * 0.40)
        blade_half_top = round(canvas_size * 0.03)
        blade_half_bottom = round(canvas_size * 0.075)
        d.polygon([
            (cx - blade_half_top, top), (cx + blade_half_top, top),
            (cx + blade_half_bottom, blade_bottom), (cx - blade_half_bottom, blade_bottom),
        ], fill=_ICON_STROKE)
    return img


@lru_cache(maxsize=8)
def _icon_png_bytes(size: int) -> bytes:
    """A crossed fork and knife, same restrained line-art language as
    icon.svg -- see the block comment above for why this exists as a
    second, raster copy. Cached (the icon never changes at runtime) so
    repeat requests -- a browser re-checking installability, or several
    icon sizes on one page load -- don't redraw it from scratch."""
    scale = 4  # draw large, downsample for clean anti-aliasing
    big = size * scale
    base = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    ImageDraw.Draw(base).rounded_rectangle(
        [(0, 0), (big - 1, big - 1)], radius=int(big * 0.208), fill=(*_ICON_BG, 255))

    utensil_canvas = int(big * 0.62)
    knife = _draw_utensil(utensil_canvas, fork=False).rotate(24, resample=Image.BICUBIC, expand=False)
    fork = _draw_utensil(utensil_canvas, fork=True).rotate(-24, resample=Image.BICUBIC, expand=False)
    offset = (big - utensil_canvas) // 2
    base.alpha_composite(knife, (offset, offset))
    base.alpha_composite(fork, (offset, offset))

    out = base.resize((size, size), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


@app.get("/icon-192.png")
async def icon_192():
    return Response(_icon_png_bytes(192), media_type="image/png")


@app.get("/icon-512.png")
async def icon_512():
    return Response(_icon_png_bytes(512), media_type="image/png")


@app.get("/sw.js")
async def service_worker():
    # Does nothing but exist -- see the block comment above. A recipe has
    # to be read live, never served stale from a cache made before an
    # ingredient was fixed, so this deliberately caches nothing.
    js = ('self.addEventListener("fetch", function (e) {});\n')
    return Response(js, media_type="application/javascript")


@app.get("/")
async def index(request: Request):
    """One page for the whole app -- app.js reads the URL itself (via the
    History API) and decides which screen to show. Server-rendering a
    different template per screen would need Kitchen's own data at
    request time for no benefit; the client already has to fetch it
    either way once JS is running.

    Cache-Control: no-cache -- CAUGHT LIVE (2026-08-26): app.js/style.css
    got a content-hash `?v=` specifically so a browser could never keep
    serving an old copy of THEM, but this page is what carries that
    hash, and it had no cache header of its own at all. A browser (this
    app is installed as a PWA on Jason's phone, which treats its own
    start_url more like a native app's launch screen than a normal page)
    holding on to an old copy of THIS document means every `?v=` on it is
    just as stale as the unversioned URL used to be -- a fix landing in
    app.js has nowhere to be discovered from. no-cache (not no-store)
    still lets a conditional GET short-circuit to a fast 304 when nothing
    changed; it just stops a browser from ever skipping that check
    itself and trusting a copy it already has.
    """
    return templates.TemplateResponse(
        request, "index.html",
        {"root": config.ROOT_PATH, "kitchen_root": config.KITCHEN_PUBLIC_ROOT,
         "site_name": config.SITE_NAME, "site_short_name": config.SITE_SHORT_NAME,
         "js_v": ASSET_JS_V, "css_v": ASSET_CSS_V,
         # Which twin this is, so the page can say so on its face --
         # comparing two things you cannot tell apart is not comparing.
         "variant": config.variant(),
         "twin": ("/recipe" if config.GEMINI else "/recipe-gemini")},
        headers={"Cache-Control": "no-cache"})


@app.get("/api/recipes")
async def api_recipes(request: Request, page: int = 1, q: str = "",
                      course: str = "", label: str = "",
                      seed: int = 0, recent: bool = False):
    # WHO is asking has to reach Kitchen: only Kitchen holds the
    # ingredient lists at list time, so only Kitchen can filter this
    # honestly (see diet.py). Without this header it would filter nobody.
    r = await _kitchen("GET", "/internal/recipe-app/recipes",
                       who=_who(request),
                       params={"page": page, "q": q, "course": course, "label": label,
                              "seed": seed, "recent": recent})
    # Filtered on the way OUT, for everyone it applies to -- see diet.py.
    return JSONResponse(diet.filter_payload(r.json(), _who(request)),
                        status_code=r.status_code)


@app.get("/api/recipes/{recipe_id}")
async def api_recipe_detail(request: Request, recipe_id: str, serves: int = 0):
    # who=_who(request) -- Jason's own request (2026-08-25): "I'd like to
    # be able to change the picture... through the actual /recipe logged
    # in as me." Without this, Kitchen only ever sees recipe-app's own
    # shared internal secret (proves the SERVICE is legitimate) and never
    # the real visitor, so its own owner check always came back false --
    # same fix _who()/_kitchen()'s own docstrings already describe for
    # the import/keep routes just below.
    r = await _kitchen("GET", f"/internal/recipe-app/recipes/{recipe_id}",
                       who=_who(request), params={"serves": serves})
    body = (r.json() if r.headers.get("content-type", "").startswith("application/json")
            else {"error": "not found"})
    body = diet.filter_payload(body, _who(request))
    # A hidden recipe is a 404 to the person it is hidden from. Anything
    # softer -- "you may not see this one" -- still tells them it exists
    # and what it probably was.
    if isinstance(body, dict) and body.get("hidden"):
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse(body, status_code=r.status_code)


@app.get("/api/step-notes/{recipe_id}/{n}")
async def api_step_notes(request: Request, recipe_id: str, n: int):
    """What the whole recipe knows about one step -- the half that works
    on the free tier, and the half carrying most of the value: what is
    already in the pan, what goes wrong here, what done looks like."""
    if not config.GEMINI:
        return JSONResponse({"error": "this twin is the local one"}, status_code=404)
    r = await _kitchen("GET", f"/internal/recipe-app/recipes/{recipe_id}",
                       who=_who(request))
    if r.status_code != 200:
        raise HTTPException(404, "not found")
    return JSONResponse(gemini.step_notes(r.json(), n))


@app.get("/api/step-image/{recipe_id}/{n}")
async def api_step_image(request: Request, recipe_id: str, n: int):
    """A drawing of one step, made knowing the whole recipe.

    A refusal comes back as JSON with the REASON rather than a broken
    image, because the likeliest reason -- billing is off -- is fixable
    and would otherwise be invisible."""
    if not config.GEMINI:
        return JSONResponse({"error": "this twin is the local one"}, status_code=404)
    r = await _kitchen("GET", f"/internal/recipe-app/recipes/{recipe_id}",
                       who=_who(request))
    if r.status_code != 200:
        raise HTTPException(404, "not found")
    img, why = gemini.step_image(r.json(), n)
    if img is None:
        return JSONResponse({"error": why}, status_code=503)
    return Response(content=img, media_type="image/png")


@app.get("/api/random")
async def api_random(request: Request):
    who = _who(request)
    # Random has to keep drawing until it finds one they may see, or the
    # dice alone would hand them a recipe the filter then blanks.
    for _ in range(12):
        r = await _kitchen("GET", "/internal/recipe-app/random", who=who)
        body = r.json()
        if not diet.restricted(who) or diet.allowed(body):
            return JSONResponse(body, status_code=r.status_code)
    return JSONResponse({"error": "could not find one to suggest"},
                        status_code=404)


@app.get("/api/install-status")
async def api_install_status(request: Request):
    r = await _kitchen("GET", "/internal/recipe-app/install-status", who=_who(request))
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/install-mark")
async def api_install_mark(request: Request):
    r = await _kitchen("POST", "/internal/recipe-app/install-mark", who=_who(request))
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/recipes/{recipe_id}/shop-add")
async def api_recipe_shop_add(request: Request, recipe_id: str, text: str = Form(...)):
    """One ingredient onto the shared shopping list -- Jason's own
    request (2026-08-26): "can everyone have a checkbox for the food
    item[,] or add it to a shopping cart so they can make sure they have
    the ingredients." """
    r = await _kitchen("POST", f"/internal/recipe-app/shop-add/{recipe_id}",
                       who=_who(request), data={"text": text})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/api/plan")
async def api_plan(start: str, end: str):
    r = await _kitchen("GET", "/internal/recipe-app/plan", params={"start": start, "end": end})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/plan")
async def api_plan_add(date: str = Form(...), recipe_id: str = Form(...),
                       meal_type: str = Form("dinner")):
    r = await _kitchen("POST", "/internal/recipe-app/plan",
                       data={"date": date, "recipe_id": recipe_id, "meal_type": meal_type})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/plan/{entry_id}/remove")
async def api_plan_remove(entry_id: int):
    r = await _kitchen("POST", f"/internal/recipe-app/plan/{entry_id}/remove")
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/api/pantry")
async def api_pantry(have: str = ""):
    r = await _kitchen("GET", "/internal/recipe-app/pantry", params={"have": have})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/api/parser-busy")
async def api_parser_busy():
    """Jason's own request (2026-08-25): "let the user know it's waiting
    to be imported" -- checked before submitting, see app.js."""
    r = await _kitchen("GET", "/internal/recipe-app/parser-busy")
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/import")
async def api_import(request: Request, url: str = Form(""), text: str = Form("")):
    """Paste-a-link import, right here on /recipe -- Jason's own request
    (2026-08-24): "want to add the import part to the /recipe as well."
    A LONGER timeout than every other call in this file -- crawling a
    real source page and running it through an LLM cleanup pass is
    genuinely slow (see Kitchen's own do_import, same pipeline), not a
    quick lookup like everything else here.

    Jason's own report (2026-08-26): a real import (thekitchn.com's
    fried rice) took longer than IMPORT_TIMEOUT and came back as a raw
    "Internal Server Error" the page tried to JSON.parse -- httpx's own
    ReadTimeout was never caught here, so FastAPI's generic 500 handler
    produced plain text, not JSON, for a page that only ever expects
    JSON back. CONFIRMED LIVE: the import had actually SUCCEEDED on
    Kitchen's own side by the time this gave up waiting -- Kitchen has
    no timeout of its own, it just took a while, and the result sat
    there as a real, unclaimed draft with no way for the page to know
    it existed. Two changes: a longer wait before giving up at all
    (matching draw-missing's own 240s, not the shorter default), and a
    real JSON error instead of a crash on whatever's left over.
    """
    try:
        r = await _kitchen("POST", "/internal/recipe-app/import", who=_who(request),
                           timeout=IMPORT_URL_TIMEOUT, data={"url": url, "text": text})
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": "This is taking longer than expected. It may still finish on its "
                      "own -- wait a minute and check Kitchen's own recent imports, or "
                      "try again."},
            status_code=504)
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/import/{draft_id}/keep")
async def api_import_keep(request: Request, draft_id: str):
    """Jason's own report (2026-08-26): "the recipe parsed itself[,] the
    save to library does nothing" -- the SAME bug as /api/import just
    fixed, on a different route: this had no explicit timeout at all, so
    it fell back to the plain 30s default -- CONFIRMED LIVE: Kitchen's
    own tandoor.keep() (matching every ingredient against the household's
    1500+ item food catalog, sometimes with a model's help for anything
    ambiguous) can genuinely take longer than that, and the recipe had
    actually saved successfully seconds after this gave up and crashed
    with a plain-text error. _keep_one()'s own "already kept" fast path
    (checked first, before doing any of the slow work again) makes a
    retry after a timeout safe -- it will not create a second copy."""
    try:
        r = await _kitchen("POST", f"/internal/recipe-app/import/{draft_id}/keep",
                           who=_who(request), timeout=IMPORT_URL_TIMEOUT)
    except httpx.TimeoutException:
        return JSONResponse(
            {"error": "This is taking longer than expected. It may have already saved -- "
                      "wait a moment and try again; saving twice is safe and will not "
                      "duplicate it."},
            status_code=504)
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/swap")
async def api_swap(tandoor_id: str = Form(...), ingredient: str = Form(...), why: str = Form("")):
    """Jason's own request (2026-08-25): "with the models i'd like to
    also suggest substitutions with tools and food items." A longer
    timeout, same reasoning as /api/import -- this is a real model call,
    not a quick lookup."""
    r = await _kitchen("POST", "/internal/recipe-app/swap", timeout=IMPORT_TIMEOUT,
                       data={"tandoor_id": tandoor_id, "ingredient": ingredient, "why": why})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/swap-equipment")
async def api_swap_equipment(tandoor_id: str = Form(...), item: str = Form(...)):
    r = await _kitchen("POST", "/internal/recipe-app/swap-equipment", timeout=IMPORT_TIMEOUT,
                       data={"tandoor_id": tandoor_id, "item": item})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/swap-amount")
async def api_swap_amount(tandoor_id: str = Form(...), ingredient: str = Form(...),
                          have: str = Form(...)):
    """Jason's own request (2026-08-26): "I want to add these features
    back into /recipe" -- the "I already have something specific"
    reverse-conversion form (subs.convert())."""
    r = await _kitchen("POST", "/internal/recipe-app/swap-amount", timeout=IMPORT_TIMEOUT,
                       data={"tandoor_id": tandoor_id, "ingredient": ingredient, "have": have})
    return JSONResponse(r.json(), status_code=r.status_code)


# --- picture editing, inline -----------------------------------------------
# Jason's own request (2026-08-25): "I would still like to go back and
# edit saved pictures already made... only as person1@example.com can I do
# this... allow person1@example.com to directly add images... like on
# /kitchen in /recipe." Every route here forwards who=_who(request) --
# unlike /api/swap above, Kitchen's own gate on these specifically
# checks WHO is asking (see main.py's _can_edit_images()), not just that
# recipe-app itself holds the shared secret.
@app.get("/api/items/{kind}/{item_id}/photo-search")
async def api_item_photo_search(request: Request, kind: str, item_id: str, q: str = ""):
    r = await _kitchen("GET", f"/internal/recipe-app/item-photo-search/{kind}/{item_id}",
                       who=_who(request), params={"q": q})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/items/{kind}/{item_id}/photo-keep")
async def api_item_photo_keep(request: Request, kind: str, item_id: str,
                              url: str = Form(...), title: str = Form("")):
    r = await _kitchen("POST", f"/internal/recipe-app/item-photo-keep/{kind}/{item_id}",
                       who=_who(request), data={"url": url, "title": title})
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/items/{kind}/{item_id}/photo-draw")
async def api_item_photo_draw(request: Request, kind: str, item_id: str):
    # Real image generation, not a quick lookup -- same timeout class as
    # /api/import.
    r = await _kitchen("POST", f"/internal/recipe-app/item-photo-draw/{kind}/{item_id}",
                       who=_who(request), timeout=IMPORT_TIMEOUT)
    return JSONResponse(r.json(), status_code=r.status_code)


@app.post("/api/recipes/{recipe_id}/draw-missing")
async def api_recipe_draw_missing(request: Request, recipe_id: str):
    """Step-by-step photo generation for one recipe -- Jason's own
    request (2026-08-25): "like in /kitchen when I can generate a photo
    for step by step... for a recipe in /recipe." Can draw several
    pictures in one call, same generous timeout as the item-draw route
    above, just longer given there can be more than one image."""
    r = await _kitchen("POST", f"/internal/recipe-app/draw-missing/{recipe_id}",
                       who=_who(request), timeout=240.0)
    return JSONResponse(r.json(), status_code=r.status_code)


@app.get("/api/recipes/{recipe_id}/step/{n}/listen")
async def api_recipe_listen(request: Request, recipe_id: str, n: int):
    """A step's own cached spoken-aloud audio -- Jason's own request
    (2026-08-25): the same Listen button /kitchen already has, "in
    /Recipe" too. Kitchen returns real audio bytes on success and a
    plain-text reason on failure (no voice set up, GPU busy, etc); passed
    through as-is either way rather than wrapped in JSON, so app.js can
    hand the response straight to an <audio> element on success."""
    r = await _kitchen("GET", f"/internal/recipe-app/listen/{recipe_id}/{n}",
                       who=_who(request), timeout=IMPORT_TIMEOUT)
    return Response(r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "text/plain"))
