"""The Kitchen — paste a link, get a recipe you can follow.

Mounted under KITCHEN_ROOT_PATH (/kitchen), behind Caddy's forward_auth. It
never checks a password itself: by the time a request arrives here, the auth
service has already said yes and put the address in X-Auth-Email. That is the
same arrangement /mama and /casey use, and it is why there is no login page in
this app.

WHAT EACH ROUTE COSTS
---------------------
Worth knowing, because the GPU is shared with a phone line that answers real
calls:

    GET  /                free
    POST /import          free IF the page publishes recipe data, one long
                          model call if it does not
    GET  /r/{id}          free, at every portion size, forever
    POST /r/{id}/swap     free if the table knows it, one short call if not
    POST /ideas           one short model call
    POST /r/{id}/keep     free (it is an HTTP call to Mealie)

The expensive one is import, once, and only for pages without structured data.
Everything you do afterwards with a recipe is arithmetic and dictionary
lookups. See the note at the top of importer.py for why that was the goal.
"""
import asyncio
import datetime as _dt
import json
import random
import re
import time
import uuid
from fractions import Fraction
from urllib.parse import quote, urlparse

import httpx
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               PlainTextResponse, RedirectResponse, Response)
from fastapi.templating import Jinja2Templates

from . import diet
from . import prep
from . import (art, basket, core, crawl, dedupe, diagram, extras, foodimages,
               foodphotos, handoff_tandoor, importer, iteminfo,
               labels, llm, mealie, notify, nutrition, people, photos,
               portions, recipe_nutrition, reparse, scale, store, subs,
               tandoor, taxonomy, toolimages, tts, vram)

templates = Jinja2Templates(directory=str(core.APP_DIR / "templates"))
templates.env.globals["root"] = core.ROOT_PATH
templates.env.globals["business"] = core.BUSINESS
templates.env.globals["tagline"] = core.TAGLINE
# A GLOBAL, not a per-route value: base.html's nav links to the library on
# every page, so passing it per route means one template that forgets and one
# page where the link silently vanishes.
templates.env.globals["mealie_public"] = core.MEALIE_PUBLIC
# The nav's one "The library" link now points here, not at Mealie -- Mealie
# is stopped (kept, not deleted, in case a recipe still needs pulling off it
# before the real data migration happens). Per-recipe "open in Mealie" links
# elsewhere still use mealie_public/draft.mealie_slug on purpose: the actual
# recipes have not moved to Tandoor yet, so those stay pointed at the data
# until they do.
templates.env.globals["tandoor_public"] = core.TANDOOR_PUBLIC

# How many links one bulk import will take. Each is a page to fetch and
# some need the model, so a hundred would sit on the GPU for an hour and
# queue behind the phone line. Forty is a comfortable evening's worth --
# raised from 20 after a paste that size got rejected outright and the
# whole pasted list was lost with it (see bulk_start below for the fix to
# that, which matters more than the number itself).
BULK_MAX = 40

app = FastAPI(title=core.BUSINESS, root_path=core.ROOT_PATH,
              docs_url=None, redoc_url=None, openapi_url=None)


@app.on_event("startup")
async def _startup():
    store.init()
    swept = store.sweep()
    if swept:
        print(f"kitchen: swept {swept} draft(s) older than {store.DRAFT_DAYS} days")
    if DRAW_IDLE:
        asyncio.create_task(_idle_artist())


# ---------------------------------------------------------------------------
#  The idle artist
# ---------------------------------------------------------------------------
# Fills in step pictures on recipes that are IN THE LIBRARY, whenever the
# graphics card has nothing else to do.
#
# Kept to the library on purpose. A draft may be swept in thirty days and most
# of them are -- drawing forty pictures for something nobody kept is the card
# doing work that gets deleted. Something in the library is something somebody
# decided to cook again.
#
# There is no schedule and no "quiet hours" setting. imagine answers "busy"
# whenever the card is short of room, and on a box with one GPU that is a
# better definition of downtime than any hour anybody would pick.
DRAW_IDLE = core.os.getenv("KITCHEN_DRAW_IDLE", "1") not in ("0", "no", "off")
DRAW_EVERY = int(core.os.getenv("KITCHEN_DRAW_EVERY_MINUTES", "20")) * 60


async def _idle_artist():
    await asyncio.sleep(90)          # let the stack finish coming up
    while True:
        try:
            ok, _why = await art.room()
            if ok:
                for draft in store.recent(200):
                    # CAUGHT LIVE (2026-08-26), Jason's own report ("let's
                    # also draw all the recipe steps missing in /recipe"):
                    # this checked ONLY mealie_slug, so since the move to
                    # Tandoor -- every recipe kept since then has an EMPTY
                    # mealie_slug and a real tandoor_id instead -- this
                    # loop has been silently skipping every one of them.
                    # Not "drawing nothing new" either: `continue` fires
                    # before _draw_missing() is ever called, so a
                    # Tandoor-kept recipe was never even considered, the
                    # entire time this app has been on Tandoor.
                    if not draft.get("tandoor_id") and not draft.get("mealie_slug"):
                        continue
                    made, why = await _draw_missing(draft["id"], limit=6)
                    if made:
                        print(f"kitchen: drew {made} step picture(s) for "
                              f"{draft['title']!r}")
                    if why:
                        break        # busy, or done -- either way, stop here
                    if made:
                        break        # one recipe per pass; do not hog the card
        except Exception as exc:     # never let the loop die on one bad recipe
            print(f"kitchen: the idle artist tripped over something: {exc}")
        await asyncio.sleep(DRAW_EVERY)


def _who(request: Request) -> str:
    """Whoever Caddy's forward_auth vouched for. Empty when run directly."""
    return (request.headers.get("X-Auth-Email") or "").strip().lower()


def _is_owner(request: Request) -> bool:
    return people.is_owner(_who(request))


# Jason's own request (2026-08-25), stated twice, specifically about
# picture-editing from /recipe: "only as person1@example.com can I do this"
# and "allow person1@example.com to directly add images... like on /kitchen in
# /recipe." A deliberately NARROWER gate than _is_owner() above (which
# also passes person4@example.com, his other owner address) -- his own
# explicit choice, not a guess on this app's part, so it checks the one
# address by name rather than reusing the general owner list.
_IMAGE_EDITOR_EMAIL = "person1@example.com"


def _can_edit_images(request: Request) -> bool:
    return _who(request) == _IMAGE_EDITOR_EMAIL


def _can_write(request: Request) -> bool:
    return people.can_write(_who(request))


def _write_gate(request: Request):
    """None if this person can change something here; otherwise the 403 to
    return instead of doing it.

    ONE FUNCTION, CALLED AT THE TOP OF EVERY ROUTE THAT WRITES. Not a
    dependency or a decorator: this app's routes are a flat list of `async
    def`s, and a dependency would be one more thing to remember to attach
    correctly on the route that gets added next year. A single line at the
    top of the function, doing the one check, is what stays obviously correct
    under maintenance the templates-and-gate pattern already relies on
    everywhere else in this file.
    """
    if not _can_write(request):
        return _oops(request, "You can look through everything here, but "
                              "only Jason, Casey, or Mama can add, change, or "
                              "remove anything. Ask one of them.", 403)
    return None


def _named(cards: list) -> list:
    """Stamp a display name onto every card that has been kept.

    store.cards() only ever hands back the email it was stamped with -- store
    is where data lives, not where "how do we address this person" is decided.
    labels.friendly_name() is what says "mama" instead of "person3", the same
    lookup that already names her pills.
    """
    for c in cards:
        c["kept_by_name"] = labels.friendly_name(c.get("kept_by", ""))
    return cards


def _page(request: Request, name: str, **ctx) -> HTMLResponse:
    # Injected for EVERY page rather than passed per route. The nav in
    # base.html shows an owners-only link; doing that per route means one
    # template that forgets, and a link that silently disappears on one page
    # for no visible reason.
    email = _who(request)
    ctx.setdefault("owner", _is_owner(request))
    ctx.setdefault("can_write", _can_write(request))
    ctx.setdefault("me", email)
    ctx.setdefault("me_name", labels.friendly_name(email))
    # The count next to "Shopping" in the nav. Shown on every page on purpose:
    # the list is only useful if you remember it is there, and a number in the
    # nav is what reminds you before you leave the house.
    ctx.setdefault("pending", store.shopping_pending())
    # Jason's own request (2026-08-25): "when I get a user who is not me
    # logged on and using my page can telegram send me an alert." Every
    # HTML page goes through here, so this is the one true chokepoint --
    # notify.visitor() itself holds the per-person cooldown, so this
    # isn't a message per click.
    if email and not people.is_owner(email):
        notify.visitor(email, labels.friendly_name(email), request.url.path)
    return templates.TemplateResponse(request, name, ctx)


def _oops(request: Request, message: str, status: int = 400) -> HTMLResponse:
    return templates.TemplateResponse(request, "error.html",
                                      {"message": message}, status_code=status)


# ---------------------------------------------------------------------------
#  Home
# ---------------------------------------------------------------------------
async def _home_ctx(pantry: str = "") -> dict:
    # Both health checks at once. Sequentially this is two 8-second timeouts
    # back to back on a bad day, and the home page is the one that must feel
    # instant -- it is what you open with your phone in one hand.
    # CAUGHT LIVE (2026-08-25): this was still calling mealie.health() --
    # Mealie has been stopped since the Tandoor migration (see this
    # module's own docstring), so the home page's own status dot has
    # been reporting the wrong service's health this whole time, unable
    # to ever show "up" no matter how healthy the REAL library (Tandoor)
    # actually was. tandoor.health() mirrors the exact same (ok, why)
    # shape on purpose, so home.html's own template logic needed no
    # change at all -- just this call, and the variable's own name
    # (mealie_ok -> library_ok) so it stops claiming to be about a
    # service this app hasn't talked to in a long time.
    llm_ok, library_ok = await asyncio.gather(llm.health(), tandoor.health())
    # The pantry picker moved to the standalone /recipe app (2026-08-24,
    # Jason's own words: "I don't want extra links to click into, I want
    # it all there on /recipe") -- no reason to keep paying for
    # store.all_ingredients(limit=5000) here on every single home-page
    # view when nothing renders the result any more.
    return dict(recent=_named(store.cards(12)),
               votes=store.vote_counts(), says=store.comment_counts(),
               llm_ok=llm_ok, library_ok=library_ok)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request, pantry: str = ""):
    return _page(request, "home.html", **(await _home_ctx(pantry)))


# ---------------------------------------------------------------------------
#  Bookmark pages -- one tool each, nav stripped, a link back to Mealie.
#
#  Mealie is the app now; these are not meant to be browsed to from a menu,
#  they are meant to be bookmarked (or opened from a Mealie Recipe Action,
#  see /from-mealie below) and used for one thing at a time. Same routes
#  and handlers as the full Kitchen pages behind them -- POST /import,
#  POST /import/bulk, POST /mine are all unchanged; these are just other
#  doors into the same rooms.
# ---------------------------------------------------------------------------
@app.get("/recipe-import", response_class=HTMLResponse)
async def recipe_import_page(request: Request):
    return _page(request, "recipe_import.html", **(await _home_ctx()))


@app.get("/whats-for-dinner", response_class=HTMLResponse)
async def whats_for_dinner_page(request: Request, pantry: str = ""):
    return _page(request, "whats_for_dinner.html", **(await _home_ctx(pantry)))


@app.get("/your-own-recipe", response_class=HTMLResponse)
async def your_own_recipe_page(request: Request):
    home = taxonomy.home_for(labels.COOKS.get(_who(request), ""))
    return _page(request, "your_own_recipe.html",
                 courses=taxonomy.COURSES,
                 home_cuisines=home,
                 other_cuisines=taxonomy.rest_of(home))


# ---------------------------------------------------------------------------
#  Recipe requests -- "can you write down your fried chicken?"
#
#  A specific ask, sent as a shareable link into the SAME write-your-own
#  form as /your-own-recipe -- Jason's own words: "I'd like to ask a family
#  member to make a recipe like my mom's fried chicken or my sister's
#  Alfredo... send a request through a sharable link... they login and fill
#  out their own recipes." Creating one is owner-only (only Jason asks);
#  opening the link and filling it in needs the same can_write everyone who
#  can add a recipe already has -- Mama and Casey, not a stranger.
# ---------------------------------------------------------------------------
@app.get("/requests", response_class=HTMLResponse)
async def requests_page(request: Request):
    if not _is_owner(request):
        return _oops(request, "Only Jason can see this.", 403)
    rows = store.requests_all()
    for r in rows:
        r["link"] = f"{core.PUBLIC_URL}/request/{r['id']}"
        r["created_by_name"] = labels.friendly_name(r["created_by"])
    return _page(request, "requests.html", requests=rows)


@app.post("/requests")
async def requests_create(request: Request, title: str = Form(""),
                          note: str = Form(""), for_email: str = Form("")):
    if not _is_owner(request):
        return _oops(request, "Only Jason can do this.", 403)
    title = title.strip()
    if not title:
        return _oops(request, "It needs a name -- what you're asking for.")
    store.request_add(title, note, for_email, _who(request))
    return RedirectResponse(f"{core.ROOT_PATH}/requests", status_code=303)


@app.get("/request/{req_id}", response_class=HTMLResponse)
async def request_page(request: Request, req_id: str):
    req = store.request_get(req_id)
    if not req:
        return _oops(request, "That request isn't here any more -- ask "
                              "whoever sent you the link for a new one.", 404)
    req["created_by_name"] = labels.friendly_name(req["created_by"])
    home = taxonomy.home_for(labels.COOKS.get(_who(request), ""))
    return _page(request, "request_recipe.html",
                 req=req,
                 courses=taxonomy.COURSES,
                 home_cuisines=home,
                 other_cuisines=taxonomy.rest_of(home))


@app.get("/from-mealie/{slug}")
async def from_mealie(request: Request, slug: str):
    """The other half of the bridge: a link ON a Mealie recipe's own page
    (a Recipe Action, configured in Mealie's own Data Management settings,
    with a link URL of "{this app's public address}/from-mealie/${slug}")
    lands here and gets sent straight into that recipe's Kitchen guide --
    step pictures, read-aloud, scaling, all of it.

    A recipe added straight in Mealie's own UI, or kept before Kitchen
    existed, has no Kitchen draft to send anyone to -- that is a normal,
    expected state, not an error, so it gets a plain explanation instead
    of a 404.
    """
    draft = store.get_by_slug(slug)
    if not draft:
        return _oops(request, "This recipe was not imported through Kitchen, "
                              "so there is no step-by-step guide, pictures, "
                              "or read-aloud for it here yet.", 404)
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft['id']}", status_code=303)


# ---------------------------------------------------------------------------
#  Installing a bookmark page as its own home-screen icon
#
#  Each bookmark page is meant to live as its own icon, not a browser tab
#  Mama has to go find. A web app manifest plus a registered service worker
#  is what Chrome/Android's install prompt actually checks for before it
#  offers itself -- without both, "Add to Home Screen" either never appears
#  or falls back to a plain bookmark with no icon and no standalone window.
#
#  iOS Safari has no install PROMPT at all -- Apple never shipped
#  beforeinstallprompt -- so there is nothing to trigger there. The button
#  (see _install_button.html) detects that and shows the two-tap
#  instructions instead of pretending a button can do it.
# ---------------------------------------------------------------------------
# Names and icons are Jason's own choice for how each one should appear
# once installed to a home screen -- not auto-derived from the page's own
# title, which says something slightly different for a reason (the page
# heading is written for someone mid-task; the home-screen name is what
# they see and tap BEFORE that, so it earns its own separate wording).
INSTALLABLE_PAGES = {
    "recipe-import": {"name": "Copy A Recipe", "short": "Copy A Recipe",
                      "icon": "\U0001F382"},           # cake
    "whats-for-dinner": {"name": "What's For Dinner", "short": "What's For Dinner",
                         "icon": "\U0001F374"},         # fork and knife
    "your-own-recipe": {"name": "Fill In Your Own Recipe", "short": "Your Own Recipe",
                        "icon": "✏️"},        # pencil
}


@app.get("/manifest/{page}.json")
async def install_manifest(page: str):
    meta = INSTALLABLE_PAGES.get(page)
    if not meta:
        return Response(status_code=404)
    manifest = {
        "name": meta["name"],
        "short_name": meta["short"],
        "start_url": f"{core.ROOT_PATH}/{page}",
        # The whole app, not just this one page -- so following a link from
        # here (importing lands on the new recipe's own guide page, say)
        # stays inside the installed window instead of kicking back out to
        # an ordinary browser tab the moment you leave this one path.
        "scope": f"{core.ROOT_PATH}/",
        "display": "standalone",
        "background_color": "#f2f2f2",
        "theme_color": "#e58325",
        "icons": [{"src": f"{core.ROOT_PATH}/icon/{page}.svg",
                  "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],
    }
    # ONLY recipe-import. Sharing a recipe link from any app's own Share
    # button is the mobile equivalent of pasting a URL into the box on this
    # page -- and the thing that actually makes "send me a recipe any time"
    # possible on a phone, where browser extensions do not run at all.
    #
    # `url` and `text` map straight across to Kitchen's own `url`/`text`
    # form fields -- deliberately NOT collapsed onto one field. Sharing a
    # page's link populates `url`; sharing a copied block of recipe text
    # (no link at all) populates `text` -- and do_import() already treats
    # those as two different things (fetch a page vs. parse pasted text).
    # Mapping both share fields onto the same form field would risk one
    # silently clobbering the other when an app sends both.
    if page == "recipe-import":
        manifest["share_target"] = {
            "action": f"{core.ROOT_PATH}/import",
            "method": "POST",
            "enctype": "application/x-www-form-urlencoded",
            "params": {"title": "title", "text": "text", "url": "url"},
        }
    return Response(json.dumps(manifest), media_type="application/manifest+json")


@app.get("/icon/{page}.svg")
async def install_icon(page: str):
    meta = INSTALLABLE_PAGES.get(page)
    if not meta:
        return Response(status_code=404)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">'
          f'<rect width="100" height="100" rx="20" fill="#e58325"/>'
          f'<text x="50" y="66" font-size="52" text-anchor="middle">{meta["icon"]}</text>'
          f'</svg>')
    return Response(svg, media_type="image/svg+xml")


@app.get("/sw.js")
async def service_worker():
    # Does nothing but exist. Installability needs a REGISTERED worker with a
    # fetch handler, not a cache -- a recipe has to be read live, every time,
    # never served stale from a cache from before you edited it.
    return Response("self.addEventListener('fetch', () => {});",
                    media_type="application/javascript")


@app.get("/foods", response_class=HTMLResponse)
async def foods_index(request: Request, q: str = ""):
    """Tandoor's own Food tree, browsable from here instead -- 1500+ raw
    entries is a lot to scroll on Tandoor's own admin, and that page is
    not ours to redesign. Nothing here is hidden or deleted.

    Each row also carries its picture, if it has one -- see
    foodphotos.py's own cache (Tandoor's Food has nowhere of its own to
    put a picture reference, unlike Mealie's old extras field, so
    Kitchen keeps it). No "used in N recipes" count any more -- that read
    Mealie's own extras.usedIn, which Tandoor's Food has no equivalent
    for; sorted alphabetically instead of by usage until there is a cheap
    way to compute that against Tandoor's own data."""
    all_f = await tandoor.all_foods()
    needle = q.strip().lower()
    rows = []
    for f in all_f:
        name = f.get("name") or ""
        if needle and needle not in name.lower():
            continue
        rows.append({
            "id": f.get("id"),
            "name": name,
            "count": 0,
            "image": foodphotos.get(f.get("id")),
        })
    rows.sort(key=lambda r: r["name"].lower())
    return _page(request, "foods.html", rows=rows, q=q, total=len(all_f))


@app.get("/tools", response_class=HTMLResponse)
async def tools_index(request: Request, q: str = ""):
    """Tandoor has no first-class "kitchen tool organizer" the way Mealie
    did (checked against its live schema) -- tools live as a Keyword
    subtree instead (tandoor.py's TOOL_PREFIX convention), and
    tandoor.all_tools() already strips that prefix back off. Picture
    reference still lives in toolimages.py's own cache, same as before
    the migration -- that part never depended on Mealie."""
    all_t = await tandoor.all_tools()
    needle = q.strip().lower()
    rows = []
    for t in all_t:
        name = t.get("name") or ""
        if needle and needle not in name.lower():
            continue
        rows.append({
            "id": t.get("id"),
            "name": name,
            "image": toolimages.get(t.get("id") or ""),
        })
    rows.sort(key=lambda r: r["name"].lower())
    return _page(request, "tools.html", rows=rows, q=q, total=len(all_t))


@app.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


@app.get("/internal/missing-pictures")
async def internal_missing_pictures(request: Request):
    """Every recipe with at least one step picture still missing, for the
    admin panel's cross-app backlog page -- one row per recipe, not one per
    step, since that is what a person clicks through to fix.

    NOT BEHIND forward_auth: nothing maps this container's port to the host
    and Caddy only proxies the public /kitchen/* paths, which the admin
    panel never uses to reach this -- it calls the internal Docker hostname
    directly. The shared secret is the second lock, same reasoning as kb's
    /from-ticket route.
    """
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)

    out = []
    for row in store.kept_with_ids():
        missing = art.missing_steps(row["recipe"])
        if not missing:
            continue
        out.append({
            "title": row["recipe"].get("title") or "Untitled",
            "slug": row["mealie_slug"],
            "missing": len(missing),
            "url": f"{core.ROOT_PATH}/r/{row['id']}",
        })
    return out


@app.get("/internal/food-warnings")
async def internal_food_warnings(request: Request):
    """Ingredient lines the three-tier matcher (tandoor.py's
    resolve_ingredients()) could not confidently place anywhere, even
    after reading the recipe's own context -- the "bring it to my notice"
    tier. Same shape and same gate as /internal/missing-pictures above."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    return [{
        "id": w["id"], "title": w["recipe_title"], "line": w["line"],
        "why": w["why"], "url": f"{core.ROOT_PATH}/r/{w['draft_id']}/edit",
    } for w in store.food_warnings()]


@app.post("/internal/food-warnings/{warning_id}/clear")
async def internal_food_warning_clear(request: Request, warning_id: int):
    """The admin panel's own "dismiss" action -- Jason's own words
    (2026-08-24): "I don't want this link here [Kitchen's /warnings page]
    I want to manage it in my admin panel." Same secret gate as the GET
    above, so this really clears the row (not just a locally-remembered
    "seen it" list on the admin app's own side, the way the picture
    backlog's own Approve button works) -- a resolved ingredient warning
    should actually be gone, not just hidden from one screen."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    store.food_warning_clear(warning_id)
    return {"ok": True}


@app.get("/internal/merge-proposals")
async def internal_merge_proposals(request: Request):
    """dedupe.py's tier-2 output -- pairs it was not mechanically certain
    enough about to merge on its own, for Jason's own review in /admin.
    Same shape and gate as the two lists above."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    return [{
        "id": p["id"], "kind": p["kind"], "item_id": p["item_id"],
        "item_name": p["item_name"], "target_id": p["target_id"],
        "target_name": p["target_name"], "reason": p["reason"],
    } for p in store.merge_proposals()]


@app.post("/internal/merge-proposals/{proposal_id}/approve")
async def internal_merge_proposal_approve(request: Request, proposal_id: int):
    """Actually does the merge -- everything up to here was Phi's
    proposal, not a change. Reuses the same tandoor.merge_food/merge_tool
    dedupe.py's own tier-1 calls, so an approved tier-2 pair merges the
    identical, atomic way a tier-1 auto-merge would have."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    p = store.merge_proposal_get(proposal_id)
    if not p:
        return {"ok": False, "error": "That proposal is not here any more."}
    merge_fn = tandoor.merge_food if p["kind"] == "food" else tandoor.merge_tool
    ok = await merge_fn(p["item_id"], p["target_id"])
    if ok:
        store.merge_proposal_clear(proposal_id)
    return {"ok": ok}


@app.post("/internal/merge-proposals/{proposal_id}/reject")
async def internal_merge_proposal_reject(request: Request, proposal_id: int):
    """Jason said no -- clears the proposal, touches nothing on Tandoor."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    store.merge_proposal_clear(proposal_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
#  Running dedupe.py on demand -- Jason's own request (2026-08-25): "can
#  the llm match all the food items to other items really quick and give
#  me a /llm-findings page, I would like a notice when it's done and a
#  link on from telegram." Backgrounded, not awaited by the request that
#  triggers it -- a full food+tool pass is real minutes of Phi-4 time
#  (vram.parsing() now serializes it against anything else using the
#  model too, see vram.py's own comment), nothing an admin click should
#  sit and wait for.
# ---------------------------------------------------------------------------
_dedupe_running = False


@app.post("/internal/dedupe-run")
async def internal_dedupe_run(request: Request):
    if (gate := _internal_gate(request)):
        return gate
    global _dedupe_running
    if _dedupe_running:
        return {"ok": False, "already_running": True}
    _dedupe_running = True

    async def _run():
        global _dedupe_running
        try:
            tallies = [await dedupe.run(kind) for kind in ("food", "tool")]
            notify.dedupe_done(tallies)
        finally:
            _dedupe_running = False

    asyncio.create_task(_run())
    return {"ok": True, "started": True}


@app.get("/internal/dedupe-status")
async def internal_dedupe_status(request: Request):
    if (gate := _internal_gate(request)):
        return gate
    return {"running": _dedupe_running}


@app.post("/internal/tidy-names-run")
async def internal_tidy_names_run(request: Request):
    """Jason's own request (2026-08-25), browsing /admin/items: strip a
    portion size baked into a food's own name ("(60ml) lemon juice from
    1 lemon" -> "Lemon Juice") without touching what a recipe's own
    ingredient line says. FOOD ONLY, not tools -- CAUGHT LIVE testing
    this against the real library: "large"/"small"/"medium" are safe
    filler for a food's PREP ("large onion, diced") but a real,
    load-bearing distinction for a TOOL ("large bowl" and "small bowl"
    are not interchangeable) -- see dedupe.tidy_names()'s own docstring.
    Pure REST calls, no model, fast enough to run inline rather than
    backgrounded like /internal/dedupe-run."""
    if (gate := _internal_gate(request)):
        return gate
    result = await dedupe.tidy_names("food")
    return result


@app.get("/internal/food-search")
async def internal_food_search(request: Request, kind: str, q: str = "", exclude: str = ""):
    """Jason's own request (2026-08-24): "I would like in the admin panel
    to be able to match manually to a like item... if I can't find the
    match I'd like to make a match for it." dedupe.py's own tier-2 only
    ever proposes a merge when Phi noticed one on its own; this is the
    other direction -- Jason picking the target himself, for anything the
    automatic pass missed or was never confident enough to suggest.
    Plain substring search, case-insensitive -- this is a person typing
    a few letters and picking from what comes back, not a relevance
    contest."""
    if (gate := _internal_gate(request)):
        return gate
    if kind not in ("food", "tool"):
        return PlainTextResponse("no", status_code=404)
    needle = q.strip().lower()
    if not needle:
        return []
    items = await (tandoor.all_foods() if kind == "food" else tandoor.all_tools())
    out = [{"id": it["id"], "name": it.get("name") or ""} for it in items
          if needle in (it.get("name") or "").lower() and str(it["id"]) != exclude]
    out.sort(key=lambda r: (len(r["name"]), r["name"].lower()))
    return out[:20]


@app.post("/internal/manual-merge")
async def internal_manual_merge(request: Request):
    """The actual merge, once Jason has picked the target himself --
    same real Tandoor merge endpoint every automatic merge already goes
    through (tandoor.merge_food/merge_tool), so a manual match behaves
    identically to an approved proposal: every recipe reference moves
    over, the old entry stops existing."""
    if (gate := _internal_gate(request)):
        return gate
    form = await request.form()
    kind = (form.get("kind") or "").strip()
    item_id = (form.get("item_id") or "").strip()
    target_id = (form.get("target_id") or "").strip()
    if kind not in ("food", "tool") or not item_id or not target_id:
        return {"ok": False}
    merge_fn = tandoor.merge_food if kind == "food" else tandoor.merge_tool
    ok = await merge_fn(item_id, target_id)
    return {"ok": ok}


@app.get("/internal/reparse-status")
async def internal_reparse_status(request: Request):
    """The done/undone list -- Jason's own words (2026-08-24): "I'll
    parse the rest manually just give me the ability to do so in /admin
    with a done and undone." """
    if (gate := _internal_gate(request)):
        return gate
    return reparse.status()


@app.post("/internal/reparse/{tandoor_id}")
async def internal_reparse_one(request: Request, tandoor_id: str):
    """One recipe, triggered by hand from /admin -- see reparse.py's own
    reparse_one_recipe() for why this pays its own model-load cost every
    time now, rather than sharing one eviction across a batch."""
    if (gate := _internal_gate(request)):
        return gate
    error = await reparse.reparse_one_recipe(tandoor_id)
    return {"ok": error is None, "error": error}


@app.post("/internal/draw-missing/{tandoor_id}")
async def internal_draw_missing(request: Request, tandoor_id: str):
    """The same _draw_missing() the guide page's own "Draw pictures for
    the N steps without one" button already calls -- reachable from
    /admin too now, Jason's own request (2026-08-24) right after asking
    for the manual re-parse control: "then we can go to the next step...
    generate images [for] the steps for the recipes too." Re-parsed
    recipes are exactly the ones missing step pictures (reparse.py drops
    the old, index-matched ones on purpose), so drawing is the natural
    next click after re-parsing, not a separate errand to Kitchen's own
    page. Does NOT evict Ollama itself -- see the admin panel's own
    "free the GPU" mechanism, called first from there, same as the
    /admin/pictures page already does before any drawing."""
    if (gate := _internal_gate(request)):
        return gate
    draft = store.get_by_tandoor_id(tandoor_id)
    if not draft:
        return {"ok": False, "error": "That recipe is not here any more.", "made": 0}
    made, why = await _draw_missing(draft["id"])
    return {"ok": made > 0, "made": made, "error": why or None}


def _recipe_links(tandoor_id) -> dict:
    """Kitchen's own guide/edit urls for a Tandoor recipe id, or empty
    strings if there is no local draft behind it (should not happen for
    anything kept through Kitchen, but a stray Tandoor-side recipe is not
    impossible)."""
    draft = store.get_by_tandoor_id(tandoor_id)
    if not draft:
        return {"guide_url": "", "edit_url": ""}
    return {"guide_url": f"{core.ROOT_PATH}/r/{draft['id']}",
           "edit_url": f"{core.ROOT_PATH}/r/{draft['id']}/edit"}


@app.get("/internal/food-usage/{food_id}")
async def internal_food_usage(request: Request, food_id: int):
    """Jason's own request (2026-08-24): "let me see the recipe where it
    comes from... open up the actual recipe and read it for myself" --
    before approving a merge, or just browsing /foods, he wants the real
    ingredient line and a link to both READ the recipe (guide_url) and
    FIX it if the line itself is the actual problem (edit_url), not just
    a name in isolation."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    out = []
    for rec in await tandoor.recipes_using_food(food_id):
        out.append({"title": rec["name"], "original_text": rec["original_text"],
                    **_recipe_links(rec["id"])})
    return out


@app.get("/internal/tool-usage/{tool_id}")
async def internal_tool_usage(request: Request, tool_id: int):
    """Same as internal_food_usage above, for a tool."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    out = []
    for rec in await tandoor.recipes_using_tool(tool_id):
        out.append({"title": rec["name"], **_recipe_links(rec["id"])})
    return out


@app.get("/warnings", response_class=HTMLResponse)
async def food_warnings_page(request: Request):
    """The same list as /internal/food-warnings above, but reachable from
    inside Kitchen itself for Jason to actually act on -- the site-wide
    admin panel's own cross-app dashboard has not been wired up to call
    the /internal route yet (that's a separate service, rust/admin/), so
    this exists to make the warnings usable today rather than waiting on
    that. Also hosts the vision-model toggle -- see vram.py."""
    if not _is_owner(request):
        return _oops(request, "Jason reviews these.", 403)
    return _page(request, "warnings.html", warnings=store.food_warnings(),
                 vision_loaded=vram.VISION_MODEL in await vram.loaded())


@app.post("/vision/on")
async def vision_on(request: Request):
    if not _is_owner(request):
        return _oops(request, "Jason only.", 403)
    await vram.vision_on()
    return RedirectResponse(f"{core.ROOT_PATH}/warnings", status_code=303)


@app.post("/vision/off")
async def vision_off(request: Request):
    if not _is_owner(request):
        return _oops(request, "Jason only.", 403)
    await vram.vision_off()
    return RedirectResponse(f"{core.ROOT_PATH}/warnings", status_code=303)


@app.post("/warnings/{warning_id}/clear")
async def clear_food_warning(request: Request, warning_id: int):
    if not _is_owner(request):
        return _oops(request, "Jason reviews these.", 403)
    store.food_warning_clear(warning_id)
    return RedirectResponse(f"{core.ROOT_PATH}/warnings", status_code=303)


@app.get("/internal/foods-missing-pictures")
async def internal_foods_missing_pictures(request: Request):
    """Foods with no picture yet, or whose only picture is still an
    unreviewed AI drawing (Jason's own words: an AI-drawn one must "go to
    my queue of things to do in admin panel"), for the admin panel's
    cross-app backlog page. A food with a real internet photo already
    saved does not reappear here -- that is the dedupe: see foods_index's
    own docstring. Same gate as /internal/missing-pictures above, same
    reasoning for why unauthenticated-but-secret is safe here."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)

    out = []
    for f in await tandoor.all_foods():
        image = foodphotos.get(f.get("id"))
        if image and image.get("source") != "ai-drawn":
            continue
        out.append({
            "title": f.get("name") or "Unnamed",
            "id": f.get("id"),
            "missing": 1,
            "url": f"{core.ROOT_PATH}/foods/{f.get('id')}/photos",
        })
    return out


@app.get("/internal/tools-missing-pictures")
async def internal_tools_missing_pictures(request: Request):
    """Same as internal_foods_missing_pictures above, for Mealie's Tools
    organizer -- see toolimages.py for why a tool's picture reference
    lives in its own cache rather than in Mealie."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)

    out = []
    for t in await tandoor.all_tools():
        image = toolimages.get(t.get("id") or "")
        if image and image.get("source") != "ai-drawn":
            continue
        out.append({
            "title": t.get("name") or "Unnamed",
            "id": t.get("id"),
            "missing": 1,
            "url": f"{core.ROOT_PATH}/tools/{t.get('id')}/photos",
        })
    return out


_ICON_MAP_TTL = 120.0
_icon_map_cache: dict = {"at": 0.0, "data": {}}


async def _get_icon_map() -> dict:
    """name (lowercased) -> {url, kind}, for every food and tool that has
    a picture. `kind` is "food" or "tool", so a caller can size a small
    ingredient icon differently from a bigger tool thumbnail (Jason's own
    request). Shared by the /icon-map route (fetched by mealie-skin's
    injected script, in the browser) and guide()'s own step-icon matching
    (called directly, no HTTP round trip needed for a same-process call)
    -- one cache, one place that decides what "has a picture" means.

    NAMES COME FROM KITCHEN'S OWN STORES, TANDOOR ONLY OVERRIDES THEM.
    This used to be the other way round -- the key came only from the
    remote library, on the reasoning that a food renamed over there would
    otherwise go on matching a stale name. That reasoning was right and
    the failure mode it ignored was worse.

    CAUGHT LIVE (2026-09-06): Tandoor had been running on a SQLite file
    with no volume behind it, so recreating the container took its whole
    library with it -- 785 foods and every tool, gone. Kitchen's own 1,256
    food pictures and 161 tool pictures were untouched on its own volume,
    and each one is stored WITH the name it was saved under. But the map
    was built by walking Tandoor and looking each id up locally, so an
    empty Tandoor meant an empty map: every picture still on disk, not one
    of them reachable, and no error anywhere to say why.

    So the local name is the floor and Tandoor's is the override: a rename
    over there still wins while Tandoor knows the food, and the icons
    survive Tandoor being empty, down, or replaced. Names Tandoor never
    heard of (things saved before it, or since it was wiped) now work too,
    which is most of them.

    Cached for two minutes: this is ~1,400 entries, and every recipe page
    load would otherwise re-read both stores to draw a handful of icons."""
    now = time.time()
    if now - _icon_map_cache["at"] > _ICON_MAP_TTL:
        # CAUGHT LIVE (2026-08-24): this was still reading mealie.all_foods()/
        # all_tools() after the migration -- harmless while Mealie was still
        # up, but Mealie is stopped now (see TANDOOR_MIGRATION_STATUS.md),
        # so guide() -- the page every recipe view renders -- was throwing
        # MealieError on EVERY SINGLE VIEW, unguarded, until this was fixed.
        out: dict = {}
        # has_picture() is not belt-and-braces: the caches carry entries
        # whose folder is empty, and because they are keyed by id while
        # this map is keyed by NAME, an empty one landing second would
        # take the name away from the id that HAS the picture. See either
        # module's has_picture() for the count.
        for kind, cache, exists in (
                ("food", foodphotos.all_with_images(), foodphotos.has_picture),
                ("tool", toolimages.all_with_images(), toolimages.has_picture)):
            for item_id, image in (cache or {}).items():
                name = ((image or {}).get("name") or "").strip().lower()
                if name and (image or {}).get("url") and exists(item_id):
                    out[name] = {"url": image["url"], "kind": kind, "id": item_id}
        # Tandoor's names win where Tandoor has an opinion, so a food
        # renamed over there stops matching the old word here. It is not
        # allowed to REMOVE anything: a food Tandoor has never heard of
        # keeps the name it was saved under. Failure is silent on purpose
        # -- the map above is already complete and correct without this,
        # and losing every icon because a rename could not be checked is
        # the trade that broke this once already.
        try:
            for kind, rows, cache in (
                    ("food", await tandoor.all_foods(), foodphotos.all_with_images()),
                    ("tool", await tandoor.all_tools(), toolimages.all_with_images())):
                for row in rows:
                    rid = str(row.get("id") or "")
                    image = (cache or {}).get(rid) or {}
                    fresh = (row.get("name") or "").strip().lower()
                    was = (image.get("name") or "").strip().lower()
                    if (fresh and image.get("url") and fresh != was
                            and (foodphotos if kind == "food"
                                 else toolimages).has_picture(rid)):
                        out.pop(was, None)
                        out[fresh] = {"url": image["url"], "kind": kind, "id": rid}
        except Exception:       # noqa: BLE001 -- see the note above
            pass
        _icon_map_cache["data"] = out
        _icon_map_cache["at"] = now
    return _icon_map_cache["data"]


_STEP_ICON_LIMIT = 5


def _icons_for_step(text: str, icon_map: dict) -> list:
    """Which known foods/tools (with a picture) are actually mentioned in
    this one step's text -- so a step can show "here's what onion, garlic
    and a Dutch oven look like" right where they are used, not just as a
    big illustration of the step as a whole (Jason's own request).

    LONGEST NAME FIRST, AND CONSUMED ONCE MATCHED. Without that, "onion"
    matches inside a step that actually says "green onion" and both the
    (wrong, too-generic) onion picture and the right green-onion picture
    would show side by side -- checking the longest names first and
    blanking out whatever they matched before checking shorter ones is
    the same fix mealie-skin's own injected script uses for the identical
    problem on Mealie's side, just in Python here since this renders
    server-side. Simple plural tolerance ("carrots" matching "carrot")
    for the same reason a step almost never uses a food's exact singular
    form Mealie stores it under.
    """
    hay = f" {(text or '').lower()} "
    hits, seen = [], set()
    for name in sorted(icon_map.keys(), key=len, reverse=True):
        if not name or name in seen:
            continue
        pattern = r"(?<!\w)" + re.escape(name) + r"s?(?!\w)"
        m = re.search(pattern, hay)
        if not m:
            continue
        seen.add(name)
        hits.append({"name": name, **icon_map[name]})
        hay = hay[:m.start()] + " " + hay[m.end():]
        if len(hits) >= _STEP_ICON_LIMIT:
            break
    # Smaller icons first, then tools -- foods are the common case and
    # this keeps their order stable across steps rather than however the
    # length-sorted match happened to find them.
    hits.sort(key=lambda h: h["kind"] != "food")
    return hits


@app.get("/icon-map")
async def icon_map(response: Response):
    """See _get_icon_map() for what this returns and why it is cached.
    FETCHED BY JAVASCRIPT RUNNING IN THE BROWSER (mealie-skin's injected
    script), which is why this is a plain public route rather than
    /internal/... with the shared secret: a browser has no route to the
    internal Docker network and nowhere safe to keep that secret.

    NOT SAME-ORIGIN. Mealie is on :9443, Kitchen answers on the default
    port under /kitchen -- same domain, different port, and a browser's
    origin check includes the port. CAUGHT LIVE: the fetch inside
    mealie-skin's injected script was silently failing CORS with no
    Access-Control-Allow-Origin header here, so every picture Jason added
    rendered fine on /kitchen/foods and /tools but never once made it
    onto a Mealie page -- explains this route rather than assumes it,
    which is exactly how the wrong assumption got written down the first
    time. Scoped to Mealie's own public origin specifically, not `*`: this
    data is meant for that one page, not any site that asks."""
    data = await _get_icon_map()
    if core.MEALIE_PUBLIC:
        response.headers["Access-Control-Allow-Origin"] = core.MEALIE_PUBLIC
        response.headers["Vary"] = "Origin"
    return data


# ---------------------------------------------------------------------------
#  Pictures for foods and tools -- a real photo first, an AI-drawn picture
#  only as an opt-in, reviewed fallback. See foodimages.py (search and
#  prompt-building, shared by both), foodphotos.py and toolimages.py
#  (where the bytes and, for tools, the picture reference itself live).
# ---------------------------------------------------------------------------
_ITEM_KINDS = {
    "food": {"word": "ingredient", "photo_route": "food-image",
             "store": foodphotos, "error": foodphotos.FoodPhotoError},
    "tool": {"word": "kitchen tool", "photo_route": "tool-image",
             "store": toolimages, "error": toolimages.ToolPhotoError},
}


def _item_photo_edit_url(kind: str, item_id) -> str:
    """The food/tool's own photo-editing PAGE (search online, or draw
    one), not the served image file -- same "foods"/"tools" plural
    slugs food_photos_page()/tool_photos_page() are registered under.
    Jason's own request (2026-08-25): "I'd like to be able to change the
    picture... through the actual /recipe." Shared here so every JSON
    response that shows a food/tool icon (recipe-app's own detail route)
    can link straight to it."""
    if not item_id:
        return ""
    slug = "foods" if kind == "food" else "tools"
    return f"{core.ROOT_PATH}/{slug}/{item_id}/photos"


async def _item_lookup(kind: str, item_id: str) -> dict | None:
    if kind == "food":
        return await tandoor.get_food(item_id)
    return await tandoor.get_tool(item_id)


def _item_image(kind: str, item_id: str, item: dict) -> dict | None:
    if kind == "food":
        return foodphotos.get(item_id)
    return toolimages.get(item_id)


async def _item_save_image(kind: str, item_id: str, item: dict, url: str,
                           source: str, alt: str = "") -> None:
    # Tandoor's Food has nowhere to put a picture reference (no extras,
    # no image field -- checked against its live schema), same problem
    # RecipeTool already had on Mealie's side. Both kinds save to
    # Kitchen's own cache now; see foodphotos.py's module docstring.
    store_mod = foodphotos if kind == "food" else toolimages
    store_mod.set(item_id, item.get("name") or "", url, source, alt)


async def _item_photos_page(request: Request, kind: str, item_id: str,
                            q: str, err: str, found: str) -> HTMLResponse:
    if not _is_owner(request):
        return _oops(request, "Jason finds or draws these pictures.", 403)
    item = await _item_lookup(kind, item_id)
    if not item:
        return _oops(request, "That is not here any more.", 404)
    item_name = item.get("name") or ""
    candidates: list = []
    search_err = err
    if q:
        try:
            candidates = await foodimages.search_images(q)
        except foodimages.ImageSearchError as e:
            search_err = str(e)
    room_ok, room_why = await art.room()
    # item_name, NOT "name" -- _page()'s own second positional argument is
    # the TEMPLATE's name, and passing name=... here collided with it
    # (caught live: every one of these pages 500'd on that alone).
    return _page(request, "item_photos.html", kind=kind,
                word=_ITEM_KINDS[kind]["word"], item_id=item_id,
                item_name=item_name, q=q or item_name, candidates=candidates,
                err=search_err, found=found,
                current=_item_image(kind, item_id, item),
                room_ok=room_ok, room_why=room_why)


@app.get("/foods/{food_id}/photos", response_class=HTMLResponse)
async def food_photos_page(request: Request, food_id: str, q: str = "",
                           err: str = "", found: str = ""):
    return await _item_photos_page(request, "food", food_id, q, err, found)


@app.get("/tools/{tool_id}/photos", response_class=HTMLResponse)
async def tool_photos_page(request: Request, tool_id: str, q: str = "",
                           err: str = "", found: str = ""):
    return await _item_photos_page(request, "tool", tool_id, q, err, found)


async def _item_photo_keep(request: Request, kind: str, item_id: str,
                           url: str, title: str) -> Response:
    if not _is_owner(request):
        return _oops(request, "Jason finds or draws these pictures.", 403)
    item = await _item_lookup(kind, item_id)
    if not item:
        return _oops(request, "That is not here any more.", 404)
    cfg = _ITEM_KINDS[kind]
    back = f"{core.ROOT_PATH}/{kind}s/{item_id}/photos"
    try:
        data = await foodimages.download_image(url)
        name = cfg["store"].save(item_id, data)
    except foodimages.ImageSearchError as e:
        return RedirectResponse(f"{back}?err={quote(str(e))}", 303)
    except cfg["error"] as e:
        return RedirectResponse(f"{back}?err={quote(str(e))}", 303)
    # CAUGHT LIVE (2026-08-25), Jason's own report: "changing pictures
    # doesn't commit the change." The file on disk WAS being replaced
    # correctly (foodphotos.save()/toolimages.save() overwrite in place)
    # -- the served URL just never changed, since the filename is always
    # "photo.<ext>", so a same-format replacement (jpg -> jpg) produced
    # the IDENTICAL src string as before. The browser (and this being a
    # PWA, its own service worker cache too) had no reason to ever
    # re-fetch it. A cache-busting query param makes every save produce
    # a genuinely different URL even when the filename doesn't change.
    served = f"{core.ROOT_PATH}/{cfg['photo_route']}/{item_id}/{name}?v={int(time.time())}"
    try:
        await _item_save_image(kind, item_id, item, served, "internet", title)
    except mealie.MealieError as e:
        return RedirectResponse(f"{back}?err={quote(str(e))}", 303)
    return RedirectResponse(f"{back}?found=1", 303)


@app.post("/foods/{food_id}/photos/keep")
async def food_photo_keep(request: Request, food_id: str,
                          url: str = Form(...), title: str = Form("")):
    return await _item_photo_keep(request, "food", food_id, url, title)


@app.post("/tools/{tool_id}/photos/keep")
async def tool_photo_keep(request: Request, tool_id: str,
                          url: str = Form(...), title: str = Form("")):
    return await _item_photo_keep(request, "tool", tool_id, url, title)


async def _item_photo_draw(request: Request, kind: str, item_id: str) -> Response:
    if not _is_owner(request):
        return _oops(request, "Jason finds or draws these pictures.", 403)
    item = await _item_lookup(kind, item_id)
    if not item:
        return _oops(request, "That is not here any more.", 404)
    cfg = _ITEM_KINDS[kind]
    back = f"{core.ROOT_PATH}/{kind}s/{item_id}/photos"
    ok, why = await art.room()
    if not ok:
        return RedirectResponse(
            f"{back}?err={quote('Cannot draw right now: ' + why)}", 303)
    try:
        prompt = foodimages.prompt_for_item(item.get("name") or "", cfg["word"])
        png = await art.draw(prompt, negative=foodimages.ITEM_NEGATIVE)
    finally:
        await art.release()
    if not png:
        return RedirectResponse(f"{back}?err=Could+not+draw+a+picture+right+now", 303)
    try:
        name = cfg["store"].save(item_id, png, drawn=True)
    except cfg["error"] as e:
        return RedirectResponse(f"{back}?err={quote(str(e))}", 303)
    # CAUGHT LIVE (2026-08-25), Jason's own report: "changing pictures
    # doesn't commit the change." The file on disk WAS being replaced
    # correctly (foodphotos.save()/toolimages.save() overwrite in place)
    # -- the served URL just never changed, since the filename is always
    # "photo.<ext>", so a same-format replacement (jpg -> jpg) produced
    # the IDENTICAL src string as before. The browser (and this being a
    # PWA, its own service worker cache too) had no reason to ever
    # re-fetch it. A cache-busting query param makes every save produce
    # a genuinely different URL even when the filename doesn't change.
    served = f"{core.ROOT_PATH}/{cfg['photo_route']}/{item_id}/{name}?v={int(time.time())}"
    # Labelled honestly right in the alt text, same rule as art.py's
    # captions -- this NEVER gets to claim it is a real photo, here or
    # anywhere it is later shown (foods.html/tools.html both check
    # image.source before rendering a caption).
    try:
        await _item_save_image(kind, item_id, item, served, "ai-drawn",
                               "AI-drawn, not a real photo")
    except mealie.MealieError as e:
        return RedirectResponse(f"{back}?err={quote(str(e))}", 303)
    return RedirectResponse(f"{back}?found=1", 303)


@app.post("/foods/{food_id}/photos/draw")
async def food_photo_draw(request: Request, food_id: str):
    return await _item_photo_draw(request, "food", food_id)


@app.post("/tools/{tool_id}/photos/draw")
async def tool_photo_draw(request: Request, tool_id: str):
    return await _item_photo_draw(request, "tool", tool_id)


@app.get("/food-image/{food_id}/{name}")
async def serve_food_image(food_id: str, name: str):
    p = foodphotos.path(food_id, name)
    if not p:
        return PlainTextResponse("No such picture.", status_code=404)
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/tool-image/{tool_id}/{name}")
async def serve_tool_image(tool_id: str, name: str):
    p = toolimages.path(tool_id, name)
    if not p:
        return PlainTextResponse("No such picture.", status_code=404)
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/item-info/{kind}/{item_id}")
async def item_info(request: Request, kind: str, item_id: str):
    """The little reference card behind a food/tool's picture -- Jason's
    own request (2026-08-24): tapping a food/tool name that already has a
    picture (see guide.html's own JS) opens this instead of leaving for a
    real site. Generated once, the same lazy-fill-on-first-view shape as
    _extras_later -- the guide itself never waits on this, and a card
    already generated (or hand-edited) is never regenerated."""
    if kind not in ("food", "tool"):
        return PlainTextResponse("no", status_code=404)
    item = await _item_lookup(kind, item_id)
    if not item:
        return PlainTextResponse("no", status_code=404)
    name = item.get("name") or ""
    cached = iteminfo.get(kind, item_id)
    if cached is None:
        cached = await iteminfo.suggest(kind, name)
        iteminfo.save(kind, item_id, cached)
    picture = _item_image(kind, item_id, item)
    return {
        "name": name,
        "picture": (picture or {}).get("url"),
        "where_to_find": cached.get("where_to_find", ""),
        "how_to_use": cached.get("how_to_use", ""),
        "history": cached.get("history", ""),
        "fun_fact": cached.get("fun_fact", ""),
        "edited": cached.get("edited", False),
        "nutrition": iteminfo.nutrition_card(name) if kind == "food" else None,
        "editable": _is_owner(request),
    }


@app.post("/item-info/{kind}/{item_id}")
async def item_info_save(request: Request, kind: str, item_id: str,
                         where_to_find: str = Form(""), how_to_use: str = Form(""),
                         history: str = Form(""), fun_fact: str = Form("")):
    """Jason's own words: "that I'll fill in." A hand-saved card is
    `edited=True` and iteminfo.suggest() never runs again for this item --
    same trust rule taxonomy.py's `locked` already uses for course/cuisine."""
    if not _is_owner(request):
        return _oops(request, "Jason fills these in.", 403)
    if kind not in ("food", "tool"):
        return PlainTextResponse("no", status_code=404)
    iteminfo.save(kind, item_id, {
        "where_to_find": where_to_find.strip(), "how_to_use": how_to_use.strip(),
        "history": history.strip(), "fun_fact": fun_fact.strip(),
    }, edited=True)
    return {"ok": True}


@app.get("/internal/item-name/{kind}/{item_id}")
async def internal_item_name(request: Request, kind: str, item_id: int):
    """Just one food/tool's current name -- a single, cheap Tandoor call,
    for /admin's manual-match page, which used to fetch the WHOLE
    item-info-list (1300+ items, each with its own usage lookup) just to
    read one name off it. CAUGHT LIVE (2026-08-24): once item-info-list
    stopped being scoped to picture-having items only, that shared fetch
    started taking 20+ seconds -- fine for the list page itself, much too
    slow for a page that only ever needed one row."""
    if (gate := _internal_gate(request)):
        return gate
    if kind not in ("food", "tool"):
        return PlainTextResponse("no", status_code=404)
    it = await (tandoor.get_food(item_id) if kind == "food" else tandoor.get_tool(item_id))
    if not it:
        return PlainTextResponse("not found", status_code=404)
    return {"id": it.get("id"), "name": it.get("name") or ""}


@app.get("/internal/item-info-list/{kind}")
async def internal_item_info_list(request: Request, kind: str):
    """Every food/tool Tandoor knows about, with its current picture/card
    (if any) -- for /admin's own management page. Jason's own request
    (2026-08-24): "I would like to manage all the tools and food items
    there with .json files."

    CAUGHT LIVE (2026-08-24): this used to skip anything without a picture
    yet -- reasonable for what the app itself ever shows a card for, but
    it meant a brand-new food (his own example: a just-imported
    "coriander") was invisible here until someone separately gave it a
    picture first, with no way to even find it from this page. Every food/
    tool now shows; `picture` is just empty for ones that don't have one.

    `used_in` is fetched CONCURRENTLY, bounded by a semaphore -- CAUGHT
    LIVE moving from "a few hundred picture-having items" to "every food,
    1500+": doing this one at a time (the original shape, fine at a few
    hundred) would serialize a network round trip per item and make this
    page take minutes to load."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    if kind not in ("food", "tool"):
        return PlainTextResponse("no", status_code=404)
    picture_store = foodphotos if kind == "food" else toolimages
    usage_fn = tandoor.recipe_names_using_food if kind == "food" else tandoor.recipes_using_tool
    items = await (tandoor.all_foods() if kind == "food" else tandoor.all_tools())
    cards = iteminfo.all_cards(kind)

    sem = asyncio.Semaphore(30)

    async def _usage(item_id):
        async with sem:
            try:
                return await usage_fn(item_id)
            except tandoor.TandoorError:
                return []

    used_in_lists = await asyncio.gather(*(_usage(it["id"]) for it in items))

    photo_slug = "foods" if kind == "food" else "tools"
    out = []
    for it, used_in in zip(items, used_in_lists):
        pic = picture_store.get(it["id"]) or {}
        card = cards.get(str(it["id"])) or {}
        out.append({
            "id": it["id"], "name": it.get("name") or "", "picture": pic.get("url") or "",
            # Jason's own request (2026-08-25): "I'd like to edit
            # pictures there too to find a better match" -- /admin/items
            # only ever linked to a picture for ITEMS MISSING ONE (and
            # even then, to the whole /pictures backlog, not this one
            # item's own page). Same URL shape as the missing-pictures
            # endpoints below, sent for EVERY item regardless of whether
            # it already has a picture, so admin can link straight to
            # "find a better one" too.
            "photo_url": f"{core.ROOT_PATH}/{photo_slug}/{it['id']}/photos",
            "where_to_find": card.get("where_to_find", ""), "how_to_use": card.get("how_to_use", ""),
            "history": card.get("history", ""), "fun_fact": card.get("fun_fact", ""),
            "edited": card.get("edited", False),
            "used_in": [u["name"] for u in used_in],
        })
    out.sort(key=lambda r: r["name"].lower())
    return out


@app.post("/internal/item-info/{kind}/{item_id}")
async def internal_item_info_save(request: Request, kind: str, item_id: str):
    """The admin panel's own save -- same shape as item_info_save above,
    but secret-gated (server-to-server from /admin) instead of owner-
    session-gated (there is no Kitchen browser session to check when the
    caller is the Rust admin app, not Jason's own browser)."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    if kind not in ("food", "tool"):
        return PlainTextResponse("no", status_code=404)
    form = await request.form()
    iteminfo.save(kind, item_id, {
        "where_to_find": (form.get("where_to_find") or "").strip(),
        "how_to_use": (form.get("how_to_use") or "").strip(),
        "history": (form.get("history") or "").strip(),
        "fun_fact": (form.get("fun_fact") or "").strip(),
    }, edited=True)
    return {"ok": True}


@app.post("/internal/item-info-bulk/{kind}")
async def internal_item_info_bulk_save(request: Request, kind: str):
    """Many cards at once -- Jason's own request (2026-08-24): "will it
    help... to add [name] for export and import when doing this in bulk."
    Body is a JSON array of {id, where_to_find, how_to_use, history,
    fun_fact}, the same shape /admin's own bulk textarea round-trips
    through an LLM -- "name"/other stray keys are simply ignored, `id` is
    the only one that matters for where a card lands. Best-effort per row:
    one bad entry (missing id, wrong types) is skipped, not a failure for
    the whole batch, since a large pasted-back JSON array is exactly the
    kind of input a person hand-edited and might have one typo in."""
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    if kind not in ("food", "tool"):
        return PlainTextResponse("no", status_code=404)
    try:
        rows = await request.json()
    except json.JSONDecodeError:
        return PlainTextResponse("That isn't valid JSON.", status_code=400)
    if not isinstance(rows, list):
        return PlainTextResponse("Expected a JSON array.", status_code=400)
    saved = 0
    skipped = 0
    for row in rows:
        if not isinstance(row, dict) or not row.get("id"):
            skipped += 1
            continue
        iteminfo.save(kind, row["id"], {
            "where_to_find": str(row.get("where_to_find") or "").strip(),
            "how_to_use": str(row.get("how_to_use") or "").strip(),
            "history": str(row.get("history") or "").strip(),
            "fun_fact": str(row.get("fun_fact") or "").strip(),
        }, edited=True)
        saved += 1
    return {"ok": True, "saved": saved, "skipped": skipped}


@app.post("/internal/item-rename/{kind}/{item_id}")
async def internal_item_rename(request: Request, kind: str, item_id: int):
    """Renaming a Tandoor Food/Keyword is automatically live everywhere
    that references it -- every recipe holds a foreign key to this one
    shared row, not a copy of its name (see tandoor.rename_food/
    rename_tool's own docstrings). Jason's own request (2026-08-24):
    "I would like to be able change the name of the food item... when I
    update it update the recipe" -- that second half is free, this route
    is the whole fix.

    CAUGHT LIVE (2026-08-24), renaming "all-purpose unbleached flour" to
    "All-Purpose Flour": Tandoor has a REAL Food already at that exact
    name, and its own PATCH doesn't validate that gracefully -- it 500s
    on an unhandled DB uniqueness error. Jason's own words: "the same
    flour... I would like instead to get a notification to merge them
    rather than an error message." So this checks for that collision
    FIRST, before ever sending the doomed PATCH, and answers 409 with
    the colliding item's own id -- /admin turns that straight into "find
    a match" pre-filled with this exact item, not a flat error."""
    if (gate := _internal_gate(request)):
        return gate
    if kind not in ("food", "tool"):
        return PlainTextResponse("no", status_code=404)
    form = await request.form()
    new_name = (form.get("name") or "").strip()
    if not new_name:
        return PlainTextResponse("A name is required.", status_code=400)
    existing = await (tandoor.all_foods() if kind == "food" else tandoor.all_tools())
    collision = next((it for it in existing
                      if it["id"] != item_id and (it.get("name") or "").strip().lower() == new_name.lower()),
                     None)
    if collision:
        return JSONResponse(status_code=409, content={
            "ok": False, "conflict": True,
            "existing_id": collision["id"], "existing_name": collision.get("name") or "",
        })
    ok = await (tandoor.rename_food(item_id, new_name) if kind == "food"
               else tandoor.rename_tool(item_id, new_name))
    if not ok:
        return PlainTextResponse("Tandoor refused that rename.", status_code=502)
    return {"ok": True, "name": new_name}


def _fdc_nutrient_rows(payload) -> list:
    """Pull a flat list of {nutrientName, unitName, value} out of whatever
    shape someone pasted -- Jason's own request (2026-08-25): "upload in
    the Json file nutritional information... since that's publicly
    available." USDA's own site/API hands this back in two different
    shapes depending which endpoint served it: `/foods/search` results
    are flat (nutrientName/unitName/value right on each row); a single
    food's own detail page (fdc.nal.usda.gov/food-details/{id}/nutrients)
    nests it (foodNutrients[].nutrient.name/.unitName +
    foodNutrients[].amount). Normalizing both here means nutrition._extract
    -- the SAME parser the automated USDA lookup already uses -- never
    needs to know or care which one it was handed, so a hand-pasted
    baseline is read exactly as strictly as an automated one."""
    rows = payload.get("foodNutrients") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        nested = row.get("nutrient") if isinstance(row.get("nutrient"), dict) else {}
        name = row.get("nutrientName") or nested.get("name") or ""
        unit = row.get("unitName") or nested.get("unitName") or ""
        value = row.get("value")
        if value is None:
            value = row.get("amount")
        if name and value is not None:
            out.append({"nutrientName": name, "unitName": unit, "value": value})
    return out


@app.get("/internal/food-nutrition/{food_id}")
async def internal_food_nutrition_get(request: Request, food_id: int):
    """The baseline currently stored for this food (if any), keyed by its
    CURRENT name -- store.food_baseline() is name-keyed (it started as a
    plain USDA-name lookup, long before a Tandoor food id existed), so a
    rename moves what this reads. /admin pre-fills its paste box with
    this."""
    if (gate := _internal_gate(request)):
        return gate
    food = await tandoor.get_food(food_id)
    if not food:
        return PlainTextResponse("not found", status_code=404)
    base = store.food_baseline(food.get("name") or "")
    return {"name": food.get("name") or "", "baseline": base}


@app.post("/internal/food-nutrition/{food_id}")
async def internal_food_nutrition_set(request: Request, food_id: int):
    """Paste-in nutrition, grounding a food's baseline in USDA's own
    published numbers directly instead of trusting the automated
    search/LLM-guess match -- Jason's own words: "since that's publicly
    available." Overwrites unconditionally: a person choosing to paste
    this in is deliberately overriding whatever was there, the same way
    typing a new value into any other field here does."""
    if (gate := _internal_gate(request)):
        return gate
    food = await tandoor.get_food(food_id)
    if not food:
        return PlainTextResponse("not found", status_code=404)
    try:
        payload = await request.json()
    except json.JSONDecodeError:
        return PlainTextResponse("That isn't valid JSON.", status_code=400)
    rows = _fdc_nutrient_rows(payload)
    if not rows:
        return PlainTextResponse(
            "No nutrients found in that JSON -- expected USDA's own "
            "foodNutrients shape (paste the whole food, or just that array).",
            status_code=400)
    nutrients = nutrition._extract(rows)
    if not nutrients:
        return PlainTextResponse(
            "Found nutrient rows, but none matched a nutrient this app tracks.",
            status_code=400)
    fdc_id = payload.get("fdcId") if isinstance(payload, dict) else None
    matched_name = (payload.get("description") if isinstance(payload, dict) else None) \
        or food.get("name") or ""
    baseline = {
        "source": "Pasted in from USDA FoodData Central",
        "fdcId": fdc_id, "matchedName": matched_name,
        "dataType": "manual USDA upload", "per": "100g", "nutrients": nutrients,
    }
    store.set_food_baseline(food.get("name") or "", baseline)
    return {"ok": True, "baseline": baseline}


@app.get("/internal/food-portion/{food_id}")
async def internal_food_portion_get(request: Request, food_id: int):
    """The baseline serving weight stored for this food, if any -- see
    portions.py's own docstring. /admin pre-fills its edit box with this."""
    if (gate := _internal_gate(request)):
        return gate
    return portions.get(food_id) or {}


@app.post("/internal/food-portion/{food_id}")
async def internal_food_portion_set(request: Request, food_id: int):
    """Jason's own words (2026-08-25): "I will hand type anything... that
    is not proportional, like a box of mangoes." This is that hand-typed
    path -- always marked edited, so a later re-import (internal_food_
    portions_import below) never overwrites it."""
    if (gate := _internal_gate(request)):
        return gate
    form = await request.form()
    unit = (form.get("unit") or "").strip()
    grams_raw = (form.get("grams") or "").strip()
    if not unit or not grams_raw:
        return PlainTextResponse("Both a unit and a gram weight are required.", status_code=400)
    try:
        grams = float(grams_raw)
    except ValueError:
        return PlainTextResponse("That gram weight isn't a number.", status_code=400)
    if grams <= 0:
        return PlainTextResponse("That gram weight has to be positive.", status_code=400)
    portions.set_baseline(food_id, unit, grams, source="hand-typed", edited=True)
    return {"ok": True}


@app.post("/internal/food-portions-import")
async def internal_food_portions_import(request: Request):
    """Match every current Tandoor food against USDA's own SR Legacy
    portion table -- Jason's own request (2026-08-25): "import a baseline
    serving size we wire into a Json file along with each item." Skips
    anything already hand-corrected (portions.build_baselines() itself
    guarantees that); re-running this later (a new food added, say) is
    always safe."""
    if (gate := _internal_gate(request)):
        return gate
    foods = await tandoor.all_foods()
    return portions.build_baselines(foods)


@app.get("/internal/nutrition-review")
async def internal_nutrition_review(request: Request):
    """usda_local.audit()'s queue -- a food whose cached nutrition and the
    local USDA match disagree by more than 2x, with no automatic call
    made either way (see usda_local.py's own comment on why auto-fixing
    this was tried and abandoned)."""
    if (gate := _internal_gate(request)):
        return gate
    return [{
        "id": r["id"], "name": r["name"],
        "cached_name": r["cached"].get("matchedName"),
        "cached_cal": (r["cached"].get("nutrients") or {}).get("calories", {}).get("value"),
        "local_name": r["local"].get("matchedName"),
        "local_cal": (r["local"].get("nutrients") or {}).get("calories", {}).get("value"),
    } for r in store.nutrition_review_all()]


@app.post("/internal/nutrition-review/{review_id}/apply")
async def internal_nutrition_review_apply(request: Request, review_id: int):
    """`choice` is "local" (write the local USDA match as the new
    baseline), "cached" (leave the existing cache alone), or "skip" --
    either way the row leaves the queue once Jason has actually looked at
    it, same as a merge proposal being approved or rejected."""
    if (gate := _internal_gate(request)):
        return gate
    row = store.nutrition_review_get(review_id)
    if not row:
        return {"ok": False}
    form = await request.form()
    choice = (form.get("choice") or "").strip()
    if choice == "local":
        store.set_food_baseline(row["name"], row["local"])
    store.nutrition_review_clear(review_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
#  The /recipe app's own backend -- server-to-server, same shared-secret
#  gate as every other /internal/* route (the Rust admin panel already
#  proves this pattern out). Jason's own request (2026-08-24): a real
#  standalone app at /recipe, its own container, with the random-pick and
#  meal-planning features he specifically said he likes -- "in house"
#  rather than just linking out to Tandoor's own raw UI. Tandoor's own
#  session-auth stays in ONE place (tandoor.py); the new container never
#  needs to know how that works.
# ---------------------------------------------------------------------------
def _internal_gate(request: Request):
    if not core.INTERNAL_SECRET or \
            request.headers.get("X-Internal-Secret", "") != core.INTERNAL_SECRET:
        return PlainTextResponse("no", status_code=403)
    return None


@app.get("/internal/recipe-app/recipes")
async def internal_recipe_app_list(request: Request, page: int = 1, per_page: int = 24,
                                   q: str = "", course: str = "", label: str = "",
                                   seed: int = 0, recent: bool = False):
    """Jason's own request (2026-08-24): "a search feature with pills to
    the main page." Filters over the SAME kept_with_ids() rows the plain
    listing already reads -- no new Tandoor call, this is all local JSON
    already on hand. `courses`/`labels` in the response are FACET COUNTS
    over the whole library, computed before `q`/`course`/`label` are
    applied -- a pill has to stay visible (and show how many recipes it
    still covers) once it or another pill is already active, not
    disappear the moment its own filter narrows the list to itself.

    `seed`, Jason's own request (2026-08-26): "on page load can random
    cards of recipes be loaded" -- app.js picks ONE random seed the
    moment the browse screen is first opened and sends the SAME one on
    every page as you scroll (see its own comment), so the order is
    random per VISIT but still stable while paging through it -- a
    seeded shuffle, not a re-shuffle per request, which would repeat or
    skip cards as new pages loaded mid-scroll.

    `recent`, Jason's own request (2026-08-26): first "add a NEW pill to
    show recently imported recipes," then, refined the same day: "can
    they be moved in and out on a bi weekly schedule[?] new ones that
    get added have a 14 day timer then get moved to the appropriate
    pill and out of NEW." A real FILTER now, not just a re-sort -- a
    recipe drops out of NEW on its own, 14 days after it was kept, with
    nothing anywhere that has to notice and move it by hand. Keyed by
    `kept_at` (see store.py's own comment on that column), not
    `created` -- those two are often genuinely different: someone can
    paste a link today and not press Keep for weeks, and what "new"
    means here is "recently entered the library", not "recently
    pasted". Combines with q/course/label like any other filter
    (a search for "chicken" with NEW active shows new chicken recipes),
    rather than being an exclusive alternative to them.
    """
    if (gate := _internal_gate(request)):
        return gate
    rows = store.kept_with_ids()
    # NOT HIDDEN ANY MORE, ADAPTED (Jason, 2026-09-03): "so my new
    # partner knows he can make these items if there are substitutions
    # available." A recipe with beef in it is shown WITH the swap named,
    # because being handed the adapted version is a different experience
    # from being handed a shorter list and never knowing why.
    #
    # Still dropped: anything whose ingredients cannot be read at all.
    # There is no honest swap to offer for a recipe nobody can see
    # inside, so unknown stays unsuitable.
    viewer_limited = diet.restricted(_who(request))
    if viewer_limited:
        rows = [r for r in rows
                if diet.adaptation(r.get("recipe") or {})["status"] != "unknown"]

    # SASHIN'S PILL (2026-09-03). Earned by having a readable ingredient
    # list with no pork and no beef in it -- never by the absence of a
    # list, which proves nothing. Injected into the recipe's own labels
    # so it flows through the existing facet counts and the existing
    # label filter with no new machinery: tapping it works because every
    # other pill already works that way.
    for r in rows:
        rec = r.get("recipe") or {}
        labels = list(rec.get("labels") or [])
        info = diet.adaptation(rec)
        pill = (diet.PILL if info["status"] == "clean"
                else diet.PILL_SWAP if info["status"] == "adaptable" else "")
        if pill and pill not in labels:
            labels.append(pill)
        rec["labels"] = labels
        # Carried on the card so the browse page can say so without a
        # second request per recipe.
        if viewer_limited and info["status"] == "adaptable":
            r["swaps"] = info["swaps"]

    NEW_DAYS = 14
    new_cutoff = int(time.time()) - NEW_DAYS * 86400

    course_counts: dict = {}
    label_counts: dict = {}
    new_count = 0
    for r in rows:
        recipe = r.get("recipe") or {}
        c = (recipe.get("course") or "").strip()
        if c:
            course_counts[c] = course_counts.get(c, 0) + 1
        for lb in recipe.get("labels") or []:
            lb = str(lb).strip()
            if lb:
                label_counts[lb] = label_counts.get(lb, 0) + 1
        if (r.get("kept_at") or 0) >= new_cutoff:
            new_count += 1

    needle = q.strip().lower()
    want_course = course.strip().lower()
    want_label = label.strip().lower()

    def _match(r):
        recipe = r.get("recipe") or {}
        if want_course and (recipe.get("course") or "").strip().lower() != want_course:
            return False
        if want_label and want_label not in [str(x).strip().lower() for x in (recipe.get("labels") or [])]:
            return False
        if needle and needle not in (recipe.get("title") or "").lower():
            return False
        if recent and (r.get("kept_at") or 0) < new_cutoff:
            return False
        return True

    filtered = [r for r in rows if _match(r)]
    if recent:
        # Newest-first within the window -- a real, meaningful order
        # (how new is "new"), not something a random shuffle should
        # ever be allowed to scramble.
        filtered.sort(key=lambda r: r.get("kept_at") or 0, reverse=True)
    elif seed:
        random.Random(seed).shuffle(filtered)
    start = (max(page, 1) - 1) * per_page
    page_rows = filtered[start:start + per_page]
    out = [{
        "id": r.get("tandoor_id"), "title": (r.get("recipe") or {}).get("title") or "Untitled",
        "image": (r.get("recipe") or {}).get("image") or "",
        "course": (r.get("recipe") or {}).get("course") or "",
    } for r in page_rows]
    return {
        "total": len(filtered), "page": page, "per_page": per_page, "recipes": out,
        "courses": sorted(course_counts.items(), key=lambda kv: -kv[1]),
        "labels": [{"name": n, "count": c}
                   for n, c in sorted(label_counts.items(), key=lambda kv: -kv[1])[:12]],
        "new_count": new_count,
    }


@app.get("/internal/recipe-app/recipes/{tandoor_id}")
async def internal_recipe_app_detail(request: Request, tandoor_id: str, serves: int = 0):
    """Same assembly guide() does for the HTML page, returned as JSON --
    NOT a redirect to Tandoor's own page, so the new app can render its
    own cooking-mode view natively."""
    if (gate := _internal_gate(request)):
        return gate
    draft = store.get_by_tandoor_id(tandoor_id)
    if not draft:
        return PlainTextResponse("not found", status_code=404)
    recipe = draft["recipe"]
    # The adapted view. Only a recipe nobody can read the ingredients of
    # is refused now -- everything else arrives with its swaps attached.
    limited = diet.restricted(_who(request))
    swap_info = diet.adaptation(recipe) if limited else None
    if limited and swap_info["status"] == "unknown":
        return PlainTextResponse("not found", status_code=404)
    # THE BRIEFING, before step one: the equipment, what you are working
    # with, and the oven EARLY so it heats while you prep rather than
    # after you discover it in step four. See prep.py.
    brief = prep.briefing(recipe)
    view = importer.build_guide(recipe, draft_id=draft["id"], draft_url=core.ROOT_PATH)
    # Same reasoning as the call below: the pictures are a garnish and
    # the recipe is the meal. Neither should be able to take the page
    # down when Tandoor is not answering.
    try:
        icon_map = await _get_icon_map()
    except Exception as e:  # noqa: BLE001
        icon_map = {}
        print(f"[kitchen] no icon map ({e}); serving the recipe plain",
              flush=True)
    # Tandoor is an ENRICHMENT here, not the source (2026-09-03). Kitchen's
    # own store holds the recipe -- Tandoor is asked only for the parsed
    # ingredient ids that put the right picture next to each line. Found
    # live: Tandoor has never been initialised (its database has zero
    # tables), so this call raised and took the WHOLE recipe page down
    # with it. A missing garnish should not close the kitchen.
    try:
        tandoor_recipe = await tandoor.get_recipe(tandoor_id)
    except Exception as e:  # noqa: BLE001
        tandoor_recipe = None
        print(f"[kitchen] tandoor unavailable, serving without its "
              f"ingredient ids: {e}", flush=True)
    if icon_map:
        for st in view["steps"]:
            st["icons"] = _icons_for_step(st["text"], icon_map)
            for ic in st["icons"]:
                ic["photo_url"] = _item_photo_edit_url(ic.get("kind"), ic.get("id"))

    # INGREDIENT ICONS, ID-FIRST -- NOT TEXT-MATCHED. Jason's own reports
    # (2026-08-26), two in a row: "recipe 30 is missing one ingredient...
    # I would like to be able to go back in and edit those kinds of
    # ingredients," then, once that turned out to be a missing-picture
    # problem rather than a missing-ingredient one, "banana peppers are
    # different than bananas." Both were the SAME root cause: this used
    # to find an ingredient's picture by searching icon_map (every food
    # in the WHOLE household's catalog that has a photo) for any name
    # that appears as a substring of the ingredient's text -- so "banana"
    # (a real, unrelated food with its own picture) matched inside
    # "banana peppers" before "pickled mild banana peppers" (the actual,
    # correct food Tandoor's own parser had already resolved that line
    # to) ever got a look in, because the correct food had no picture of
    # its own yet and so was not even a candidate.
    #
    # Tandoor's own ingredient parser has ALREADY resolved every line to
    # one specific food id -- CONFIRMED LIVE against recipe 30's own data
    # (tandoor.get_recipe('30')): "banana peppers" resolves to food 1500
    # "pickled mild banana peppers", never to food 26 "banana". Using
    # that id directly (foodphotos.get(id)), rather than re-guessing from
    # text, cannot make this class of mistake -- it either has the right
    # food's picture or, same as before, no picture at all yet.
    #
    # INDEX-ALIGNED, not matched by name at all: tandoor._to_tandoor()
    # builds Tandoor's ingredient list FROM this same recipe's display
    # list, in order (see its own docstring), so the two line up 1:1 for
    # any normal Kitchen-kept recipe. Guarded by a length check rather
    # than assumed blindly -- a recipe hand-edited directly in Tandoor
    # could in principle have a different count, and matching the wrong
    # ROW would be worse than matching nothing, so that case falls back
    # to the old text-matching behaviour instead of guessing an index.
    tandoor_ings = [ing for step in ((tandoor_recipe or {}).get("steps") or [])
                    for ing in (step.get("ingredients") or [])]
    aligned = len(tandoor_ings) == len(view["ingredients"])
    for idx, item in enumerate(view["ingredients"]):
        food = (tandoor_ings[idx].get("food") if aligned else None) or {}
        if food.get("id"):
            pic = foodphotos.get(food["id"]) or {}
            item["icon"] = {"name": food.get("name") or "", "kind": "food", "id": food["id"],
                            "url": pic.get("url"),
                            "photo_url": _item_photo_edit_url("food", food["id"])}
        elif icon_map:
            # Fallback only -- see the length-mismatch note above.
            hit = _icons_for_step(item["text"], icon_map)
            item["icon"] = hit[0] if hit else None
            if item["icon"]:
                item["icon"]["photo_url"] = _item_photo_edit_url(
                    item["icon"].get("kind"), item["icon"].get("id"))
        else:
            item["icon"] = None

    # Jason's own request (2026-08-26), Tandoor's own screen shown as the
    # reference: "I wanted all recipes to have this layout[,] the food
    # items[,] neatly stacked[,] and converted with the ability to adjust
    # the portions." Built from Tandoor's OWN structured ingredient data
    # (tandoor_ings, the same list already fetched above for the icon
    # matching) -- NOT from view["ingredients"]'s pre-formatted text,
    # which has no separate amount/unit/name to put in aligned columns.
    # scale.render() is the SAME arithmetic guide.html's own portion
    # buttons use (Fraction math, never a model -- see scale.py's own
    # docstring for why), just applied to a real number instead of
    # parsed back out of a sentence.
    # SERVINGS COME FROM TANDOOR, AND TANDOOR'S LIBRARY IS EMPTY.
    #
    # CAUGHT LIVE 2026-09-06 on recipe 111 (Broccoli Cheddar Soup): the
    # page said "0 servings" while its own yields field said "4
    # serving(s)". base_servings read only from the Tandoor recipe, and
    # Tandoor holds none of the 249 -- so it was 0 for every recipe, which
    # made `factor` fall back to 1 and left the portion buttons unable to
    # scale anything at all.
    #
    # Kitchen has always known the answer: `yields` is its own field, from
    # its own store. Same shape of fault as the icon map had -- reaching
    # to Tandoor for something already in hand -- so the same fix: Tandoor
    # when it has an opinion, Kitchen's own text when it does not.
    base_servings = (tandoor_recipe or {}).get("servings") or 0
    if not base_servings:
        base_servings = portions.servings_in(recipe.get("yields") or "")
    servings_now = serves if serves > 0 else base_servings
    factor = (Fraction(servings_now, base_servings)
             if base_servings and servings_now else Fraction(1))

    structured_ingredients = []
    for ing in tandoor_ings:
        if ing.get("is_header"):
            structured_ingredients.append({"header": ing.get("note") or ing.get("original_text") or ""})
            continue
        food = ing.get("food") or {}
        unit_name = (ing.get("unit") or {}).get("name") or ""
        amount = ing.get("amount") or 0
        amount_text = ""
        if amount and not ing.get("no_amount"):
            try:
                scaled = Fraction(amount).limit_denominator(64) * factor
                amount_text = scale.render(scaled, countable=not bool(unit_name))
            except (ValueError, ZeroDivisionError, OverflowError):
                amount_text = str(amount)
        pic = foodphotos.get(food["id"]) if food.get("id") else None
        structured_ingredients.append({
            "amount": amount_text, "unit": unit_name, "name": food.get("name") or "",
            "note": ing.get("note") or "",
            "icon": ({"kind": "food", "id": food["id"], "url": (pic or {}).get("url"),
                     "photo_url": _item_photo_edit_url("food", food["id"])}
                    if food.get("id") else None),
        })

    # Jason's own request (2026-08-25): "when a timer needs to be set...
    # setting a temp to 400 degrees... illustrations of measurements in
    # the steps... just popup the image of this." Code-drawn, same as
    # svg_for() (already computed above via build_guide -- this is the
    # per-MENTION version, see diagram.badges_for's own docstring for
    # why it's a separate pass from that one).
    for st in view["steps"]:
        st["badges"] = diagram.badges_for(st["text"])
    tool_names = tandoor.tools_from(tandoor_recipe or {})
    tools_view = []
    for t in tool_names:
        hit = (icon_map or {}).get((t or "").strip().lower(), {})
        tools_view.append({"name": t, "url": hit.get("url"), "id": hit.get("id"),
                           "photo_url": _item_photo_edit_url("tool", hit.get("id"))})

    # Jason's own request (2026-08-24): "a usda breakdown per one serving
    # ... and a serving for the whole recipe as the recipe calls" -- same
    # recipe_nutrition.daily_values() guide.html's own %DV chart already
    # uses, called twice: once scaled per serving (the normal case), once
    # with servings=1 so totals_for()'s whole-recipe sum comes back
    # unscaled. Reused rather than re-summed in JS so this can never drift
    # from what guide.html shows for the same recipe.
    nutrition_full = recipe.get("nutrition_full")
    nutrition_servings = recipe.get("nutrition_servings") or 1
    nutrition_per_serving = (recipe_nutrition.daily_values(nutrition_full, nutrition_servings)
                             + recipe_nutrition.extra_amounts(nutrition_full, nutrition_servings)
                             if nutrition_full else [])
    # THE POT SCALES, THE PLATE DOES NOT.
    #
    # daily_values(full, n) DIVIDES the recipe's totals by n. The whole-
    # recipe view wants them multiplied by `factor` instead -- at 8
    # servings from a base of 4 there is twice as much in the pot -- so it
    # is passed 1/factor, which divides by a half and doubles.
    #
    # nutrition_per_serving above is deliberately left alone. Scaling a
    # recipe makes more food and more servings in the same proportion, so
    # what is on one plate is unchanged. Applying `factor` there too would
    # report that someone's sodium halved when they made more soup, which
    # is a worse failure than the one being fixed.
    whole_divisor = float(Fraction(1) / factor) if factor else 1.0
    nutrition_whole = (recipe_nutrition.daily_values(nutrition_full, whole_divisor)
                       + recipe_nutrition.extra_amounts(nutrition_full, whole_divisor)
                       if nutrition_full else [])

    # CAUGHT LIVE (2026-08-24), testing the nutrition breakdown above:
    # build_guide()'s own return has no "title"/"yields" keys at all --
    # guide.html gets those off the separate `recipe` context var main.py
    # passes it alongside `view`, not off `view` itself (see build_guide's
    # own return dict: "recipe", "steps", "ingredients", "serves_now" --
    # never "title"). This route had been reading `view.get("title")`
    # since /recipe was first built, which is always None -- the SPA's
    # own recipe-detail screen has been showing no title since day one.
    return {
        # Jason's own request (2026-08-26): "use the original source as a
        # link to get back to the recipe... so that if someone doesn't
        # want to use my recipe they can see the original." Same field
        # guide.html already reads (recipe.source) -- just never handed
        # to /recipe's own JSON before now.
        "source": recipe.get("source") or "",
        # THE BRIEFING before step one -- equipment, what you are working
        # with, and the oven early so it heats while you prep. Never
        # invents a temperature: a recipe that gives none gets no preheat
        # line rather than a plausible guess (see prep.py).
        "briefing": brief,
        # Present only for a viewer who is restricted: the swaps that
        # make this recipe theirs, with the adjustment each one needs.
        "swaps": (swap_info or {}).get("swaps") or [],
        "diet_status": (swap_info or {}).get("status") or "",
        "id": tandoor_id, "title": recipe.get("title"), "image": recipe.get("image"),
        "yields": recipe.get("yields"), "prep": recipe.get("prep"),
        "cook": recipe.get("cook"), "total": recipe.get("total"),
        "ingredients": view.get("ingredients"), "steps": view.get("steps"),
        # Jason's own request (2026-08-26): the neat, column-aligned
        # ingredient layout (quantity | unit | name) with a real portion
        # picker -- see this route's own comment above for how it's built.
        "structured_ingredients": structured_ingredients,
        "servings_base": base_servings, "servings_now": servings_now,
        # Jason's own request (2026-08-26): "I want to add these features
        # back into /recipe" -- the "already written down, answers back
        # instantly, no model" quick-pick buttons guide.html's own
        # "Missing something?" card shows (subs.scan(), a dictionary
        # lookup, not a suggestion the model came up with).
        "quick_swaps": subs.scan([item["text"] for item in view["ingredients"]]),
        "tools": tools_view, "nutrition": recipe.get("nutrition_full"),
        "nutrition_servings": recipe.get("nutrition_servings"),
        "nutrition_per_serving": nutrition_per_serving,
        "nutrition_whole": nutrition_whole,
        # Same flag guide.html already uses to show/hide its own Listen
        # button -- Jason's own request (2026-08-25), a Play button "in
        # /Recipe" too. False (button hidden) until a voice is actually
        # configured -- see tts.py's own "no voice name, no speech" rule.
        "listen_enabled": tts.enabled(),
        # Jason's own request (2026-08-25): "I would still like to go
        # back and edit saved pictures already made... through the
        # actual /recipe logged in as me," then, twice more, "only as
        # person1@example.com can I do this" / "allow person1@example.com to
        # directly add images." recipe-app forwards the real visitor's
        # own X-Auth-Email (see its own _kitchen()/_who()), not just its
        # shared internal secret -- that only proves recipe-app itself
        # is a legitimate caller, never who is actually looking at the
        # page. _can_edit_images() (his one specific address, not the
        # wider owner list _is_owner() would accept) so app.js only
        # shows a picture-editing control when it would actually be
        # allowed to use it.
        "owner": _can_edit_images(request),
    }


def _recipe_app_image_gate(request: Request):
    """None if this request is allowed to search/keep/draw/generate a
    picture from /recipe; otherwise the response to hand straight back.
    Two checks, not one: _internal_gate() proves recipe-app itself is a
    legitimate caller (the shared secret), _can_edit_images() proves the
    real visitor behind it is the one specific person allowed to do
    this -- neither is a substitute for the other."""
    if (gate := _internal_gate(request)):
        return gate
    if not _can_edit_images(request):
        return {"ok": False, "error": "Only Jason can edit pictures."}
    return None


@app.get("/internal/recipe-app/item-photo-search/{kind}/{item_id}")
async def internal_recipe_app_item_photo_search(request: Request, kind: str, item_id: str, q: str = ""):
    """The same search item_photos.html's own search box already does
    (foodimages.search_images), as JSON instead of a rendered page --
    Jason's own request (2026-08-25): "allow person1@example.com to directly
    add images... like on /kitchen in /recipe," his choice of the
    embedded-inline option over a link out to Kitchen's own page.

    DEFAULTS q TO THE ITEM'S OWN NAME. Jason's own report (2026-08-26):
    "there is no way to add pictures... I want how it's wired up in
    /kitchen/food for that one item." The modal's very first open (no q
    typed yet) used to search for nothing and show an empty grid -- not
    actually broken, just indistinguishable from broken with no results
    and no obvious next step. Kitchen's own item_photos.html gets away
    with the identical "empty until you search" behavior because the
    text box is right there, pre-filled and begging to be submitted; a
    same-page item on /recipe deserved to just show something the
    moment it opens, so this fills q in itself when nothing was passed.
    """
    if (gate := _recipe_app_image_gate(request)):
        return gate
    if kind not in _ITEM_KINDS:
        return {"ok": False, "error": "Not a real kind."}
    item = await _item_lookup(kind, item_id)
    if not item:
        return {"ok": False, "error": "That is not here any more."}
    search_q = q or item.get("name") or ""
    results = []
    error = ""
    if search_q:
        try:
            results = await foodimages.search_images(search_q)
        except foodimages.ImageSearchError as e:
            error = str(e)
    room_ok, room_why = await art.room()
    return {"ok": True, "results": results, "error": error,
           "current": _item_image(kind, item_id, item),
           "item_name": item.get("name") or "", "room_ok": room_ok, "room_why": room_why}


@app.post("/internal/recipe-app/item-photo-keep/{kind}/{item_id}")
async def internal_recipe_app_item_photo_keep(request: Request, kind: str, item_id: str,
                                              url: str = Form(...), title: str = Form("")):
    if (gate := _recipe_app_image_gate(request)):
        return gate
    if kind not in _ITEM_KINDS:
        return {"ok": False, "error": "Not a real kind."}
    url = url.strip()
    title = title.strip()
    if not url:
        return {"ok": False, "error": "No picture chosen."}
    item = await _item_lookup(kind, item_id)
    if not item:
        return {"ok": False, "error": "That is not here any more."}
    cfg = _ITEM_KINDS[kind]
    try:
        data = await foodimages.download_image(url)
        name = cfg["store"].save(item_id, data)
    except (foodimages.ImageSearchError, cfg["error"]) as e:
        return {"ok": False, "error": str(e)}
    # Cache-busted the same way _item_photo_keep()'s own HTML route
    # already is -- see that function's own comment for why.
    served = f"{core.ROOT_PATH}/{cfg['photo_route']}/{item_id}/{name}?v={int(time.time())}"
    try:
        await _item_save_image(kind, item_id, item, served, "internet", title)
    except mealie.MealieError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "url": served}


@app.post("/internal/recipe-app/item-photo-draw/{kind}/{item_id}")
async def internal_recipe_app_item_photo_draw(request: Request, kind: str, item_id: str):
    if (gate := _recipe_app_image_gate(request)):
        return gate
    if kind not in _ITEM_KINDS:
        return {"ok": False, "error": "Not a real kind."}
    item = await _item_lookup(kind, item_id)
    if not item:
        return {"ok": False, "error": "That is not here any more."}
    ok, why = await art.room()
    if not ok:
        await vram.free()
        ok, why = await art.room()
        if not ok:
            return {"ok": False, "error": f"Cannot draw right now: {why}"}
    cfg = _ITEM_KINDS[kind]
    try:
        prompt = foodimages.prompt_for_item(item.get("name") or "", cfg["word"])
        png = await art.draw(prompt, negative=foodimages.ITEM_NEGATIVE)
    finally:
        await art.release()
    if not png:
        return {"ok": False, "error": "Could not draw a picture right now."}
    try:
        name = cfg["store"].save(item_id, png, drawn=True)
    except cfg["error"] as e:
        return {"ok": False, "error": str(e)}
    served = f"{core.ROOT_PATH}/{cfg['photo_route']}/{item_id}/{name}?v={int(time.time())}"
    try:
        await _item_save_image(kind, item_id, item, served, "ai-drawn", "AI-drawn, not a real photo")
    except mealie.MealieError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "url": served}


@app.post("/internal/recipe-app/draw-missing/{tandoor_id}")
async def internal_recipe_app_draw_missing(request: Request, tandoor_id: str):
    """Step-by-step photo generation for one recipe, straight from
    /recipe -- Jason's own request (2026-08-25): "like in /kitchen when
    I can generate a photo for step by step... only as person1@example.com can
    I do this for a recipe in /recipe." Same _draw_missing() the admin
    panel's own "Draw pictures" button and guide.html's own button both
    already call. Frees the GPU itself first (vram.free()) -- unlike the
    admin route, there's no separate "free the GPU" button in front of
    this one to click first."""
    if (gate := _recipe_app_image_gate(request)):
        return gate
    draft = store.get_by_tandoor_id(tandoor_id)
    if not draft:
        return {"ok": False, "error": "That recipe is not here any more.", "made": 0}
    ok, why = await art.room()
    if not ok:
        await vram.free()
    made, why = await _draw_missing(draft["id"])
    return {"ok": made > 0, "made": made, "error": why or None}


@app.get("/internal/recipe-app/install-status")
async def internal_recipe_app_install_status(request: Request):
    """Whether THIS person (not this browser) has already installed
    /recipe -- Jason's own request (2026-08-26): "if the desktop/mobile
    app is downloaded check for it and don't run again for that
    person." See store.py's `installs` table for why this has to be
    per-person rather than left to localStorage alone."""
    if (gate := _internal_gate(request)):
        return gate
    return {"installed": store.is_installed(_who(request))}


@app.post("/internal/recipe-app/install-mark")
async def internal_recipe_app_install_mark(request: Request):
    """Called once app.js notices it is actually RUNNING installed
    (standalone display mode) -- not on the mere click of "Install",
    which can still be cancelled. Idempotent: marking twice costs
    nothing (store.mark_installed() is INSERT ... DO NOTHING)."""
    if (gate := _internal_gate(request)):
        return gate
    store.mark_installed(_who(request))
    return {"ok": True}


@app.get("/internal/recipe-app/random")
async def internal_recipe_app_random(request: Request):
    if (gate := _internal_gate(request)):
        return gate
    # For a restricted viewer the dice are thrown over THEIR library, not
    # over everything -- drawing from the whole shelf and rejecting after
    # the fact leaks which recipes exist by how often it comes up empty.
    if diet.restricted(_who(request)):
        import random as _rnd
        ok = [r for r in store.kept_with_ids()
              if diet.allowed(r.get("recipe") or {})]
        if not ok:
            return {"id": None}
        pick = _rnd.choice(ok)
        return {"id": pick.get("tandoor_id") or pick.get("id"),
                "title": (pick.get("recipe") or {}).get("title")}
    recipe = await tandoor.random_recipe()
    if not recipe:
        return {"id": None}
    return {"id": recipe.get("id"), "title": recipe.get("name")}


@app.get("/internal/recipe-app/listen/{tandoor_id}/{n}")
async def internal_recipe_app_listen(request: Request, tandoor_id: str, n: int):
    """The same cached step audio as Kitchen's own /r/{id}/step/{n}/listen,
    reachable from /recipe -- Jason's own request (2026-08-25): a Play
    button "in /Recipe" too, not just Kitchen's own guide page. Keyed by
    tandoor_id, not draft_id, since that's the only id /recipe ever has."""
    if (gate := _internal_gate(request)):
        return gate
    draft = store.get_by_tandoor_id(tandoor_id)
    if not draft:
        return PlainTextResponse("That recipe is not here any more.", status_code=404)
    steps = draft["recipe"].get("steps", [])
    if n < 1 or n > len(steps):
        return PlainTextResponse("That step is not there.", status_code=404)
    try:
        audio, content_type = await tts.cached_speak(draft["id"], f"step-{n}", steps[n - 1])
    except tts.TTSError as e:
        return PlainTextResponse(str(e), status_code=503)
    return Response(content=audio, media_type=content_type)


@app.post("/internal/recipe-app/shop-add/{tandoor_id}")
async def internal_recipe_app_shop_add(request: Request, tandoor_id: str):
    """Put ONE ingredient on the shared household shopping list -- Jason's
    own request (2026-08-26): "can everyone have a checkbox for the food
    item[,] or add it to a shopping cart so they can make sure they have
    the ingredients." Same store.shopping_add() Kitchen's own "Add to
    shopping list" button already calls (see shop_from_recipe()), just
    ONE line at a time instead of the whole recipe, and reachable by
    tandoor_id since that's the only id /recipe ever has. Same dedupe
    (shopping_add() itself skips a line already on the list) and same
    gate as every other write /recipe can make -- no owner restriction;
    this is a household-shared list, "everyone" as asked."""
    if (gate := _internal_gate(request)):
        return gate
    form = await request.form()
    text = (form.get("text") or "").strip()
    if not text:
        return {"ok": False, "error": "Nothing to add."}
    draft = store.get_by_tandoor_id(tandoor_id)
    if not draft:
        return {"ok": False, "error": "That recipe is not here any more."}
    added = store.shopping_add([text], source=draft["title"], draft_id=draft["id"],
                               who=_who(request))
    return {"ok": True, "added": added}


@app.get("/internal/recipe-app/plan")
async def internal_recipe_app_plan(request: Request, start: str, end: str):
    if (gate := _internal_gate(request)):
        return gate
    entries = await tandoor.meal_plan_range(start, end)
    return [{
        "id": e.get("id"), "date": (e.get("from_date") or "")[:10],
        "meal_type": e.get("meal_type_name") or "",
        "recipe_id": (e.get("recipe") or {}).get("id"),
        "title": e.get("title") or (e.get("recipe") or {}).get("name") or "Untitled",
    } for e in entries]


@app.post("/internal/recipe-app/plan")
async def internal_recipe_app_plan_add(request: Request):
    if (gate := _internal_gate(request)):
        return gate
    form = await request.form()
    date = (form.get("date") or "").strip()
    recipe_id = (form.get("recipe_id") or "").strip()
    meal_type = (form.get("meal_type") or "dinner").strip()
    if not date or not recipe_id:
        return {"ok": False}
    entry_id = await tandoor.meal_plan_add(date, recipe_id, meal_type)
    return {"ok": bool(entry_id), "id": entry_id}


@app.post("/internal/recipe-app/plan/{entry_id}/remove")
async def internal_recipe_app_plan_remove(request: Request, entry_id: int):
    if (gate := _internal_gate(request)):
        return gate
    ok = await tandoor.meal_plan_remove(entry_id)
    return {"ok": ok}


@app.get("/internal/recipe-app/pantry")
async def internal_recipe_app_pantry(request: Request, have: str = ""):
    """What you can nearly make from what you already have -- Jason's own
    words (2026-08-24): "mealie did that I still want that feature too."
    Not actually a Mealie feature at all, though -- it's basket.py's own
    could_make(), pure code, no model, already working (see home.html's
    own pantry box). Just exposed here too, for the standalone /recipe
    app, so it lives in that app without Jason ever leaving it."""
    if (gate := _internal_gate(request)):
        return gate
    pantry_have = [t.strip() for t in re.split(r"[\n,]", have) if t.strip()]
    if not pantry_have:
        return []
    matches = basket.could_make(pantry_have, store.all_ingredients(limit=5000))
    out = []
    for m in matches:
        draft = store.get(m["id"])
        tandoor_id = draft.get("tandoor_id") if draft else None
        if not tandoor_id:
            continue
        out.append({**m, "id": tandoor_id})
    return out


@app.post("/internal/recipe-app/swap")
async def internal_recipe_app_swap(request: Request):
    """Ingredient substitutions, right on /recipe -- Jason's own request
    (2026-08-25): "with the models i'd like to also suggest substitutions
    with tools and food items." subs.suggest() already does exactly this
    (table first, the model for anything not written down) -- this is
    the same feature Kitchen's own guide.html already has, reachable by
    Tandoor id instead of a draft id, since /recipe only ever knows
    kept recipes' Tandoor ids."""
    if (gate := _internal_gate(request)):
        return gate
    form = await request.form()
    ingredient = (form.get("ingredient") or "").strip()
    tandoor_id = (form.get("tandoor_id") or "").strip()
    why = (form.get("why") or "").strip()
    if not ingredient:
        return JSONResponse({"error": "Which ingredient?"}, status_code=400)
    draft = store.get_by_tandoor_id(tandoor_id) if tandoor_id else None
    dish = (draft["recipe"].get("title") if draft else "") or ""
    try:
        return await subs.suggest(ingredient, dish=dish, why=why)
    except llm.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/internal/recipe-app/swap-equipment")
async def internal_recipe_app_swap_equipment(request: Request):
    """Same as swap() above, for a tool -- subs.suggest_equipment()."""
    if (gate := _internal_gate(request)):
        return gate
    form = await request.form()
    item = (form.get("item") or "").strip()
    tandoor_id = (form.get("tandoor_id") or "").strip()
    if not item:
        return JSONResponse({"error": "Which tool?"}, status_code=400)
    draft = store.get_by_tandoor_id(tandoor_id) if tandoor_id else None
    dish = (draft["recipe"].get("title") if draft else "") or ""
    try:
        return await subs.suggest_equipment(item, dish=dish)
    except llm.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.post("/internal/recipe-app/swap-amount")
async def internal_recipe_app_swap_amount(request: Request):
    """"I have {have}, the recipe wants {ingredient} -- how much do I
    actually need." Jason's own request (2026-08-26): "I want to add
    these features back into /recipe" -- Kitchen's own guide.html has
    three swap forms (quick pre-known substitutes, a free-text "what
    else could I use", and this one, "I already have something
    specific"); /recipe only ever got the first two ported. Different
    question from /swap: that one suggests WHAT to use given a bare
    ingredient name; this one converts a substitute ALREADY NAMED to an
    exact amount, against the recipe's own line (with its quantity).
    See subs.convert() -- no table fallback exists for this one, so a
    model failure is a real error, not silently empty."""
    if (gate := _internal_gate(request)):
        return gate
    form = await request.form()
    ingredient = (form.get("ingredient") or "").strip()
    have = (form.get("have") or "").strip()
    tandoor_id = (form.get("tandoor_id") or "").strip()
    if not ingredient or not have:
        return JSONResponse({"error": "Which ingredient, and what do you have instead?"},
                            status_code=400)
    draft = store.get_by_tandoor_id(tandoor_id) if tandoor_id else None
    dish = (draft["recipe"].get("title") if draft else "") or ""
    try:
        return await subs.convert(ingredient, have, dish=dish)
    except llm.LLMError as e:
        return JSONResponse({"error": str(e)}, status_code=502)


@app.get("/internal/recipe-app/parser-busy")
async def internal_recipe_app_parser_busy(request: Request):
    """Non-blocking check, not an answer that has to wait for the import
    itself to finish -- Jason's own request (2026-08-25): "can my phi be
    used if another one is not chatting and everything is idle to parse
    the data from the site. otherwise let the user know it's waiting to
    be imported." /recipe's own import screen checks this FIRST, before
    submitting, so it can say "hang on, something else is using it"
    up front rather than just going quiet -- vram.parsing() itself
    already makes the actual wait safe (see vram.py's own new lock),
    this is only the polite heads-up."""
    if (gate := _internal_gate(request)):
        return gate
    return {"busy": vram.busy()}


@app.post("/internal/recipe-app/import")
async def internal_recipe_app_import(request: Request):
    """Paste-a-link import, reachable from /recipe itself -- Jason's own
    request (2026-08-24): "want to add the import part to the /recipe as
    well." Same underlying pipeline as the browser's own /import form
    (do_import below); this returns the parsed draft as JSON instead of
    redirecting to Kitchen's own guide page, so /recipe can show its own
    review screen without sending anyone out of the app. Deeper editing
    (step pictures, tools, nutrition review) still lives on Kitchen's own
    guide page -- this covers the common case, paste a link, check it
    looks right, save it -- not a full port of guide.html."""
    if (gate := _internal_gate(request)):
        return gate
    form = await request.form()
    url = (form.get("url") or "").strip()
    text = (form.get("text") or "").strip()
    if not url and not text:
        return JSONResponse({"error": "Give me a link, or paste the recipe text."}, status_code=400)
    try:
        recipe = await (importer.from_url(url) if url else importer.from_text(text))
    except core.Blocked as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except llm.LLMError as e:
        return JSONResponse({"error": f"{e} That page had no machine-readable "
                                      "recipe on it, so reading it needed the model."},
                            status_code=400)
    draft_id = store.save(recipe, _who(request))
    asyncio.create_task(_label_later(draft_id))
    return {
        "draft_id": draft_id,
        "title": recipe.get("title") or "Untitled",
        "image": recipe.get("image") or "",
        "yields": recipe.get("yields") or "",
        "ingredients": recipe.get("ingredients") or [],
        "steps": recipe.get("steps") or [],
    }


@app.post("/internal/recipe-app/import/{draft_id}/keep")
async def internal_recipe_app_import_keep(request: Request, draft_id: str):
    """Same one true Keep path every other "put it in the library" button
    in this app already goes through -- see _keep_one's own docstring."""
    if (gate := _internal_gate(request)):
        return gate
    if not store.get(draft_id):
        return JSONResponse({"error": "That recipe is not here any more."}, status_code=404)
    ok, err = await _keep_one(draft_id, _who(request))
    if not ok:
        return JSONResponse({"error": err or "The library would not take it."}, status_code=502)
    draft = store.get(draft_id)
    return {"ok": True, "tandoor_id": (draft or {}).get("tandoor_id")}


# ---------------------------------------------------------------------------
#  Importing
# ---------------------------------------------------------------------------
@app.post("/import")
async def do_import(request: Request,
                    url: str = Form(""),
                    text: str = Form(""),
                    serves: str = Form("")):
    if (gate := _write_gate(request)):
        return gate
    url, text = url.strip(), text.strip()
    if not url and not text:
        return _oops(request, "Give me a link, or paste the recipe text.")

    started = time.monotonic()
    try:
        if url:
            recipe = await importer.from_url(url)
        else:
            recipe = await importer.from_text(text)
    except core.Blocked as e:
        return _oops(request, str(e))
    except llm.LLMError as e:
        return _oops(request,
                     f"{e}\n\nThat page had no machine-readable recipe on it, "
                     "so reading it needed the model.")

    recipe["seconds"] = round(time.monotonic() - started, 1)
    draft_id = store.save(recipe, _who(request))

    # LABELLED AFTER THE REDIRECT, not before it.
    #
    # A page that publishes its recipe data imports with no model call at all,
    # in well under a second, and that is the property worth protecting -- it
    # is what makes the importer feel instant and keeps it working while the
    # GPU is busy on the phone line. Labelling is one short model call; doing
    # it inline would put 5-30 seconds in front of every import to produce
    # four words you do not need until you next go browsing.
    #
    # So it happens in the background and the pill appears a moment later.
    asyncio.create_task(_label_later(draft_id))

    to = f"{core.ROOT_PATH}/r/{draft_id}"
    if serves.strip().isdigit():
        to += f"?serves={int(serves)}"
    return RedirectResponse(to, status_code=303)


async def _label_later(draft_id: str):
    """Put labels on a draft once the person has already got their page."""
    try:
        draft = store.get(draft_id)
        if not draft:
            return
        recipe = draft["recipe"]
        found = await labels.suggest(recipe, store.all_labels())
        if found:
            recipe["labels"] = found
        # Course and where-it-is-from, unless a person has already said. Once
        # somebody sets those by hand they are theirs -- see taxonomy.locked.
        if not taxonomy.locked(recipe):
            read = await taxonomy.read(recipe)
            recipe["course"] = read["course"]
            recipe["cuisine"] = read["cuisine"]
        store.update_recipe(draft_id, recipe)
    except Exception as e:
        # Swallowed on purpose. Nothing downstream needs labels, and a
        # background task that raises takes its traceback to a log nobody is
        # reading. One line, so it is findable if pills stop appearing.
        print(f"kitchen: could not label {draft_id}: {e.__class__.__name__}: {e}")


async def _extras_later(draft_id: str):
    """Fill in what might go well with this, once the guide has already
    loaded. Same shape as _label_later -- see the note there."""
    try:
        draft = store.get(draft_id)
        if not draft:
            return
        recipe = draft["recipe"]
        if recipe.get("extras") is not None:
            return   # someone else's request for this page won the race
        # [] IS THE CACHED ANSWER, same as an empty labels list would be --
        # not "still don't know". Storing it is what stops a recipe the model
        # has nothing to add to from being asked again on every future view.
        recipe["extras"] = await extras.suggest(recipe)
        store.update_recipe(draft_id, recipe)
    except Exception as e:
        print(f"kitchen: could not suggest extras for {draft_id}: "
              f"{e.__class__.__name__}: {e}")


# ---------------------------------------------------------------------------
#  The guide
# ---------------------------------------------------------------------------
@app.get("/r/{draft_id}", response_class=HTMLResponse)
async def guide(request: Request, draft_id: str, serves: str = "",
                drew: str = "", nodraw: str = ""):
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here. Drafts you never "
                              f"kept are swept after {store.DRAFT_DAYS} days.", 404)

    want = int(serves) if serves.isdigit() and 0 < int(serves) <= 100 else None
    view = importer.build_guide(draft["recipe"], want,
                                draft_id=draft_id, draft_url=core.ROOT_PATH)

    # Small ingredient/tool icons next to what a step actually mentions --
    # Jason's own request, alongside the bigger illustration each step
    # already has. Same icon_map _get_icon_map() builds for mealie-skin's
    # injected script, just matched here in Python instead of in the
    # browser, since this page already renders server-side.
    icon_map = await _get_icon_map()
    if icon_map:
        for st in view["steps"]:
            st["icons"] = _icons_for_step(st["text"], icon_map)
        # A picture on the ingredient list itself too, not just the tools
        # list and the per-step rows -- Jason's own request, same picture
        # size everywhere on this page now (his own correction: no more
        # small-icon/big-thumbnail split between foods and tools). Best
        # single match per line, same matcher as a step's own icon row.
        for item in view["ingredients"]:
            hit = _icons_for_step(item["text"], icon_map)
            item["icon"] = hit[0] if hit else None

    # A standalone "Tools" list, same idea as Ingredients right above it on
    # the page -- Jason's own request, "add checkboxes to each item...
    # including tools". FROM MEALIE, NOT THE DRAFT: a tools list added or
    # edited straight in Mealie's own UI never writes back to Kitchen's
    # local copy, so reading draft["recipe"]["tools"] here showed nothing
    # for exactly the recipes most likely to actually have one -- checked
    # live against a real recipe with 5 real tools in Mealie and 0 in the
    # local draft. Falls back to the local copy for a recipe never kept
    # (no mealie_slug yet). Each name matched against the SAME icon_map by
    # exact lowercase name -- a real picture when this tool has one in
    # /kitchen/tools, otherwise just the name.
    if draft.get("tandoor_id"):
        tandoor_recipe = await tandoor.get_recipe(draft["tandoor_id"])
        tool_names = tandoor.tools_from(tandoor_recipe or {})
    elif draft.get("mealie_slug"):
        mealie_recipe = await mealie.get_recipe(draft["mealie_slug"])
        tool_names = [t.get("name") for t in (mealie_recipe or {}).get("tools") or []]
    else:
        tool_names = draft["recipe"].get("tools") or []
    tools_view = []
    for t in tool_names:
        if not t:
            continue
        hit = (icon_map or {}).get((t or "").strip().lower(), {})
        tools_view.append({"name": t, "url": hit.get("url"), "id": hit.get("id")})

    # Whether YOUR made-and-tested pill is already on, so the button can say
    # what pressing it will do rather than the same thing either way.
    made_mine = labels.made_by(_who(request)) in (draft["recipe"].get("labels") or [])

    # ASKED ONCE, ON THE FIRST VISIT A RECIPE ACTUALLY GETS -- not on import.
    # A bulk import can bring in sixty drafts that nobody ever opens; asking
    # the model for extras on every one of those would be sixty model calls
    # for suggestions nobody reads. `is None` is "never asked" -- `[]` is a
    # real, cached answer ("nothing worth adding") and must not ask again.
    if draft["recipe"].get("extras") is None:
        asyncio.create_task(_extras_later(draft_id))

    # %DV chart against a 2000-calorie diet -- see recipe_nutrition.py for
    # the FDA reference values this is built from. totals_for() (run at
    # keep()-time or by nutrition_fix.py) sums the WHOLE recipe; dividing by
    # its OWN recorded serving count is what makes this per serving
    # regardless of how many servings are asked for on this particular view
    # -- scaling the recipe up or down does not change what one serving has
    # in it.
    nutrition_full = draft["recipe"].get("nutrition_full")
    nutrition_servings = draft["recipe"].get("nutrition_servings", 1)
    daily_values = (recipe_nutrition.daily_values(nutrition_full, nutrition_servings)
                    if nutrition_full else [])
    nutrition_extra = (recipe_nutrition.extra_amounts(nutrition_full, nutrition_servings)
                       if nutrition_full else [])
    # THE ONE NUMBER THAT ACTUALLY MOVES WHEN THE PORTION BUTTONS ARE
    # PRESSED. daily_values() above is per serving on purpose and does not
    # change with the portion count -- see its own docstring. This is the
    # different question "if I am making this many right now, what does
    # that add up to", which does change with it.
    #
    # NOT view["serves_now"] -- that is build_guide()'s "as written" figure
    # (see scale.factor_for(): its second return value is always the
    # recipe's own native yield, never `want`, on purpose -- guide.html's
    # "Serves N as written" line depends on that staying fixed). `want` is
    # what was actually asked for on THIS view; falls back to the native
    # count when nothing was picked, same as the portion buttons' own
    # default.
    nutrition_batch = (recipe_nutrition.batch_totals(
        nutrition_full, nutrition_servings, want or view.get("serves_now"))
        if nutrition_full else [])

    # FALLBACK ONLY -- see crawl.py's _nutrition_from_ld() docstring.
    # nutrition_full above is this app's own ingredient-level USDA estimate;
    # it silently comes back empty when too few ingredients matched a known
    # food, which is common on an odd or heavily-branded ingredient list.
    # nutrition_stated is whatever the SOURCE PAGE published about itself,
    # already per serving -- shares recipe_nutrition.py's own key names on
    # purpose, so the same daily_values() math applies with servings=1
    # (it's already per serving, nothing left to divide by). Only computed
    # at all when the real estimate has nothing to show.
    nutrition_stated = draft["recipe"].get("nutrition_stated") or {}
    daily_values_stated = (recipe_nutrition.daily_values(nutrition_stated, 1)
                           if nutrition_stated and not daily_values else [])
    nutrition_stated_extra = (recipe_nutrition.extra_amounts(nutrition_stated, 1)
                              if nutrition_stated and not daily_values else [])

    return _page(request, "guide.html", draft=draft, id=draft_id,
                 made_mine=made_mine,
                 added_by=labels.added_by(draft.get("who_email", "")),
                 added_email=draft.get("who_email", ""),
                 kept_by_name=labels.friendly_name(draft.get("kept_by", "")),
                 votes=store.votes_for(draft_id, _who(request)),
                 says=store.comments_for(draft_id),
                 drew=drew if drew.isdigit() else "",
                 nodraw=nodraw[:200],
                 can_draw=sum(1 for st in view["steps"]
                              if not st["photo"] and art.worth_drawing(st["text"])),
                 extras_disclaimer=extras.DISCLAIMER,
                 now_date=_dt.date.today().isoformat(),
                 daily_values=daily_values, nutrition_extra=nutrition_extra,
                 nutrition_batch=nutrition_batch,
                 daily_values_stated=daily_values_stated,
                 nutrition_stated_extra=nutrition_stated_extra,
                 nutrition_stated_source=urlparse(draft["recipe"].get("source") or "").netloc,
                 courses=taxonomy.COURSES, cuisines=taxonomy.CUISINES,
                 listen_enabled=tts.enabled(), tools=tools_view, **view)


@app.post("/r/{draft_id}/share")
async def share_recipe(request: Request, draft_id: str):
    """Mint a no-login link to this one recipe -- Jason's own request
    (2026-08-25): "share the links to a certain recipe to a random person
    without a login with the share button... 2 day temp access." Same
    _write_gate() as every other route that changes something here: not
    owner-only, but not open to someone whose account was narrowed to
    /recipe alone either, since that account has no way to reach this
    route in the first place (it lives under /kitchen).

    Needs a real Tandoor id -- a draft nobody has Kept yet has nothing a
    stranger could be shown that would still be there tomorrow, so this
    is refused rather than sharing the draft's in-progress state."""
    if (gate := _write_gate(request)):
        return gate
    draft = store.get(draft_id)
    if not draft:
        return JSONResponse({"ok": False, "error": "That recipe is not here any more."}, status_code=404)
    if not draft.get("tandoor_id"):
        return JSONResponse({"ok": False,
                             "error": "Keep this recipe first -- only ones in the library can be shared."},
                            status_code=400)
    token = store.share_create(draft["tandoor_id"], draft["title"], _who(request))
    return {"ok": True, "url": f"{core.SITE_ROOT}/share/{token}",
           "days": store.SHARE_DAYS}


async def _push_own_photo(draft_id: str, recipe: dict, slug: str):
    """Send the photo YOU took to Mealie as the recipe's picture.

    Best effort, and silent about failing. By the time this runs the recipe is
    already filed with its ingredients and method; refusing the whole save over
    a picture would be the wrong trade, and an error page at that point reads
    as "it did not save" when it did.

    Only the hero -- Mealie has one image per recipe, so that is the one
    picture that belongs there. Step-level pictures go through
    _push_step_pictures below instead, which is a real per-step home
    (an asset plus a markdown embed in that step's own summary).
    """
    own = (recipe.get("mine") or {}).get("hero")
    if not own:
        return
    p = photos.path(draft_id, own)
    if not p:
        return
    try:
        await mealie.put_image(slug, p, own)
    except mealie.MealieError as e:
        print(f"kitchen: kept {slug} but its photo would not upload: {e}")


async def _push_step_pictures(draft_id: str, recipe: dict, tandoor_id):
    """Every step picture this recipe can have, right away rather than
    waiting on the idle artist to get to it -- see art.py's _idle_artist,
    which only works through the 200 most recent drafts, one recipe per
    pass. A recipe just kept is always among those 200, so it would get
    here eventually; this is just not making it wait.

    Real photos first (no graphics card needed, so no reason to queue them):
    whatever the source page already had, matched to a step by
    importer.attach_images(). Then whatever is still missing a picture and
    is worth drawing gets queued through the normal _draw_missing path,
    which checks the card is free and gives up cleanly if it is not --
    the idle artist or a bulk draw_fix.py run picks up anything skipped here.

    Best effort throughout, same as _push_own_photo_tandoor: the recipe is
    already saved by the time this runs, so a picture that does not land
    is a cosmetic gap, never a reason the keep itself should look like it
    failed.
    """
    steps = importer.attach_images(recipe.get("steps", []), recipe.get("images", []))
    for step in steps:
        photo = step.get("photo")
        if photo and photo.get("url"):
            await tandoor.push_step_photo(tandoor_id, step["n"], photo["url"],
                                          photo.get("alt", ""))
    try:
        await _draw_missing(draft_id, limit=6)
    except Exception as exc:
        print(f"kitchen: could not queue step drawings for {tandoor_id}: {exc}")


@app.post("/r/{draft_id}/keep")
async def keep(request: Request, draft_id: str):
    """Keep it. Whoever presses this is who it is kept by.

    THERE USED TO BE AN APPROVAL STEP HERE, AND THERE ISN'T ANY MORE.
    Everybody in the household is already someone Jason chose to let cook here
    -- that is the actual gate for THIS action, and it is enforced by
    _write_gate() below rather than by making them wait for a second person to
    look at every single recipe. That used to mean Mama's recipes sat as
    "waiting for Jason" until he happened to open the app. What matters is not
    gatekeeping who can add a recipe -- it is knowing who did, which
    mark_kept() now records on every Keep.

    Signed in at all (people.json) is a wider circle than the household
    (people.WRITERS) -- somebody can be let into /admin/people for a reason
    that has nothing to do with cooking, and read the whole library without
    being able to change it. See _write_gate().

    "The owner has final say" still holds where it actually needs to: nobody
    but Jason can rename or delete something that is already in the library --
    that is _is_owner()'s own gate on every rename/merge/delete route,
    unaffected by this route, and it is the real backstop. This route only
    ever ADDS. (Was "the household wall in handoff.py" -- that file was
    Mealie-era sign-in hand-off, deleted 2026-08-25 once handoff_tandoor.py
    had fully replaced it and nothing called it any more.)
    """
    if (gate := _write_gate(request)):
        return gate
    if not store.get(draft_id):
        return _oops(request, "That recipe is not here any more.", 404)
    ok, err = await _keep_one(draft_id, _who(request))
    if not ok:
        return _oops(request, f"The library would not take it: {err}\n\nThe "
                              "guide itself is fine — go back and it is still there.")
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}?kept=1", 303)


async def _keep_one(draft_id: str, who: str) -> tuple:
    """Put one draft in the library. Returns (ok, error-message-or-empty).

    THE ONE PLACE "KEEP" ACTUALLY HAPPENS, so the button and Keep-all (below)
    cannot drift on what it does -- push the photo, then stamp who and when.
    Already-kept is treated as success rather than an error: Keep-all calls
    this on every 'done' row in a batch without first checking which ones
    already made it in, and a recipe somebody kept individually five minutes
    ago must not come back as a failure in that list.

    TANDOOR, NOT MEALIE -- switched over as part of the Mealie-to-Tandoor
    migration (Mealie is stopped, data intact, not deleted).
    """
    draft = store.get(draft_id)
    if not draft:
        return False, "gone"
    if draft.get("tandoor_id"):
        return True, ""
    try:
        recipe_id = await tandoor.keep(draft["recipe"], draft_id)
    except tandoor.TandoorError as e:
        return False, str(e)
    await _push_own_photo_tandoor(draft_id, draft["recipe"], recipe_id)
    # BACKGROUNDED, not awaited -- real photos are a handful of downloads and
    # a drawing pass can be seconds of GPU time; the person who just pressed
    # Keep should not wait on either. Same tradeoff push_recipe_nutrition
    # already makes in mealie.py's keep().
    asyncio.create_task(_push_step_pictures(draft_id, draft["recipe"], recipe_id))
    asyncio.create_task(tandoor.push_recipe_nutrition(recipe_id))
    store.mark_kept_tandoor(draft_id, recipe_id, who)
    # Jason's own request (2026-08-25): "when a new recipe is sent I want
    # a notification if it's not made by me on what recipe it is so I
    # know to get images." THE one place Keep happens (see this
    # function's own docstring), so every path that adds a recipe --
    # the single Keep button, Keep-all, /recipe's own import -- reaches
    # this the same way.
    if who and not people.is_owner(who):
        notify.new_recipe(draft["recipe"].get("title") or "Untitled",
                          labels.friendly_name(who), f"{core.ROOT_PATH}/r/{draft_id}")
    return True, ""


async def _reattach_step_files(draft_id: str, recipe: dict, recipe_id):
    """Put back the step pictures an edit just orphaned.

    CAUGHT LIVE (see tandoor.update_recipe()'s own docstring): PATCHing a
    recipe's steps creates brand new Step rows every time, so the picture
    that was attached to the OLD step is not attached to anything any
    more. Nothing is actually lost -- Kitchen keeps its own copy of every
    photo and drawing on the draft regardless of Tandoor -- this just
    re-uploads what is already sitting on disk onto the new step ids,
    rather than drawing anything fresh. Best effort throughout, same
    reasoning as every other picture push here: the edit itself already
    saved, a picture that does not re-attach is a cosmetic gap, not a
    failure.
    """
    mine = recipe.get("mine") or {}
    drawn = recipe.get("drawn") or {}
    for key, val in {**drawn, **mine}.items():
        # `mine` wins where both exist, same priority _draw_each() already
        # gives a real photo over a drawing.
        if not key.startswith("step-"):
            continue
        try:
            n = int(key.removeprefix("step-"))
        except ValueError:
            continue
        name = mine.get(key) or drawn.get(key)
        p = photos.path(draft_id, name)
        if not p:
            continue
        ext = name.rsplit(".", 1)[-1]
        try:
            if key in mine:
                await tandoor.push_own_step_photo(recipe_id, n, p.read_bytes(), ext)
            else:
                await tandoor.push_step_illustration(recipe_id, n, p.read_bytes())
        except tandoor.TandoorError as e:
            print(f"kitchen: could not re-attach {key} after edit: {e}")


async def _push_own_photo_tandoor(draft_id: str, recipe: dict, recipe_id):
    """Send the photo YOU took to Tandoor as the recipe's picture. Same
    shape and same best-effort reasoning as the old _push_own_photo() --
    see that function's docstring -- retargeted at tandoor.put_image()."""
    own = (recipe.get("mine") or {}).get("hero")
    if not own:
        return
    p = photos.path(draft_id, own)
    if not p:
        return
    try:
        await tandoor.put_image(recipe_id, p.read_bytes(), own)
    except tandoor.TandoorError as e:
        print(f"kitchen: kept recipe {recipe_id} but its photo would not upload: {e}")


# Batches currently running a Keep-all pass. In memory only, not the database
# -- the same tradeoff the import workers already make (see _bulk_worker): a
# container restart mid-run loses the "in progress" flag, but Keep-all is
# idempotent (see _keep_one's already-kept check), so pressing the button
# again after a restart just finishes what is left rather than double-keeping
# anything.
KEEPING_ALL: set = set()


@app.post("/bulk/{batch}/keep_all")
async def keep_all(request: Request, batch: str):
    """Everything this import found a recipe on, in the library, in one go.

    WHY THIS EXISTS: importing brought in a plate of new drafts, but nothing
    puts them in the library except pressing Keep on each one -- and after a
    forty-link bulk import, that IS the app being unusable, not a small annoyance.
    This is the same _keep_one() the single button calls, run once per
    successful row in the batch, so "I imported a lot of recipes" ends with
    them actually being somewhere you can find them, not forty-nine separate
    button presses.
    """
    if (gate := _write_gate(request)):
        return gate
    rows = store.bulk_rows(batch)
    if not rows:
        return _oops(request, "No import by that name.", 404)
    asyncio.create_task(_keep_all_worker(batch, rows, _who(request)))
    return RedirectResponse(f"{core.ROOT_PATH}/bulk/{batch}", 303)


async def _keep_all_worker(batch: str, rows: list, who: str):
    KEEPING_ALL.add(batch)
    try:
        for row in rows:
            if row["state"] != "done" or not row["draft_id"]:
                continue
            try:
                await _keep_one(row["draft_id"], who)
            except Exception as exc:      # one bad recipe must not stop the rest
                print(f"kitchen: keep-all could not keep {row['draft_id']}: {exc}")
    finally:
        KEEPING_ALL.discard(batch)


# ---------------------------------------------------------------------------
#  Her own recipes — the ones that were never on a website
# ---------------------------------------------------------------------------
@app.get("/mine", response_class=HTMLResponse)
async def own_form(request: Request):
    # THE COOK'S OWN TRADITIONS FIRST. Forty cuisines in one dropdown is forty
    # chances to scroll past the one you meant, and it is nearly always the
    # same handful. The rest is still on the page, one tap down.
    home = taxonomy.home_for(labels.COOKS.get(_who(request), ""))
    return _page(request, "mine.html",
                 courses=taxonomy.COURSES,
                 home_cuisines=home,
                 other_cuisines=taxonomy.rest_of(home))


@app.post("/mine")
async def own_save(request: Request,
                   title: str = Form(""), needs: str = Form(""),
                   steps: str = Form(""), serves: str = Form(""),
                   course: str = Form(""), cuisine: str = Form(""),
                   cuisine_other: str = Form(""), notes: str = Form(""),
                   request_id: str = Form("")):
    """A recipe typed in by the person who cooks it.

    NO MODEL TOUCHES THE CONTENT HERE, and that is the whole point. An imported
    recipe is a guess about what a web page meant; this is somebody telling you
    how they make it. Running that through a model to "tidy" it would replace
    the one thing that makes it worth having -- her words, her order, her
    amounts -- with a paraphrase.

    So the text is stored exactly as typed. The model is asked for labels
    afterwards, and only for labels.
    """
    if (gate := _write_gate(request)):
        return gate
    title = title.strip()
    # WHAT YOU TYPED WINS over what was ticked. Somebody who opened "somewhere
    # else" and typed a tradition has said something more specific than the
    # button that happened to be selected when they got there.
    cuisine = (cuisine_other.strip() or cuisine.strip())
    ingredients = [line.strip() for line in needs.splitlines() if line.strip()]
    method = [importer._denumber(line) for line in steps.splitlines() if line.strip()]

    if not title:
        return _oops(request, "It needs a name — even just “Mama's greens”.")
    if not ingredients:
        return _oops(request, "Put in what you need, one thing per line.")
    if not method:
        return _oops(request, "Put in how you make it, one step per line.")

    recipe = crawl.Recipe(
        title=title,
        source="",
        image="", images=[], mine={},
        course=taxonomy.clean_course(course),
        cuisine=taxonomy.clean_cuisine(cuisine),
        # Set by the person writing it out, so nothing re-guesses them later.
        # Only marked by-hand if they actually answered -- leaving both blank
        # should let the model have a go rather than locking in "nobody said".
        taxonomy_by_hand=bool(course.strip() or cuisine.strip()),
        yields=serves.strip(),
        prep="", cook="", total="",
        ingredients=ingredients,
        steps=method,
        notes=notes.strip(),
        # THE PILL IS PUT ON HERE, not asked for. Somebody typing out how
        # they make a thing has, by definition, made it -- and it is THEIR
        # pill: mama's recipes and Casey's each get their own shelf.
        labels=[labels.made_by(_who(request))],
        how="written out by hand — not read off a website",
    )
    draft_id = store.save(recipe, _who(request))
    asyncio.create_task(_label_later(draft_id))
    # BEST EFFORT, AFTER THE SAVE, NEVER BEFORE IT. Whether this recipe was
    # written in answer to a request is bookkeeping for Jason's own /requests
    # list -- a bad or stale request_id (the row got deleted, the field was
    # tampered with) must never look like the recipe itself failed to save.
    if request_id.strip() and store.request_get(request_id.strip()):
        store.request_fulfill(request_id.strip(), draft_id)
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}", status_code=303)


@app.get("/r/{draft_id}/edit", response_class=HTMLResponse)
async def edit_recipe(request: Request, draft_id: str):
    """The form: title, ingredients, steps, one line each. Real edits, not
    the substitution suggestions /swap gives -- what you type here is what
    the recipe becomes, in Kitchen and in the library both.

    CHECKED LIVE BEFORE BUILDING THIS: there was no way to do this at all.
    /refresh-from-source only ever fills gaps, never overwrites; /swap and
    friends are LLM suggestions that never touch the stored recipe. This
    is the first real "change what this recipe says" route.
    """
    if (gate := _write_gate(request)):
        return gate
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    return _page(request, "edit_recipe.html", draft=draft, id=draft_id)


@app.post("/r/{draft_id}/edit")
async def save_edit(request: Request, draft_id: str,
                    title: str = Form(...), ingredients: str = Form(...),
                    steps: str = Form(...)):
    if (gate := _write_gate(request)):
        return gate
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    recipe = draft["recipe"]
    recipe["title"] = title.strip() or recipe.get("title") or "Untitled"
    recipe["ingredients"] = [ln.strip() for ln in ingredients.splitlines() if ln.strip()]
    recipe["steps"] = [ln.strip() for ln in steps.splitlines() if ln.strip()]
    store.update_recipe(draft_id, recipe)

    # ALREADY IN THE LIBRARY? PUSH THE EDIT THERE TOO, and put the step
    # pictures back -- see tandoor.update_recipe()'s own docstring for why
    # editing steps at all orphans whatever picture was on them.
    if draft.get("tandoor_id"):
        try:
            await tandoor.update_recipe(draft["tandoor_id"], recipe, draft_id)
            await _reattach_step_files(draft_id, recipe, draft["tandoor_id"])
        except tandoor.TandoorError as e:
            return _oops(request, f"Saved here, but the library would not "
                                  f"take the edit: {e}\n\nYour change is "
                                  "not lost -- go back and it is still here.")
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}", status_code=303)


@app.post("/r/{draft_id}/about")
async def set_about(request: Request, draft_id: str,
                    course: str = Form(""), cuisine: str = Form("")):
    """Set the course and where it comes from, by hand.

    MARKS THEM AS A PERSON'S ANSWER (taxonomy_by_hand), which stops any later
    re-label overwriting them. That flag is the whole point of this route: a
    model reading an ingredient list will call a dish "american" when the
    family that has made it for four generations calls it something else, and
    correcting that has to stick.
    """
    if (gate := _write_gate(request)):
        return gate
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    recipe = draft["recipe"]
    recipe["course"] = taxonomy.clean_course(course)
    recipe["cuisine"] = taxonomy.clean_cuisine(cuisine)
    recipe["taxonomy_by_hand"] = True
    store.update_recipe(draft_id, recipe)
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}", status_code=303)


@app.post("/r/{draft_id}/refresh-from-source")
async def refresh_from_source(request: Request, draft_id: str):
    """Go back to this recipe's own source page for whatever was missed the
    first time round: nutrition the site published, its own rating and
    reviews, and any ingredient line the steps actually give an amount for
    but the list itself left bare ("salt and pepper" with nothing in front
    of it). Only ever FILLS GAPS -- never overwrites a title, step, or
    ingredient line that's already here, hand-edited or not."""
    if (gate := _write_gate(request)):
        return gate
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    changed = await _refresh_one(draft_id, draft)
    if not changed:
        return _oops(request, "Nothing new to pull in -- either the source "
                     "page did not have anything more than what's already "
                     "here, or it could not be reached just now.")
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}#nutrition", status_code=303)


async def _refresh_one(draft_id: str, draft: dict) -> bool:
    """The actual backfill -- shared by the button above and enrich_fix.py's
    batch pass over the whole library. Returns True if anything changed."""
    recipe = draft["recipe"]
    changed = False

    # Ingredient completion never needs the network -- it only looks at
    # what is already stored (ingredients against this recipe's own steps).
    new_ings = await crawl.fill_ingredients_from_steps(
        recipe.get("ingredients") or [], recipe.get("steps") or [])
    if new_ings != recipe.get("ingredients"):
        recipe["ingredients"] = new_ings
        changed = True

    # Nutrition/rating/reviews need the source page again -- only bothered
    # with when there's a source URL AND something is still actually
    # missing, so a recipe that already has everything never re-fetches.
    source = recipe.get("source", "")
    if source and (not recipe.get("nutrition_stated")
                   or not recipe.get("source_reviews")
                   or not recipe.get("source_rating")):
        try:
            final_url, page_html = await crawl.fetch(source)
            fresh = crawl.from_jsonld(crawl.read(page_html), final_url)
        except Exception:  # noqa: BLE001 -- a dead/blocked source page is
            fresh = None    # not a reason to lose the ingredient fix above
        if fresh:
            if not recipe.get("nutrition_stated") and fresh.get("nutrition_stated"):
                recipe["nutrition_stated"] = fresh["nutrition_stated"]
                changed = True
            if not recipe.get("source_reviews") and fresh.get("source_reviews"):
                recipe["source_reviews"] = fresh["source_reviews"]
                changed = True
            if not recipe.get("source_rating") and fresh.get("source_rating"):
                recipe["source_rating"] = fresh["source_rating"]
                changed = True

    if not changed:
        return False

    store.update_recipe(draft_id, recipe)
    slug = draft.get("mealie_slug")
    if slug:
        # Best effort into Mealie too -- Kitchen's own save above already
        # succeeded, so a Mealie hiccup here shrinks what happened, it does
        # not undo it.
        try:
            await mealie.push_ingredients(slug, recipe["ingredients"])
            await mealie.push_recipe_nutrition(slug)
        except Exception:  # noqa: BLE001
            pass
    return True


@app.post("/r/{draft_id}/tested")
async def mark_tested(request: Request, draft_id: str):
    """Put the made-and-tested pill on, or take it off. A person's call only."""
    if (gate := _write_gate(request)):
        return gate
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    recipe = draft["recipe"]
    current = list(recipe.get("labels") or [])
    # YOUR OWN pill, toggled. Pressing this on a recipe Casey has already
    # vouched for adds yours alongside hers rather than replacing it -- two
    # people having made the same thing is worth more than one, not less.
    mine = labels.made_by(_who(request))
    if mine in current:
        current.remove(mine)
    else:
        current.insert(0, mine)
    recipe["labels"] = current
    store.update_recipe(draft_id, recipe)
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}", status_code=303)


# ---------------------------------------------------------------------------
#  Bulk import — a pile of links at once
# ---------------------------------------------------------------------------
@app.post("/import/bulk")
async def bulk_start(request: Request, urls: str = Form(""), page: str = Form("home")):
    if (gate := _write_gate(request)):
        return gate
    # WHICH PAGE TO RETURN TO ON A REJECTION -- this form lives on both the
    # main home page and the bookmark-only /recipe-import page, and a
    # rejection has to land back on whichever one was actually used, not
    # always home.html (which the bookmark page's visitor may never see the
    # rest of, and shouldn't have to navigate away from). A hidden field,
    # not the Referer header, because privacy settings strip that.
    template = "recipe_import.html" if page == "recipe-import" else "home.html"
    found = []
    for line in (urls or "").splitlines():
        line = line.strip()
        # Take the URL out of a line that may have other things on it. People
        # paste from notes, from a chat, from a numbered list -- "3. Lasagne
        # https://..." should work rather than being rejected for tidiness.
        m = re.search(r"https?://\S+", line)
        if m:
            found.append(m.group(0).rstrip(").,"))

    # Deduplicated, order kept. Pasting the same list twice is common and
    # should not import anything twice.
    seen, urls_list = set(), []
    for u in found:
        if u not in seen:
            seen.add(u)
            urls_list.append(u)

    # A REJECTION KEEPS WHAT YOU PASTED, on both paths below. _oops() used to
    # be what these returned -- a separate blank page with just the message,
    # nothing of what you'd typed. Going over the limit once meant the whole
    # pasted list was gone and had to be retyped from scratch. Re-rendering
    # home.html with `urls` still in it is the actual fix; the counter in
    # home.html (js, live as you paste) is what should stop this happening in
    # the first place, and this is the fallback for whenever it doesn't --
    # JavaScript off, or a client that ignores it.
    if not urls_list:
        return _page(request, template, **(await _home_ctx()),
                     bulk_error="I could not find any links in that. One web "
                               "address per line is the surest way.",
                     bulk_prefill=urls)
    if len(urls_list) > BULK_MAX:
        return _page(request, template, **(await _home_ctx()),
                     bulk_error=f"That is {len(urls_list)} links. {BULK_MAX} at a "
                               "time is the limit — each one is a page to fetch "
                               "and some need the model, so a huge batch would "
                               "sit on the GPU for an hour. Trim the list below "
                               "and send it again -- nothing here was lost.",
                     bulk_prefill=urls)

    batch = uuid.uuid4().hex[:10]
    store.bulk_queue(batch, urls_list)
    # ONE worker, not one task per link. Several imports at once would each
    # want the model, and on a single 4070 that is slower overall than doing
    # them in turn -- plus it would queue behind the phone line.
    asyncio.create_task(_bulk_worker(batch, _who(request)))
    return RedirectResponse(f"{core.ROOT_PATH}/bulk/{batch}", status_code=303)


async def _bulk_worker(batch: str, who: str):
    while True:
        row = store.bulk_next(batch)
        if not row:
            return
        try:
            recipe = await importer.from_url(row["url"])
        except (core.Blocked, llm.LLMError) as e:
            store.bulk_finish(row["id"], "failed", str(e))
            continue
        except Exception as e:
            store.bulk_finish(row["id"], "failed",
                              f"{e.__class__.__name__}: {e}")
            continue

        draft_id = store.save(recipe, who)
        # Labelled INLINE here, unlike a single import. Nobody is staring at a
        # spinner waiting for this one -- the whole point of a batch is that
        # you walk away -- so the recipe may as well arrive finished.
        try:
            found = await labels.suggest(recipe, store.all_labels())
            if found:
                recipe["labels"] = found
            read = await taxonomy.read(recipe)
            recipe["course"] = read["course"]
            recipe["cuisine"] = read["cuisine"]
            store.update_recipe(draft_id, recipe)
        except Exception:
            pass
        store.bulk_finish(row["id"], "done", "", draft_id,
                          recipe.get("title", "Untitled"))


@app.get("/bulk/{batch}", response_class=HTMLResponse)
async def bulk_status(request: Request, batch: str):
    rows = store.bulk_rows(batch)
    if not rows:
        return _oops(request, "No import by that name.", 404)
    importing = any(r["state"] in ("waiting", "working") for r in rows)
    keeping = batch in KEEPING_ALL
    kept_ids = store.kept_ids([r["draft_id"] for r in rows])
    for r in rows:
        r["kept"] = r["draft_id"] in kept_ids
    done = sum(1 for r in rows if r["state"] == "done")
    return _page(request, "bulk.html", rows=rows, batch=batch,
                 busy=importing or keeping, keeping=keeping,
                 done=done, failed=sum(1 for r in rows if r["state"] == "failed"),
                 kept=len(kept_ids),
                 # Offer the button only once importing has actually stopped --
                 # keeping the ones already done WHILE more are still being read
                 # just means the button's own count is wrong before you have
                 # finished reading the notice next to it.
                 can_keep_all=(not importing and not keeping
                              and done > len(kept_ids)))


# ---------------------------------------------------------------------------
#  Browsing by label
# ---------------------------------------------------------------------------
@app.get("/browse", response_class=HTMLResponse)
async def browse(request: Request, label: str = "", course: str = "",
                 cuisine: str = "", cook: str = ""):
    """Four rows of pills, because they answer four different questions.

    Course and cuisine are their own FIELDS, not labels -- but they are pills
    here too, because "tap the thing you want" is how you browse on a phone and
    a person should not have to know which of them is stored differently.
    """
    every = store.all_labels()
    made = [(n, c) for n, c in every if labels.is_made_pill(n)]
    rest = [(n, c) for n, c in every if not labels.is_made_pill(n)]
    # A ONE-OFF LABEL STILL CLUTTERS THE ROW EVEN THOUGH NOBODY WOULD EVER
    # TAP IT. Below 5 recipes a pill is rarely worth the space it takes on a
    # phone, so those wait behind "Show N more" rather than crowding out the
    # handful of labels that actually narrow anything down.
    common_pills = [(n, c) for n, c in rest if c >= 5]
    rare_pills = [(n, c) for n, c in rest if c < 5]

    # WHO PUT IT HERE. Derived from the who_email column rather than stored as
    # a label, so it cannot drift and renaming a cook renames every pill.
    whose = [(labels.added_by(e), e, n) for e, n in store.cook_counts()]
    whose = [w for w in whose if w[0]]

    course, cuisine = course.strip().lower(), cuisine.strip().lower()
    label = label.strip().lower()

    # A rare pill that IS the active filter must still be visible -- tucking
    # away the very thing somebody is looking at, because it happens to have
    # fewer than 5 recipes, would be a worse bug than the clutter this fixes.
    active_rare = next((p for p in rare_pills if p[0] == label), None)
    if active_rare:
        rare_pills.remove(active_rare)
        common_pills = [active_rare] + common_pills

    if cook:
        items, heading = store.by_cook(cook), labels.added_by(cook) or cook
    elif course:
        items, heading = store.with_field("course", course), course
    elif cuisine:
        items, heading = store.with_field("cuisine", cuisine), cuisine
    elif label:
        items, heading = store.with_label(label), label
    else:
        items, heading = [], ""

    return _page(request, "browse.html",
                 cards=_named(store.cards(ids=[i["id"] for i in items])) if items else [],
                 votes=store.vote_counts(), says=store.comment_counts(),
                 pills=common_pills, rare_pills=rare_pills, made=made, whose=whose,
                 courses=store.field_counts("course"),
                 cuisines=store.field_counts("cuisine"),
                 label=label, course=course, cuisine=cuisine, cook=cook,
                 heading=heading, items=items)


# ---------------------------------------------------------------------------
#  The shopping list
# ---------------------------------------------------------------------------
@app.get("/list", response_class=HTMLResponse)
async def shopping(request: Request):
    """The list, grouped by what you are making, plus what else you nearly can.

    Both answers are worked out in code -- see basket.py. Nothing here waits on
    a model, because this page is read standing in a shop with one bar of
    signal, which is the worst possible place to be waiting on a GPU.
    """
    items = store.shopping_all()
    ticked = [i["text"] for i in items if i.get("done")]
    return _page(request, "list.html",
                 items=items,
                 groups=basket.by_recipe(items),
                 # WHAT ELSE, from what is actually IN the trolley rather than
                 # what is still on the list. You change your mind about dinner
                 # based on what you have picked up, not what you meant to.
                 could=basket.could_make(ticked, store.all_ingredients()),
                 ticked=len(ticked))


@app.post("/list/add")
async def shopping_add_free(request: Request, text: str = Form("")):
    if (gate := _write_gate(request)):
        return gate
    lines = [t.strip() for t in (text or "").splitlines() if t.strip()]
    if lines:
        store.shopping_add(lines, who=_who(request))
    return RedirectResponse(f"{core.ROOT_PATH}/list", status_code=303)


@app.post("/list/{item_id}/tick")
async def shopping_tick(request: Request, item_id: int):
    if (gate := _write_gate(request)):
        return gate
    store.shopping_toggle(item_id)
    return RedirectResponse(f"{core.ROOT_PATH}/list#i{item_id}", status_code=303)


@app.post("/list/{item_id}/remove")
async def shopping_drop(request: Request, item_id: int):
    if (gate := _write_gate(request)):
        return gate
    store.shopping_remove(item_id)
    return RedirectResponse(f"{core.ROOT_PATH}/list", status_code=303)


@app.post("/list/clear")
async def shopping_clear(request: Request):
    if (gate := _write_gate(request)):
        return gate
    store.shopping_clear_done()
    return RedirectResponse(f"{core.ROOT_PATH}/list", status_code=303)


@app.post("/r/{draft_id}/shop")
async def shop_from_recipe(request: Request, draft_id: str, serves: str = Form("")):
    """Put this recipe's ingredients on the list, AT THE PORTIONS SHOWN.

    The serving count comes from the page you were looking at, not from the
    recipe's own yield. If you have scaled a lasagne up to eight and then press
    this, the list must say what you need for eight -- getting that wrong sends
    somebody to the shop with the wrong quantities, which is the one job this
    button has.
    """
    if (gate := _write_gate(request)):
        return gate
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    want = int(serves) if serves.isdigit() and 0 < int(serves) <= 100 else None
    view = importer.build_guide(draft["recipe"], want)
    lines = [item["text"] for item in view["ingredients"]]
    added = store.shopping_add(lines, source=draft["title"], draft_id=draft_id,
                               who=_who(request))
    return RedirectResponse(f"{core.ROOT_PATH}/list?added={added}", status_code=303)


# ---------------------------------------------------------------------------
#  The meal plan — a thin wrapper, unlike the shopping list above. Mealie
#  already has a real planner (per-household, per-day, per-meal-type); this
#  page is just quicker to use than Mealie's own for "what's for dinner this
#  week", the same reason Kitchen exists at all.
# ---------------------------------------------------------------------------
def _week(start: str = "") -> list:
    """Seven ISO dates, today first unless a Monday was asked for."""
    if start:
        try:
            base = _dt.date.fromisoformat(start)
        except ValueError:
            base = _dt.date.today()
    else:
        base = _dt.date.today()
    return [(base + _dt.timedelta(days=i)).isoformat() for i in range(7)]


@app.get("/plan", response_class=HTMLResponse)
async def plan(request: Request, start: str = ""):
    """CAUGHT LIVE (2026-08-24): still calling mealie.mealplan_week() long
    after Mealie was stopped -- try/except kept it from 500ing, but it has
    been answering with a plain error page ever since. Retargeted at
    Tandoor's own /api/meal-plan/ (confirmed live, see tandoor.py's own
    meal-plan section for the request-shape testing that went into it).
    """
    dates = _week(start)
    entries = await tandoor.meal_plan_range(dates[0], dates[-1])
    by_date: dict = {d: [] for d in dates}
    for e in entries:
        d = (e.get("from_date") or "")[:10]
        if d in by_date:
            by_date[d].append(e)
    days = []
    for d in dates:
        day = _dt.date.fromisoformat(d)
        entries_here = by_date[d]
        for e in entries_here:
            tandoor_id = (e.get("recipe") or {}).get("id")
            draft = store.get_by_tandoor_id(tandoor_id) if tandoor_id else None
            e["kitchen_id"] = draft["id"] if draft else ""
            e["public_link"] = tandoor.public_link(tandoor_id) if tandoor_id else ""
            e["title"] = e.get("title") or (e.get("recipe") or {}).get("name") or "Untitled"
            e["entryType"] = e.get("meal_type_name") or ""
        days.append({"date": d, "label": day.strftime("%A, %b %-d"),
                    "is_today": d == _dt.date.today().isoformat(),
                    "entries": entries_here})
    prev_start = (_dt.date.fromisoformat(dates[0]) - _dt.timedelta(days=7)).isoformat()
    next_start = (_dt.date.fromisoformat(dates[0]) + _dt.timedelta(days=7)).isoformat()
    return _page(request, "plan.html", days=days,
                 prev_start=prev_start, next_start=next_start,
                 entry_types=["breakfast", "lunch", "dinner", "side"])


@app.post("/r/{draft_id}/plan")
async def plan_add(request: Request, draft_id: str, date: str = Form(...),
                   entry_type: str = Form("dinner")):
    if (gate := _write_gate(request)):
        return gate
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    if not draft.get("tandoor_id"):
        return _oops(request, "Keep this recipe first -- the meal plan is "
                              "shared with the whole household through the "
                              "library, and only kept recipes are in it.")
    entry_id = await tandoor.meal_plan_add(date, draft["tandoor_id"], entry_type)
    if not entry_id:
        return _oops(request, "The library would not add that to the plan.")
    return RedirectResponse(f"{core.ROOT_PATH}/plan?start={date}", status_code=303)


@app.post("/plan/{item_id}/remove")
async def plan_remove(request: Request, item_id: int, start: str = Form("")):
    if (gate := _write_gate(request)):
        return gate
    await tandoor.meal_plan_remove(item_id)
    dest = f"{core.ROOT_PATH}/plan"
    if start:
        dest += f"?start={start}"
    return RedirectResponse(dest, status_code=303)


# ---------------------------------------------------------------------------
#  Surprise me — one recipe, picked at random from the shared library.
# ---------------------------------------------------------------------------
@app.get("/surprise")
async def surprise(request: Request):
    """CAUGHT LIVE (2026-08-24): still calling mealie.random_recipe() long
    after Mealie was stopped -- wrapped in a try/except so it never 500'd,
    just quietly answered with an error page every time since. Tandoor's
    own `?random=true` filter (confirmed live) replaces it."""
    recipe = await tandoor.random_recipe()
    if not recipe:
        return _oops(request, "There is nothing in the library to pick from yet.")
    draft = store.get_by_tandoor_id(recipe["id"])
    if draft:
        return RedirectResponse(f"{core.ROOT_PATH}/r/{draft['id']}", status_code=303)
    link = tandoor.public_link(recipe["id"])
    if link:
        return RedirectResponse(link, status_code=303)
    return _oops(request, "Found one, but there's no way to open it from here.")


# ---------------------------------------------------------------------------
#  Did it turn out? — thumbs and notes
# ---------------------------------------------------------------------------
@app.post("/r/{draft_id}/vote")
async def cast_vote(request: Request, draft_id: str, up: str = Form("")):
    """A thumb, one per person, toggling.

    Pressing the thumb that is already lit takes the vote back rather than
    doing nothing -- it is the same button, and "I changed my mind" is a
    normal thing that should not need a third control to express.
    """
    if (gate := _write_gate(request)):
        return gate
    who = _who(request)
    if not who:
        return _oops(request, "This page is only reachable through the "
                              "website's sign-in.", 401)
    if not store.get(draft_id):
        return _oops(request, "That recipe is not here any more.", 404)
    store.vote(draft_id, who, 1 if up == "1" else -1)
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}#says", 303)


@app.post("/r/{draft_id}/say")
async def add_note(request: Request, draft_id: str, text: str = Form("")):
    if (gate := _write_gate(request)):
        return gate
    who = _who(request)
    if not who:
        return _oops(request, "This page is only reachable through the "
                              "website's sign-in.", 401)
    if not store.get(draft_id):
        return _oops(request, "That recipe is not here any more.", 404)
    store.add_comment(draft_id, who, labels.friendly_name(who), text)
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}#says", 303)


@app.post("/r/{draft_id}/say/{comment_id}/remove")
async def drop_note(request: Request, draft_id: str, comment_id: int):
    # Your own, or anybody's if you are the owner. The check is in the DELETE
    # itself rather than a read-then-write, so it cannot be raced.
    store.drop_comment(comment_id, _who(request), _is_owner(request))
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}#says", 303)


# ---------------------------------------------------------------------------
#  Drawings for the steps that have no picture
# ---------------------------------------------------------------------------
async def _draw_missing(draft_id: str, limit: int = 12) -> tuple:
    """Draw what is missing on one recipe. Returns (how many, why it stopped).

    Sequential on purpose. The card draws one picture at a time whatever we
    ask of it, so firing off eight requests only means eight things queued in
    front of the check that decides whether to give the card back.
    """
    draft = store.get(draft_id)
    if not draft:
        return 0, "that recipe is not here any more"

    ok, why = await art.room()
    if not ok:
        return 0, why

    recipe = draft["recipe"]
    drawn = recipe.setdefault("drawn", {})
    mine = recipe.get("mine") or {}
    steps = importer.attach_images(recipe.get("steps", []), recipe.get("images", []))
    tandoor_id = draft.get("tandoor_id") or ""
    slug = draft.get("mealie_slug") or ""

    try:
        return await _draw_each(draft_id, recipe, drawn, mine, steps, limit,
                                slug, tandoor_id)
    finally:
        # WHATEVER HAPPENED, GIVE THE CARD BACK. Not on the happy path only:
        # the case that matters is the one where this raised halfway, because
        # that is exactly when nobody is left to notice 7GB is still taken.
        await art.release()


async def _draw_each(draft_id, recipe, drawn, mine, steps, limit, slug="",
                     tandoor_id="") -> tuple:
    made = 0
    # Jason's own report (2026-08-25): "the /recipe llm image generator
    # was supposed to be more specific using the items and more verbose
    # details along with the tools." Computed once for the whole recipe,
    # not per step -- _get_icon_map() is itself cached (see its own TTL),
    # so this is just avoiding an extra await per step for no reason.
    icon_map = await _get_icon_map()
    for step in steps:
        if made >= limit:
            return made, "that is as many as one go does"
        slot = f"step-{step['n']}"
        # Anything real already there wins and is left alone.
        if mine.get(slot) or step.get("photo") or drawn.get(slot):
            continue
        if not art.worth_drawing(step["text"]):
            continue

        # Seeded off the draft, the step, AND how many times this exact slot
        # has been drawn before -- tries[slot], persisted on the recipe.
        #
        # CAUGHT LIVE: this used to be seeded off just the draft and the step
        # number, which was deliberate -- "re-drawing the same step gives the
        # same picture rather than a different one each time somebody
        # reloads" -- but it had a real cost nobody noticed until now: a step
        # never redraws differently AT ALL, by design, so "remove the
        # drawing" followed by "draw the missing ones" reliably produced the
        # exact same picture back. From the person clicking it, that reads as
        # "there's a button to change the picture and it doesn't do
        # anything" -- which is exactly the bug reported. The fix keeps the
        # original guarantee (a page reload never redraws anything, because
        # `drawn.get(slot)` is already set and this loop skips it before
        # ever reaching art.draw()) while making an EXPLICIT remove-then-draw
        # actually change the result.
        tries = recipe.setdefault("drawn_tries", {})
        tries[slot] = tries.get(slot, 0) + 1

        # A picture Jason found online for THIS step, described by the
        # vision model and folded into the prompt -- Jason's own ask
        # (2026-08-24): "use my pictures and increase context to be able
        # to generate more accurate pictures." Best effort and silent:
        # this only ever works while Jason has vision mode switched on
        # (see vram.py), and a step with no reference or a model that
        # isn't loaded right now just draws exactly as it always did.
        reference_detail = ""
        ref_name = (recipe.get("reference") or {}).get(slot)
        ref_path = photos.path(draft_id, ref_name) if ref_name else None
        if ref_path:
            try:
                reference_detail = await llm.describe_image(
                    ref_path.read_bytes(),
                    "Describe what this picture shows in one short, plain "
                    "sentence -- the food, the vessel, the stage of "
                    "cooking. No opinions, no recipe suggestions.")
            except llm.LLMError:
                pass

        # WHICH INGREDIENTS AND TOOLS ARE ACTUALLY IN THIS STEP -- the same
        # matcher the step's own small call-out icons already use
        # (_icons_for_step), reused here rather than duplicated. A step's
        # text often leans on a pronoun ("mix them together") that names
        # nothing a picture can be specific about; this gives the prompt
        # the real names even when the sentence itself does not say them.
        hits = _icons_for_step(step["text"], icon_map) if icon_map else []
        step_items = [h["name"] for h in hits if h["kind"] == "food"][:4]
        step_tools = [h["name"] for h in hits if h["kind"] == "tool"][:2]

        png = await art.draw(
            art.prompt_for(step["text"], recipe.get("title", ""),
                           recipe.get("cuisine", ""), reference_detail,
                           items=step_items, tools=step_tools),
            seed=(int(draft_id[:8], 16) + step["n"] * 1009 + tries[slot])
                 % (2 ** 31),
            negative=art.negative_for(step["text"]))
        if not png:
            # The card was taken by something interactive mid-run. Keep what
            # we drew and stop -- the phone line matters more than the rest of
            # this recipe's illustrations.
            store.update_recipe(draft_id, recipe)
            return made, "the graphics card was wanted for something else"
        try:
            drawn[slot] = photos.keep_drawn(draft_id, slot, png)
            made += 1
        except photos.PhotoError:
            continue

        # PUSHED TO THE LIBRARY TOO, only once the recipe is actually kept
        # (slug/tandoor_id set) -- a drawing on a draft nobody kept yet has
        # nowhere in the library to go. Best effort and never blocks the
        # loop: an upload failure here must not cost the picture itself,
        # which is already safely saved locally by keep_drawn() above.
        # Tandoor first -- that is the live library now; mealie_slug only
        # still fires for the handful of recipes kept before the switch.
        if tandoor_id:
            await tandoor.push_step_illustration(tandoor_id, step["n"], png)
        elif slug:
            caption = await art.caption_for(step["text"], recipe.get("title", ""))
            await mealie.push_step_illustration(slug, step["n"], png, caption)

        # PERSISTED HERE, NOT ONLY AFTER THE WHOLE LOOP. CAUGHT LIVE: this
        # loop can run for minutes across several steps (each art.draw()
        # call is a real GPU job), and if the process restarts partway
        # through -- a deploy, a crash -- `drawn` was only ever held in
        # memory until the loop finished, so every step drawn in an
        # interrupted pass looked "never drawn" again on the next run. The
        # picture itself was safe (keep_drawn() had already written it and
        # push_step_illustration() had already reached Mealie), but nothing
        # recorded that it happened, so the idle artist drew and pushed the
        # SAME step again next time it got to this recipe -- and kept doing
        # it, once per restart, until one real recipe had 41 duplicate
        # illustrations stacked on some steps. push_step_illustration() now
        # replaces rather than stacks (see mealie.py's strip_re), which
        # stops the duplication either way, but persisting progress as it
        # happens is the actual fix for the wasted redraws themselves.
        store.update_recipe(draft_id, recipe)

    store.update_recipe(draft_id, recipe)
    return made, ""


@app.post("/r/{draft_id}/draw")
async def draw_steps(request: Request, draft_id: str):
    if not _is_owner(request):
        return _oops(request, "Jason draws the step pictures.", 403)
    made, why = await _draw_missing(draft_id)
    if made:
        return RedirectResponse(
            f"{core.ROOT_PATH}/r/{draft_id}?drew={made}#method", 303)
    return RedirectResponse(
        f"{core.ROOT_PATH}/r/{draft_id}?nodraw={quote(why or 'nothing to draw')}"
        "#method", 303)


@app.post("/r/{draft_id}/draw/{slot}/remove")
async def drop_drawing(request: Request, draft_id: str, slot: str):
    if not _is_owner(request):
        return _oops(request, "Jason manages the step pictures.", 403)
    draft = store.get(draft_id)
    if draft:
        photos.remove(draft_id, slot, drawn=True)
        recipe = draft["recipe"]
        (recipe.get("drawn") or {}).pop(slot, None)
        store.update_recipe(draft_id, recipe)
        # CAUGHT LIVE: this used to stop here, clearing only the local copy.
        # On a kept recipe the library kept showing the old drawing forever
        # -- nothing told it to drop its own embed. Same fix drop_photo()
        # already has for step photos, just for the AI-drawn kind.
        if draft.get("tandoor_id") and slot.startswith("step-"):
            try:
                n = int(slot.removeprefix("step-"))
                await tandoor.remove_step_file(draft["tandoor_id"], n)
            except (ValueError, tandoor.TandoorError) as e:
                print(f"kitchen: removed the drawing here but the library kept it: {e}")
        elif draft.get("mealie_slug") and slot.startswith("step-"):
            try:
                n = int(slot.removeprefix("step-"))
                await mealie.remove_step_illustration(draft["mealie_slug"], n)
            except (ValueError, mealie.MealieError) as e:
                print(f"kitchen: removed the drawing here but the library kept it: {e}")
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}#s{slot}", 303)


# ---------------------------------------------------------------------------
#  Reading a step aloud (see app/tts.py)
# ---------------------------------------------------------------------------
@app.get("/r/{draft_id}/step/{n}/listen")
async def listen_step(request: Request, draft_id: str, n: int):
    """Audio bytes for one step, or a plain-text reason it could not read it.

    No extra gate beyond being signed in at all -- same as the guide page
    itself, which every signed-in person can already read. Playing text back
    is not a write.
    """
    draft = store.get(draft_id)
    if not draft:
        return PlainTextResponse("That recipe is not here any more.", status_code=404)
    steps = draft["recipe"].get("steps", [])
    if n < 1 or n > len(steps):
        return PlainTextResponse("That step is not there.", status_code=404)
    try:
        # cached_speak(), not speak() -- Jason's own request (2026-08-25):
        # "save the recipes to be able to speak aloud... something small
        # like an mp3." First press per step pays the GPU cost; every
        # press after that is a file read.
        audio, content_type = await tts.cached_speak(draft_id, f"step-{n}", steps[n - 1])
    except tts.TTSError as e:
        return PlainTextResponse(str(e), status_code=503)
    return Response(content=audio, media_type=content_type)


# ---------------------------------------------------------------------------
#  Your own photographs
# ---------------------------------------------------------------------------
@app.post("/r/{draft_id}/photo")
async def add_photo(request: Request, draft_id: str,
                    slot: str = Form(...), photo: UploadFile = File(...)):
    if (gate := _write_gate(request)):
        return gate
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    try:
        name = photos.save(draft_id, slot, photo)
    except photos.PhotoError as e:
        return _oops(request, str(e))

    # Recorded ON THE DRAFT rather than inferred from what is on disk. The
    # guide has to know a photo exists before it renders, and scanning a
    # directory per step would be a stat call per step per page load for
    # something that changes about twice in a recipe's life.
    recipe = draft["recipe"]
    recipe.setdefault("mine", {})[slot] = name
    store.update_recipe(draft_id, recipe)

    # ALREADY IN THE LIBRARY? SEND IT ON NOW.
    #
    # Photos used to reach Mealie only at the moment you pressed Keep, which
    # made "go back and add a picture" quietly not work: the picture appeared
    # on the guide, looked saved, and the library still showed the scraped
    # one. Adding a photo to a kept recipe is a normal thing to do -- you cook
    # it, it comes out well, you photograph it -- and it is precisely the
    # photo most worth having, because it is yours and it is of the real dish.
    if draft.get("tandoor_id") and slot == "hero":
        await _push_own_photo_tandoor(draft_id, recipe, draft["tandoor_id"])
    elif draft.get("mealie_slug") and slot == "hero":
        await _push_own_photo(draft_id, recipe, draft["mealie_slug"])
    elif draft.get("tandoor_id") and slot.startswith("step-"):
        try:
            n = int(slot.removeprefix("step-"))
            p = photos.path(draft_id, name)
            ext = name.rsplit(".", 1)[-1]
            if p:
                await tandoor.push_own_step_photo(
                    draft["tandoor_id"], n, p.read_bytes(), ext)
        except (ValueError, OSError, tandoor.TandoorError) as e:
            print(f"kitchen: saved the step photo here but the library would not take it: {e}")
    elif draft.get("mealie_slug") and slot.startswith("step-"):
        try:
            n = int(slot.removeprefix("step-"))
            p = photos.path(draft_id, name)
            ext = name.rsplit(".", 1)[-1]
            if p:
                await mealie.push_own_step_photo(
                    draft["mealie_slug"], n, p.read_bytes(), ext)
        except (ValueError, OSError, mealie.MealieError) as e:
            print(f"kitchen: saved the step photo here but Mealie would not take it: {e}")

    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}#s{slot}", 303)


@app.post("/r/{draft_id}/photo/{slot}/remove")
async def drop_photo(request: Request, draft_id: str, slot: str):
    if (gate := _write_gate(request)):
        return gate
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    photos.remove(draft_id, slot)
    recipe = draft["recipe"]
    (recipe.get("mine") or {}).pop(slot, None)
    store.update_recipe(draft_id, recipe)

    # If the library is showing the photo you just deleted, put the original
    # back rather than leaving it there. Deleting something and having it stay
    # visible somewhere else is the kind of thing nobody thinks to check.
    if draft.get("tandoor_id") and slot == "hero" and recipe.get("image"):
        try:
            await tandoor.push_image_from_url(draft["tandoor_id"], recipe["image"])
        except tandoor.TandoorError as e:
            print(f"kitchen: removed the photo here but the library kept it: {e}")
    elif draft.get("mealie_slug") and slot == "hero" and recipe.get("image"):
        try:
            await mealie._post(mealie.IMAGE.format(slug=draft["mealie_slug"]),
                               {"url": recipe["image"], "includeTags": False},
                               timeout=45)
        except mealie.MealieError as e:
            print(f"kitchen: removed the photo here but the library kept it: {e}")
    elif draft.get("tandoor_id") and slot.startswith("step-"):
        try:
            n = int(slot.removeprefix("step-"))
            await tandoor.remove_step_file(draft["tandoor_id"], n)
        except (ValueError, tandoor.TandoorError) as e:
            print(f"kitchen: removed the step photo here but the library kept it: {e}")
    elif draft.get("mealie_slug") and slot.startswith("step-"):
        try:
            n = int(slot.removeprefix("step-"))
            await mealie.remove_own_step_photo(draft["mealie_slug"], n)
        except (ValueError, mealie.MealieError) as e:
            print(f"kitchen: removed the step photo here but the library kept it: {e}")
    # Removing yours puts back whatever was there before -- the scraped photo,
    # or the drawing. Nothing was destroyed to make room for yours.
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}#s{slot}", 303)


@app.post("/r/{draft_id}/reference")
async def add_reference(request: Request, draft_id: str,
                        slot: str = Form(...), url: str = Form(...)):
    """A picture Jason found online, held up as a reference for what a
    step (or the dish) should actually look like -- fed to the vision
    model when this step is next drawn, not shown as this recipe's own
    picture (see photos.save_reference's own docstring for why).
    """
    if not _is_owner(request):
        return _oops(request, "Jason manages reference pictures.", 403)
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    try:
        core.is_public_url(url)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": core.USER_AGENT,
                                                   **core.BROWSER_HEADERS})
            resp.raise_for_status()
        name = photos.save_reference(draft_id, slot, resp.content)
    except (core.Blocked, httpx.HTTPError, photos.PhotoError) as e:
        return _oops(request, f"Could not use that picture: {e}")
    recipe = draft["recipe"]
    recipe.setdefault("reference", {})[slot] = name
    store.update_recipe(draft_id, recipe)
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}#s{slot}", 303)


@app.post("/r/{draft_id}/reference/{slot}/remove")
async def drop_reference(request: Request, draft_id: str, slot: str):
    if not _is_owner(request):
        return _oops(request, "Jason manages reference pictures.", 403)
    draft = store.get(draft_id)
    if draft:
        photos.remove(draft_id, slot, reference=True)
        recipe = draft["recipe"]
        (recipe.get("reference") or {}).pop(slot, None)
        store.update_recipe(draft_id, recipe)
    return RedirectResponse(f"{core.ROOT_PATH}/r/{draft_id}#s{slot}", 303)


@app.get("/photo/{draft_id}/{name}")
async def serve_photo(draft_id: str, name: str):
    p = photos.path(draft_id, name)
    if not p:
        return PlainTextResponse("No such photo.", status_code=404)
    # Cached hard: the filename is per-slot and a re-upload REPLACES it, so a
    # long cache would show the old photo after a change. One hour is enough to
    # keep a guide snappy while you scroll it, and short enough that a swap
    # shows up before you have finished cooking.
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


# ---------------------------------------------------------------------------
#  Share links — a recipe, no account required
#
#  Jason's own request (2026-08-25): "share the links to a certain recipe
#  to a random person without a login with the share button." Everything
#  below is reached through /share/*, which has NO forward_auth in front
#  of it in the Caddyfile at all -- unlike every other route in this file,
#  which trusts Caddy to have already checked who is asking. These check
#  for themselves, on every request, because nobody else is going to.
#
#  WHAT A GUEST CAN SEE, AND WHY THAT IS SAFE
#  A valid, unexpired token (store.share_get()) unlocks exactly the one
#  recipe it was minted for -- title, ingredients, steps, tools, nutrition,
#  read only. No edit controls, no votes, no comments, no who-added-it, no
#  link back into /kitchen (which would just bounce them to a sign-in page
#  they have no account for).
#
#  PICTURES ARE THE ONE PART WORTH EXPLAINING. A step's own uploaded or
#  AI-drawn photo lives behind /photo/{draft_id}/{name}, which IS gated
#  normally (it is under /kitchen) -- so those are relayed through
#  /share/{token}/photo/draft/{name} below, scoped to THIS token's own
#  draft_id, which a guest cannot substitute (it comes from the token, not
#  from anything in the URL they control). Food/tool ICON pictures
#  (/food-image/{id}/..., /tool-image/{id}/...) are relayed too, but
#  deliberately NOT scoped to this one recipe's ingredients -- they are
#  generic, already-shared stock pictures ("olive oil", "a wooden spoon")
#  reused across the whole library by icon_map, not anything private to
#  one recipe or one person's kitchen. The thing worth keeping scoped is
#  RECIPE CONTENT and PERSONAL PHOTOS; a stray guess at another food's
#  icon id costs nothing worth guarding.
# ---------------------------------------------------------------------------
_SHARE_ICON_RE = [
    (re.compile(r"/food-image/([^/?]+)/([^/?]+)"), "food"),
    (re.compile(r"/tool-image/([^/?]+)/([^/?]+)"), "tool"),
]


def _share_relay_icon(url: str, token: str) -> str:
    """Rewrite an icon_map URL (baked with ROOT_PATH and a cache-buster
    already on it, see foodphotos.set()/toolimages.set()) to go through
    this token's own relay instead of straight through /kitchen, which a
    guest cannot pass.

    NOT core.ROOT_PATH here -- CAUGHT LIVE testing the very first real
    share link: /share/* is its own TOP-LEVEL block in the Caddyfile (see
    that block's own comment), not nested under /kitchen the way every
    other link this app writes is, so prepending ROOT_PATH produced
    /kitchen/share/... and every picture on the page 404'd through the
    gated block instead of the public one."""
    if not url:
        return url
    for pat, kind in _SHARE_ICON_RE:
        m = pat.search(url)
        if m:
            return f"/share/{token}/photo/{kind}/{m.group(1)}/{m.group(2)}"
    return url


async def _share_view(share: dict):
    """The same shape guide() builds for itself, trimmed to what a guest
    is shown. Returns None if the recipe behind the token is gone (kept
    recipe deleted from Tandoor since the link was made)."""
    draft = store.get_by_tandoor_id(share["tandoor_id"])
    if not draft:
        return None
    recipe = draft["recipe"]
    token = share["token"]
    # draft_url="" -- see the class comment above: this builds BARE
    # /photo/{draft_id}/{name} paths (no ROOT_PATH), which is exactly what
    # relay_draft() below expects to strip and re-wrap.
    view = importer.build_guide(recipe, draft_id=draft["id"], draft_url="")
    draft_prefix = f"/photo/{draft['id']}/"

    def relay_draft(url: str) -> str:
        if url and url.startswith(draft_prefix):
            # NOT core.ROOT_PATH -- see _share_relay_icon()'s own comment.
            return f"/share/{token}/photo/draft/{url[len(draft_prefix):]}"
        return url  # already an external URL (the original source page's own photo)

    for st in view["steps"]:
        st["photo"] = relay_draft(st["photo"])

    icon_map = await _get_icon_map()
    for st in view["steps"]:
        st["icons"] = [
            {**ic, "url": _share_relay_icon(ic.get("url"), token)}
            for ic in _icons_for_step(st["text"], icon_map)
        ]
    for item in view["ingredients"]:
        hit = _icons_for_step(item["text"], icon_map)
        item["icon"] = ({**hit[0], "url": _share_relay_icon(hit[0].get("url"), token)}
                        if hit else None)

    own_hero = (recipe.get("mine") or {}).get("hero")
    return {
        "view": view,
        "recipe": recipe,
        "title": share["title"],
        "hero": relay_draft(f"{draft_prefix}{own_hero}") if own_hero else recipe.get("image"),
        "expires": share["expires"],
    }


@app.get("/share/{token}", response_class=HTMLResponse)
async def shared_recipe(request: Request, token: str):
    share = store.share_get(token)
    if not share:
        return templates.TemplateResponse(request, "share_gone.html", {}, status_code=404)

    ctx = await _share_view(share)
    if not ctx:
        return templates.TemplateResponse(request, "share_gone.html", {}, status_code=404)

    recipe = ctx["recipe"]
    tandoor_recipe = await tandoor.get_recipe(share["tandoor_id"])
    tool_names = tandoor.tools_from(tandoor_recipe or {})
    icon_map = await _get_icon_map()
    tools_view = []
    for t in tool_names:
        if not t:
            continue
        hit = (icon_map or {}).get((t or "").strip().lower(), {})
        tools_view.append({"name": t, "url": _share_relay_icon(hit.get("url"), token)})

    nutrition_full = recipe.get("nutrition_full")
    nutrition_servings = recipe.get("nutrition_servings", 1)
    daily_values = (recipe_nutrition.daily_values(nutrition_full, nutrition_servings)
                    if nutrition_full else [])
    nutrition_extra = (recipe_nutrition.extra_amounts(nutrition_full, nutrition_servings)
                       if nutrition_full else [])
    expires_note = _dt.datetime.fromtimestamp(ctx["expires"]).strftime("on %b %-d at %-I:%M %p")

    return templates.TemplateResponse(request, "share.html", {
        "title": ctx["title"], "hero": ctx["hero"], "expires_note": expires_note,
        "tools": tools_view, "daily_values": daily_values,
        "nutrition_extra": nutrition_extra, **ctx["view"],
    })


@app.get("/share/{token}/photo/draft/{name}")
async def shared_draft_photo(token: str, name: str):
    """A step's own uploaded or drawn photo. NO item_id in this path --
    CAUGHT LIVE testing the first real share link: this used to share one
    route with the food/tool family below, which needs {kind}/{item_id}/
    {name} (3 segments); relay_draft() in _share_view() only ever emits
    {name} (2 segments) for this family on purpose (the draft is fixed by
    the token, not by anything in the URL), so the combined route simply
    never matched a draft photo's URL at all -- a 404 from FastAPI's own
    catch-all, not from the "no such picture" check either route makes."""
    share = store.share_get(token)
    if not share:
        return PlainTextResponse("This link has expired.", status_code=404)
    draft = store.get_by_tandoor_id(share["tandoor_id"])
    p = photos.path(draft["id"], name) if draft else None
    if not p:
        return PlainTextResponse("No such picture.", status_code=404)
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/share/{token}/photo/{kind}/{item_id}/{name}")
async def shared_item_photo(token: str, kind: str, item_id: str, name: str):
    share = store.share_get(token)
    if not share:
        return PlainTextResponse("This link has expired.", status_code=404)
    if kind == "food":
        p = foodphotos.path(item_id, name)
    elif kind == "tool":
        p = toolimages.path(item_id, name)
    else:
        p = None
    if not p:
        return PlainTextResponse("No such picture.", status_code=404)
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})


# ---------------------------------------------------------------------------
#  The library — signed in without a second password
# ---------------------------------------------------------------------------
@app.get("/library")
async def library(request: Request, next: str = ""):
    """Hand this person a Tandoor session and send them to it.

    Entered two ways: the "The library" link on every page here, and Caddy
    bouncing anybody who lands on port 9445 without a Tandoor cookie (see the
    :9445 block in web/Caddyfile). Either way the person never types a
    Tandoor password, because one was never made for them to type.

    THIS USED TO SEND PEOPLE TO MEALIE. Mealie is stopped now (data intact,
    not deleted) and this is the Tandoor equivalent -- see
    handoff_tandoor.py's own docstring for what changed underneath. Anyone
    still holding an old bookmark to :9443 finds Mealie simply not
    listening; nothing here needs to handle that specially.

    Same read-only rule as before: mints a session only for people
    people.can_write() allows, on the same reasoning as the old Mealie
    version of this route (Tandoor has no read-only role either, so a
    read-only visitor is never handed a session at all -- everything they
    can see, this app already shows them on its own pages).
    """
    email = _who(request)
    if not email:
        return _oops(request, "This page is only reachable through the "
                              "website's sign-in.", 401)
    if not people.can_write(email):
        return _page(request, "no_library.html")
    if not core.TANDOOR_PUBLIC:
        return _oops(request, "KITCHEN_TANDOOR_PUBLIC is not set, so I do not "
                              "know what address to send you to.")
    try:
        session = await handoff_tandoor.mint(email)
    except handoff_tandoor.HandoffError as e:
        return _oops(request, f"{e}\n\nYou can still reach the library by "
                              "signing in to it directly, but that is the "
                              "thing this page exists to avoid.")

    # `next` comes from Caddy as the path the person actually asked for, so a
    # deep link to one recipe survives the round trip instead of dumping them
    # on the home page. Only a PATH is honoured -- anything with a scheme or a
    # host in it would make this an open redirect, and an open redirect on a
    # page that hands out session cookies is worth being careful about.
    dest = core.TANDOOR_PUBLIC
    if next.startswith("/") and not next.startswith("//"):
        dest += next

    response = RedirectResponse(dest, status_code=303)
    response.headers["Set-Cookie"] = handoff_tandoor.cookie(session)
    return response


@app.post("/r/{draft_id}/swap", response_class=HTMLResponse)
async def swap(request: Request, draft_id: str,
               ingredient: str = Form(...), why: str = Form("")):
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    try:
        answer = await subs.suggest(ingredient.strip(),
                                    dish=draft["recipe"].get("title", ""),
                                    why=why.strip())
    except llm.LLMError as e:
        return _oops(request, f"{e}\n\nNothing in the substitutions table "
                              "covers that one, so it needed the model.")
    return _page(request, "swap.html", answer=answer, ingredient=ingredient,
                 draft=draft, id=draft_id)


@app.post("/r/{draft_id}/swap-amount", response_class=HTMLResponse)
async def swap_amount(request: Request, draft_id: str,
                      ingredient: str = Form(...), have: str = Form(...)):
    """"I have {have}, the recipe wants {ingredient} -- how much do I
    actually need." Different from /swap: that one suggests WHAT to use;
    this one converts a substitute you already named to an exact amount,
    given the recipe's own line (with its quantity) rather than a bare
    ingredient name. See subs.convert()."""
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    try:
        answer = await subs.convert(ingredient.strip(), have.strip(),
                                    dish=draft["recipe"].get("title", ""))
    except llm.LLMError as e:
        return _oops(request, f"{e}\n\nWorking out an exact amount needs "
                              "the model, and it did not answer.")
    return _page(request, "swap_amount.html", answer=answer,
                ingredient=ingredient, have=have, draft=draft, id=draft_id)


@app.post("/r/{draft_id}/swap-equipment", response_class=HTMLResponse)
async def swap_equipment(request: Request, draft_id: str, item: str = Form(...)):
    draft = store.get(draft_id)
    if not draft:
        return _oops(request, "That recipe is not here any more.", 404)
    try:
        answer = await subs.suggest_equipment(item.strip(),
                                              dish=draft["recipe"].get("title", ""))
    except llm.LLMError as e:
        return _oops(request, f"{e}\n\nNothing in the equipment table covers "
                              "that one, so it needed the model.")
    return _page(request, "swap.html", answer=answer, ingredient=item,
                 draft=draft, id=draft_id)


# ---------------------------------------------------------------------------
#  Ideas
# ---------------------------------------------------------------------------
IDEAS_SYSTEM = """You suggest things to cook. You are talking to someone
standing in their kitchen deciding what to make, not writing a magazine.

Rules:
- Suggest four dishes, no more.
- For each: the name, one sentence on why it fits what they asked, and the
  handful of ingredients that actually define it.
- Prefer things that can be made from what they said they have. If something
  needs one thing they did not mention, say which one thing.
- Real, ordinary dishes. Do not invent a fusion nobody has cooked.
- No preamble, no sign-off, no mention of being an AI."""

IDEAS_SHAPE = """
Return JSON: {"ideas": [{"name": "", "why": "", "key_ingredients": ["", ""],
"needs": ""}]}
"""


@app.get("/ideas", response_class=HTMLResponse)
async def ideas_form(request: Request):
    return _page(request, "ideas.html", ideas=None, asked="")


@app.post("/ideas", response_class=HTMLResponse)
async def ideas(request: Request, have: str = Form(""), mood: str = Form("")):
    asked = " ".join(p for p in (have.strip(), mood.strip()) if p)
    if not asked:
        return _page(request, "ideas.html", ideas=None, asked="",
                     err="Tell me what you have in, or what you fancy.")

    ask = ""
    if have.strip():
        ask += f"What I have in: {have.strip()}\n"
    if mood.strip():
        ask += f"What I fancy: {mood.strip()}\n"

    try:
        data = await llm.chat_json(IDEAS_SYSTEM + IDEAS_SHAPE, ask,
                                   model=core.LLM_TALK, temperature=0.7,
                                   max_tokens=900)
    except llm.LLMError as e:
        return _page(request, "ideas.html", ideas=None, asked=asked, err=str(e))

    found = [i for i in (data.get("ideas") or []) if isinstance(i, dict)][:4]
    return _page(request, "ideas.html", ideas=found, asked=asked)


# ---------------------------------------------------------------------------
#  Template helpers
# ---------------------------------------------------------------------------
def _servings_options(now: int | None) -> list:
    """The portion buttons. Built around what the recipe actually serves."""
    base = now or 4
    out = sorted({1, 2, base, base * 2, base // 2 or 1, 4, 6, 8})
    return [n for n in out if 0 < n <= 24]


templates.env.globals["servings_options"] = _servings_options
templates.env.globals["render_qty"] = scale.render
templates.env.filters["frac"] = lambda f: scale.render(Fraction(f))
