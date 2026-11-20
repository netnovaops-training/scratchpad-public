"""Handing a finished recipe to Mealie.

WHY THIS IS A THIN CLIENT AND NOT AN INTEGRATION
------------------------------------------------
Mealie is the library and this app is the front door. The front door should
know as little about the library's insides as it can get away with, because
Mealie ships new versions regularly and every endpoint this file knows about is
a thing that can break on an update.

So it uses three calls and no more:

    POST   /api/recipes/create/url   let Mealie scrape it itself
    POST   /api/recipes              create an empty recipe, get a slug
    PATCH  /api/recipes/{slug}       fill it in with what we parsed

IF AN UPDATE EVER BREAKS THIS, the running container documents itself: open
https://example.org:9443/docs and the real, current API is listed
there. That is a two-minute fix, and it is why the paths are constants at the
top of this file rather than scattered through it.

TWO ROUTES IN, ON PURPOSE
-------------------------
`from_url` asks Mealie to scrape the page itself. When it works it is the best
possible import, because Mealie's scraper knows hundreds of sites specifically
and keeps the ingredient units structured. It is tried FIRST for exactly that
reason.

`from_recipe` pushes what THIS app parsed. That is the path for the pages
Mealie's scraper does not handle, for anything typed into the paste box, and
for anything the model had to work out. Neither route is a fallback for a
broken other one -- they are for different kinds of page.

WHY FAILURES ARE NOT FATAL
--------------------------
Mealie being down must not stop the guide from rendering. You imported a
recipe, you are standing in the kitchen, and whether it also got filed in the
library is not the urgent part. Everything here raises MealieError, and main.py
catches it and shows the guide anyway with a note saying it was not saved.
"""
import asyncio
import json
import re
import uuid
from urllib.parse import quote

import httpx

from . import core, labels, nutrition, recipe_nutrition, store

CREATE_FROM_URL = "/api/recipes/create/url"
CREATE = "/api/recipes"
DETAIL = "/api/recipes/{slug}"
IMAGE = "/api/recipes/{slug}/image"
ASSETS = "/api/recipes/{slug}/assets"


async def get_recipe(slug: str) -> dict | None:
    """The full recipe as Mealie has it right now, or None. For anything
    that needs to know what Mealie's OWN record says rather than
    Kitchen's local copy -- CAUGHT LIVE: a recipe's `tools` list can be
    added or edited straight in Mealie's own UI, which never writes back
    to Kitchen's draft, so guide.html's Tools section reads from here,
    not from the draft, or it would show nothing for exactly the
    recipes most likely to actually have a tools list."""
    if not slug:
        return None
    try:
        return (await _get(DETAIL.format(slug=slug))).json()
    except MealieError:
        return None

# Used by the sign-in hand-off and the people mirror. Same reasoning as above:
# constants, so an upstream rename is one edit against /docs rather than a hunt.
USERS = "/api/admin/users"
USER = "/api/admin/users/{uid}"
TOKEN = "/api/auth/token"
# Two calls, because Mealie has no "set this user's password" endpoint and
# ignores a password field on the user PUT. See _reset_to() in handoff.py.
RESET_TOKEN = "/api/admin/users/password-reset-token"
RESET_PASSWORD = "/api/users/reset-password"
HOUSEHOLDS = "/api/admin/households"
GROUPS = "/api/admin/groups"

# WHERE THE LIBRARY LIVES, and where everybody else lives.
#
# Mealie has no read-only role -- I checked, and it matters: a non-admin in the
# SAME household renamed one of the owner's recipes and it stuck. Delete was
# refused; edit was not. So "the owner has the final say" cannot be done with
# permissions on a user, only with the wall between households, which Mealie
# does enforce (lockRecipeEditsFromOtherHouseholds, on by default).
#
# Owners live in the household holding the library. Everyone else lives next
# door, can read all of it, and cannot change any of it. What they CAN do is
# make recipes of their own in their own household -- those never touch the
# library, and the way anything gets in is the owner approving it in /kitchen.
OWNER_HOUSEHOLD = "Family"
GUEST_HOUSEHOLD = "Guests"


class MealieError(Exception):
    """Mealie refused or could not be reached. Never fatal to a page render."""


def configured() -> bool:
    return bool(core.MEALIE_TOKEN)


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {core.MEALIE_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def public_link(slug: str) -> str:
    """The address YOUR BROWSER can open, which is not the one we talk to.

    core.MEALIE_URL is http://mealie:9000 -- a Docker network name. Putting
    that in a link produces a page that works for the container and 'site
    cannot be reached' for you, which is a confusing five minutes the first
    time it happens.
    """
    if not core.MEALIE_PUBLIC:
        return ""
    return f"{core.MEALIE_PUBLIC}/g/home/r/{slug}"


async def _post(path: str, payload: dict, *, timeout: float = 60):
    if not configured():
        raise MealieError(
            "No Mealie API token is set. Make one in Mealie under your profile "
            "→ API Tokens, and put it in .env as KITCHEN_MEALIE_TOKEN."
        )
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(core.MEALIE_URL + path, json=payload,
                                  headers=_headers())
    except httpx.RequestError as e:
        raise MealieError(f"Could not reach Mealie ({e.__class__.__name__}). "
                          "Is the container running?")
    if r.status_code == 401:
        raise MealieError("Mealie rejected the API token. Make a new one in "
                          "Mealie under your profile → API Tokens.")
    if r.status_code >= 400:
        raise MealieError(f"Mealie said {r.status_code}: {r.text[:200]}")
    return r


async def _post_form(path: str, form: dict, *, timeout: float = 30):
    """Form-encoded, and WITHOUT the admin token.

    Mealie's /api/auth/token is an OAuth2 password form: it wants
    application/x-www-form-urlencoded, not JSON, and it is a sign-in rather
    than an authenticated call. Sending the admin bearer alongside it would be
    asking "who am I" while also saying "I am the admin", which is at best
    confusing and at worst answers with the wrong session.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(core.MEALIE_URL + path, data=form,
                                  headers={"Accept": "application/json"})
    except httpx.RequestError as e:
        raise MealieError(f"Could not reach Mealie ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise MealieError(f"Mealie refused the sign-in ({r.status_code}).")
    return r


async def _get(path: str, *, timeout: float = 30):
    if not configured():
        raise MealieError("No Mealie API token is set.")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(core.MEALIE_URL + path, headers=_headers())
    except httpx.RequestError as e:
        raise MealieError(f"Could not reach Mealie ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise MealieError(f"Mealie said {r.status_code}: {r.text[:200]}")
    return r


async def _put(path: str, payload: dict, *, timeout: float = 60):
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.put(core.MEALIE_URL + path, json=payload,
                                 headers=_headers())
    except httpx.RequestError as e:
        raise MealieError(f"Could not reach Mealie ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise MealieError(f"Mealie said {r.status_code}: {r.text[:200]}")
    return r


async def _patch(path: str, payload: dict, *, timeout: float = 60):
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.patch(core.MEALIE_URL + path, json=payload,
                                   headers=_headers())
    except httpx.RequestError as e:
        raise MealieError(f"Could not reach Mealie ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise MealieError(f"Mealie said {r.status_code}: {r.text[:200]}")
    return r


async def _delete(path: str, *, timeout: float = 30):
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.delete(core.MEALIE_URL + path, headers=_headers())
    except httpx.RequestError as e:
        raise MealieError(f"Could not reach Mealie ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise MealieError(f"Mealie said {r.status_code}: {r.text[:200]}")
    return r


def _slug_from(response) -> str:
    """Mealie answers a create with the slug.

    It has historically answered as a bare JSON string ("chicken-pie") and as
    an object with a slug in it. Handle both rather than betting on one -- the
    cost is four lines, the cost of being wrong is that Keep silently files
    everything under a slug of "None".
    """
    try:
        data = response.json()
    except ValueError:
        return response.text.strip().strip('"')
    if isinstance(data, str):
        return data
    if isinstance(data, dict):
        return data.get("slug") or data.get("id") or ""
    return ""


async def from_url(url: str, recipe: dict) -> str:
    """Ask Mealie to scrape the page itself. Returns the new recipe's slug.

    THIS USED TO STOP HERE, and it was the whole reason 207 recipes went in
    with no tags. Mealie's own scraper is the best source for ingredients and
    instructions (see the module docstring for why it's tried first), but it
    knows nothing about labels.py/taxonomy.py's work -- so once the scrape
    lands, this follows up with ONE small PATCH carrying only what Mealie's
    scraper cannot know: our tags and category. It does not touch
    recipeIngredient/recipeInstructions, so a good scrape is never clobbered.
    """
    r = await _post(CREATE_FROM_URL, {"url": url, "includeTags": False},
                    timeout=120)   # Mealie fetches the page, so this is slow
    slug = _slug_from(r)
    if not slug:
        raise MealieError("Mealie accepted the link but did not say where it "
                          "put the recipe.")
    try:
        payload = await _finalize_organizers({
            "tags": _tags_for(recipe),
            "recipeCategory": _category_for(recipe),
            "tools": _tools_for(recipe),
        })
        await _patch(DETAIL.format(slug=slug), payload)
    except MealieError:
        # The recipe itself is already safely filed -- missing tags on this
        # one is a cosmetic problem, not a reason to make the whole import
        # look like it failed.
        pass
    return slug


def _slugify(name: str) -> str:
    """Mealie's own slug shape: lowercase, spaces and punctuation to
    hyphens. CAUGHT LIVE: `slug` is required on every tag/category object
    now, not just `name` -- a stricter check than an older comment here
    claimed, most likely an upstream version change.

    CAUGHT LIVE, THE HARD WAY: matching is NOT purely by name the way an
    earlier version of this comment claimed. Mealie's tag/category/tool
    slug is unique regardless of name casing, but name matching is
    case-SENSITIVE -- so sending {"name": "dinner", "slug": "dinner"} when
    Mealie already has a tag named "Dinner" (same slug) is a slug
    collision on CREATE, reported as a generic, misleading "Recipe already
    exists" 400. See _resolve_tag_like() below, which is the real fix:
    look up what already exists and reuse ITS name, rather than trust that
    slug-matching alone makes this safe."""
    s = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return s or "tag"


def _tags_for(recipe: dict) -> list:
    """The pills this app already worked out, as Mealie tags.

    THESE USED TO BE THROWN AWAY AT THE DOOR. Every import runs labels.py and
    taxonomy.py, works out "chicken", "quick", "soul food", "dinner" -- and
    then sent Mealie a recipe with no tags at all. 207 recipes in and the tag
    list was empty, which is why filtering by anything in the library returned
    nothing.

    Course and cuisine are included as tags as well as their own fields, so
    that "show me the Southern ones" works from the tag rail the same way the
    labels do. Duplicating them is cheap; having two places to look is not.
    """
    seen, out = set(), []
    for name in (list(recipe.get("labels") or [])
                 + [recipe.get("course", ""), recipe.get("cuisine", "")]):
        name = (name or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        # Mealie matches an existing tag by NAME and creates the rest, so
        # this slug only ever matters the first time a given tag is used.
        out.append({"name": name, "slug": _slugify(name)})
    return out


def _category_for(recipe: dict) -> list:
    """Mealie's `recipeCategory` -- a separate field from tags, shown as its
    own filter rail in the UI. Course only (Dinner, Dessert, ...), which is
    what "category" means in Mealie's own vocabulary. Cuisine and the rest
    stay tag-only; course is deliberately in both places, same reasoning as
    _tags_for()'s own docstring."""
    name = (recipe.get("course") or "").strip()
    return [{"name": name, "slug": _slugify(name)}] if name else []


def _tools_for(recipe: dict) -> list:
    """Mealie's `tools` -- equipment, not ingredients. Whatever crawl.py
    pulled from the source page's own schema.org `tool` field (see
    _tools_from_ld() there); most sites don't publish this at all, and an
    empty list is the honest answer when they don't, not a reason to guess."""
    seen, out = [], set()
    for name in recipe.get("tools") or []:
        name = (name or "").strip()
        key = name.lower()
        if not name or key in out:
            continue
        out.add(key)
        seen.append({"name": name, "slug": _slugify(name)})
    return seen


_ORGANIZER_ENDPOINT = {
    "tags": "/api/organizers/tags",
    "categories": "/api/organizers/categories",
    "tools": "/api/organizers/tools",
}


async def _resolve_tag_like(kind: str, candidates: list) -> list:
    """Each {name, slug} in `candidates`, upgraded to the FULL existing
    record (with its real `id`) if Mealie already has one with that slug.

    THE REAL FIX, caught live and confirmed by hand: this app's own
    long-standing assumption -- "Mealie matches an existing tag by name and
    creates the rest" -- is wrong for this Mealie version. Sending
    {"name": "soup", "slug": "soup"} with NO `id`, even when a tag named
    exactly "soup" with slug "soup" already exists, makes Mealie try to
    CREATE a new one anyway -- which collides with the one already there
    and 400s as a baffling "Recipe already exists". Sending the existing
    record's real `id` alongside its name/slug is what actually attaches
    it instead of recreating it.

    A candidate with no existing match is a genuinely new tag/category/
    tool. For "tags" and "categories" that is left exactly as it was -- no
    `id` -- because Mealie DOES create-and-attach those two on its own
    given just a name and slug (checked directly, working). "tools" is
    different AGAIN: CAUGHT LIVE, its RecipeTool-Input schema requires
    `id` unconditionally, even for something brand new -- so a genuinely
    new tool is created with its own POST first, here, to get a real id
    before it is ever attached to a recipe.
    """
    if not candidates:
        return []
    endpoint = _ORGANIZER_ENDPOINT[kind]
    existing = {}
    page = 1
    while True:
        r = await _get(f"{endpoint}?perPage=100&page={page}")
        items = r.json().get("items") or []
        for it in items:
            existing[(it.get("slug") or "").lower()] = it
        if len(items) < 100:
            break
        page += 1
    out = []
    for c in candidates:
        found = existing.get(c["slug"].lower())
        if found:
            out.append({"id": found["id"], "name": found["name"], "slug": found["slug"]})
        elif kind == "tools":
            created = (await _post(endpoint, {"name": c["name"]})).json()
            existing[created["slug"].lower()] = created  # so a duplicate in
            # the SAME candidate list reuses this one instead of double-POSTing
            out.append({"id": created["id"], "name": created["name"],
                       "slug": created["slug"]})
        else:
            out.append(c)
    return out


async def _finalize_organizers(payload: dict) -> dict:
    """Run _resolve_tag_like() over whichever of tags/recipeCategory/tools
    are present in a payload about to be sent to Mealie. One place both
    from_url()'s follow-up patch and from_recipe()'s full patch route
    through, so neither can forget the fix above."""
    if "tags" in payload:
        payload["tags"] = await _resolve_tag_like("tags", payload["tags"])
    if "recipeCategory" in payload:
        payload["recipeCategory"] = await _resolve_tag_like("categories", payload["recipeCategory"])
    if "tools" in payload:
        payload["tools"] = await _resolve_tag_like("tools", payload["tools"])
    return payload


def _to_mealie(recipe: dict) -> dict:
    """Our shape -> Mealie's shape.

    INGREDIENTS GO IN AS TEXT AND ARE PARSED AFTERWARDS. Mealie's own parser
    is better than anything this app would do in a hurry, and splitting
    "1 1/2 cups flour" here into fields Mealie is about to re-derive is
    duplicated work that can only disagree.

    But "afterwards" has to actually happen. It never did: the text went in
    whole and nothing ever called the parser, so `food` stayed None on every
    ingredient of every recipe and the Foods list stayed empty. `originalText`
    is set here so that when the parser does run, the words somebody actually
    typed survive next to whatever it made of them.

    Run the parse with:  mealie_fix.py --backfill
    """
    return {
        "name": recipe.get("title") or "Untitled",
        "description": recipe.get("notes", ""),
        "recipeYield": recipe.get("yields", ""),
        "prepTime": recipe.get("prep", ""),
        "cookTime": recipe.get("cook", ""),
        "totalTime": recipe.get("total", ""),
        "orgURL": recipe.get("source", ""),
        "tags": _tags_for(recipe),
        "recipeCategory": _category_for(recipe),
        "tools": _tools_for(recipe),
        "recipeIngredient": [
            {"note": line, "originalText": line}
            for line in recipe.get("ingredients", [])
        ],
        "recipeInstructions": [{"text": step} for step in recipe.get("steps", [])],
    }


async def from_recipe(recipe: dict) -> str:
    """Push what we parsed. Create, then fill, then attach the photo."""
    r = await _post(CREATE, {"name": recipe.get("title") or "Untitled"})
    slug = _slug_from(r)
    if not slug:
        raise MealieError("Mealie created the recipe but did not return a slug.")

    await _patch(DETAIL.format(slug=slug), await _finalize_organizers(_to_mealie(recipe)))

    if recipe.get("image"):
        # Best effort, and quiet about it. A missing photo is a cosmetic
        # problem; failing the whole save because of one is not a trade worth
        # making when the ingredients and method are already safely filed.
        try:
            await _post(IMAGE.format(slug=slug),
                        {"url": recipe["image"], "includeTags": False},
                        timeout=45)
        except MealieError:
            pass
    return slug


async def put_image(slug: str, file_path, filename: str = "image.jpg"):
    """Upload a photo FILE as the recipe's image.

    PUT, not POST. The POST on the same path takes {"url": ...} and makes
    Mealie fetch it; the PUT takes multipart and is the only one that accepts
    bytes we already hold. Getting these the wrong way round produces a 422
    that reads like a malformed body rather than the wrong verb.
    """
    try:
        with open(file_path, "rb") as fh:
            files = {"image": (filename, fh, "application/octet-stream")}
            data = {"extension": filename.rsplit(".", 1)[-1].lower()}
            async with httpx.AsyncClient(timeout=90) as client:
                r = await client.put(
                    core.MEALIE_URL + IMAGE.format(slug=slug),
                    files=files, data=data,
                    headers={"Authorization": f"Bearer {core.MEALIE_TOKEN}"})
    except OSError as e:
        raise MealieError(f"Could not read the photo back off disk ({e}).")
    except httpx.RequestError as e:
        raise MealieError(f"Could not reach Mealie ({e.__class__.__name__}).")
    if r.status_code >= 400:
        raise MealieError(f"Mealie would not take the photo ({r.status_code}).")
    return r


PARSE = "/api/parser/ingredients"
FOODS = "/api/foods"
UNITS = "/api/units"


async def all_foods() -> list[dict]:
    """Every food Mealie knows about, however many pages that takes.
    CAUGHT LIVE: the pagination field is `total_pages`, snake_case, not the
    `totalPages` every other Mealie response you would expect from the
    camelCase shape everywhere else uses -- checking the wrong key just
    silently stops after page one instead of erroring, which is the kind
    of bug that looks like "it worked" until someone counts the results."""
    out: list[dict] = []
    page = 1
    while True:
        data = (await _get(f"{FOODS}?page={page}&perPage=100")).json()
        out.extend(data.get("items") or [])
        if page >= (data.get("total_pages") or 1):
            break
        page += 1
    return out


async def get_food(food_id: str) -> dict | None:
    """One food by id, or None if Mealie does not have it (or answers with
    anything other than 2xx/404) -- used by the food-picture routes, which
    need the FULL food object before they can PUT extras back onto it."""
    try:
        return (await _get(f"{FOODS}/{food_id}")).json()
    except MealieError:
        return None


TOOLS = "/api/organizers/tools"


async def all_tools() -> list[dict]:
    """Every tool organizer Mealie knows about -- Dutch oven, stand mixer,
    the works. An organizer list is household-sized (tens, not hundreds),
    so unlike all_foods() there is no pagination to walk; still tolerant of
    either a plain list or a {"items": [...]} wrapper, since Mealie is not
    consistent about that shape across its own endpoints."""
    data = (await _get(TOOLS)).json()
    if isinstance(data, dict):
        return data.get("items") or []
    return data or []


async def get_tool(tool_id: str) -> dict | None:
    """One tool by id, or None. Same reasoning as get_food -- the picture
    routes need the name even though the reference itself is saved in
    toolimages.py, not on Mealie."""
    try:
        return (await _get(f"{TOOLS}/{tool_id}")).json()
    except MealieError:
        return None


async def _find_or_create(kind: str, name: str) -> str:
    """A food or unit's id, by name -- reusing an existing one if there is an
    exact match rather than growing duplicates every time this runs. `kind`
    is "foods" or "units". Same shape as mealie_fix.py's ensure(), but
    single-lookup rather than preloading the whole table: this runs once per
    freshly-kept recipe, not across 200 of them in a batch.

    BEST EFFORT -- returns "" on any failure rather than raising. CAUGHT
    LIVE: a garbage unit name like "." (brute's parse of "a drizzle of
    olive oil" has nothing better to call the unit) hit a 409 -- Mealie's
    own "UNIQUE constraint failed" on a name that slugifies to nothing --
    and because this used to let that propagate, ONE bad line's unit
    silently threw away every other line's already-correct parse for the
    whole recipe (parse_ingredients()'s outer try/except caught it and
    skipped the save entirely). A recipe missing a food/unit link on one
    odd line is a cosmetic gap; a recipe that silently never got its fix
    at all, every single time, because of that one line, is the actual
    bug this was."""
    name = (name or "").strip()
    if not name:
        return ""
    try:
        r = await _get(f"/api/{kind}?search={quote(name)}&perPage=5")
        for item in r.json().get("items") or []:
            if (item.get("name") or "").strip().lower() == name.lower():
                return item.get("id") or ""
        r2 = await _post(f"/api/{kind}", {"name": name})
        return r2.json().get("id") or ""
    except MealieError:
        return ""


# Mealie's parser sometimes returns a technically-successful but unreadable
# note -- a leading comma left over from a parenthetical the source site
# wrote as "(, about 5 ounces each, trimmed)", or a doubled comma. CAUGHT
# LIVE: 474 ingredient notes across 175 recipes had one of these, worst on
# lines with no clear leading quantity ("Kosher salt and freshly ground
# black pepper, to taste") -- exactly what the steps-mining backfill
# (crawl.fill_ingredients_from_steps) produces, so the two features made
# each other's output worse together in a way neither would alone.
_GARBLED_NOTE_RE = re.compile(r"^\s*,|,\s*,")
# A note starting with "or" is usually FINE, not garbled -- "1 lb. ground
# chicken or turkey" correctly parses to food "ground chicken", note "or
# ground turkey", because the source line really does say "or". CAUGHT
# LIVE (second pass): an earlier version of this check flagged every
# leading "or" as broken and it went and "fixed" a few hundred perfectly
# good notes for no reason. Only a leading "or" the ORIGINAL LINE cannot
# account for -- no "or" anywhere in it -- means the parser invented the
# word, which is the real failure this is trying to catch.
_LEADING_OR_RE = re.compile(r"^\s*or\b", re.I)
_HAS_OR_RE = re.compile(r"\bor\b", re.I)


def _garbled(note: str, original: str = "") -> bool:
    note = note or ""
    if _GARBLED_NOTE_RE.search(note):
        return True
    if _LEADING_OR_RE.match(note) and not _HAS_OR_RE.search(original or ""):
        return True
    return False


# Mealie's parser can hand back a technically-successful FOOD name that is
# not actually a food -- a leading parenthetical unit conversion it never
# stripped ("(2 1/2 sticks) unsalted butter", "(320 g.) all-purpose
# flour"), or a leading bare quantity it left glued on. CAUGHT LIVE
# checking a real recipe that came back completely unchanged from a
# fresh re-parse: "6 tbsp unsalted butter" and "28-ounce can whole
# peeled tomatoes in puree" both parse CLEANLY on their own (confirmed
# directly against Mealie's own /api/parser/ingredients) -- so a stale
# bad food record from before this file's own parsing got better is the
# common case and a fresh reparse alone fixes it. The one pattern that
# is STILL reproducibly wrong today is a food name with a leading
# parenthetical group MEALIE'S OWN PARSER never recognised as a unit
# aside -- "1 1/4 c. (2 1/2 sticks) unsalted butter" correctly gets
# quantity=1.25, unit="c.", but "food" comes back as "(2 1/2 sticks)
# unsalted butter" instead of just "unsalted butter". Mechanical and
# safe to fix by hand: the leading "(...)" is a real, meaningful unit
# note (a stick count, a metric equivalent) that belongs in the note
# field, not deleted -- just moved to where it belongs.
_LEADING_PAREN_RE = re.compile(r"^\s*(\([^()]*\))\s*")
_FOOD_NAME_BAD_RE = re.compile(r"^\s*[\(\+]|^\s*\d")


def _split_leading_paren_from_food(food_name: str, note: str) -> tuple[str, str]:
    m = _LEADING_PAREN_RE.match(food_name or "")
    if not m:
        return food_name, note
    rest = food_name[m.end():].strip()
    if not rest:
        return food_name, note  # the parenthetical WAS the whole name -- nothing to split
    new_note = (m.group(1) + (", " + note if note else "")).strip()
    return rest, new_note


def _food_name_bad(name: str) -> bool:
    name = (name or "").strip()
    if not name:
        return False
    if _FOOD_NAME_BAD_RE.match(name):
        return True
    return name.count("(") != name.count(")")


async def parse_ingredients(slug: str) -> None:
    """Turn the plain-text ingredient lines Mealie just imported into
    structured foods and units, same day, not left for a manual --backfill
    run. 213 recipes went in with an empty Foods list before this existed --
    see mealie_fix.py's own docstring. Best effort: never raises, because a
    recipe that saved fine but stayed unparsed is a much smaller problem than
    one that looks like it failed to save at all.

    Tries BOTH parsers for every line (one batch call each, not one call
    per line) and picks whichever came back readable -- checking the NOTE
    (see _garbled) AND the FOOD NAME (see _food_name_bad) both, "nlp"
    first since it is usually the better one, "brute" if that one looks
    bad on either count. Before giving up on a candidate whose food name
    starts with a leading parenthetical, _split_leading_paren_from_food()
    gets a chance to rescue it -- that one specific shape is fixable, not
    actually bad. If nothing usable comes out of either parser even after
    that, the line is left as plain text: no food, no unit, just the note
    Mealie already shows for any not-yet-parsed ingredient. A readable
    line with no structure beats a "structured" one nobody can read, and
    the food/unit linking on a "salt, to taste" line was never carrying
    much weight anyway.
    """
    try:
        full = (await _get(DETAIL.format(slug=slug))).json()
        ings = full.get("recipeIngredient") or []
        lines = [(i.get("originalText") or i.get("note") or "").strip() for i in ings]
        lines = [l for l in lines if l]
        if not lines:
            return

        results = {}
        for parser in ("nlp", "brute"):
            try:
                results[parser] = (await _post(PARSE, {"parser": parser, "ingredients": lines})).json()
            except MealieError:
                results[parser] = None
        if not results.get("nlp") and not results.get("brute"):
            return

        out = []
        for i, original in enumerate(lines):
            chosen = None
            chosen_food = ""
            chosen_note = ""
            for parser in ("nlp", "brute"):
                r = results.get(parser)
                if not r or i >= len(r):
                    continue
                ing = r[i].get("ingredient") or {}
                note = ing.get("note") or ""
                food_name = (ing.get("food") or {}).get("name", "")
                if _garbled(note, original):
                    continue
                food_name, note = _split_leading_paren_from_food(food_name, note)
                if _food_name_bad(food_name):
                    continue
                chosen, chosen_food, chosen_note = r[i], food_name, note
                break

            if chosen is None:
                # Neither parser produced something readable -- plain text,
                # same shape a freshly-imported, not-yet-parsed line has.
                out.append({"note": original, "originalText": original})
                continue

            ing = chosen.get("ingredient") or {}
            unit_name = (ing.get("unit") or {}).get("name", "")
            entry = {
                "quantity": ing.get("quantity") or 0,
                "note": chosen_note,
                "originalText": original,
            }
            food_id = await _find_or_create("foods", chosen_food)
            unit_id = await _find_or_create("units", unit_name)
            if food_id:
                entry["food"] = {"id": food_id, "name": chosen_food}
            if unit_id:
                entry["unit"] = {"id": unit_id, "name": unit_name}
            out.append(entry)

        await _patch(DETAIL.format(slug=slug), {"recipeIngredient": out})
    except MealieError:
        pass


async def _push_step_asset(slug: str, step_index: int, file_bytes: bytes,
                          filename: str, mime: str, asset_name: str,
                          embed_markdown_for: callable,
                          strip_re: "re.Pattern | None" = None) -> bool:
    """Upload one file as a Mealie recipe asset for a specific step, and
    reference it in that step's `summary` field. RecipeStep has no image
    field of its own (checked directly against the schema), so an asset plus
    a markdown embed in `summary` is the only place a per-step picture can go
    without inventing a field Mealie does not have.

    Shared by push_step_illustration (a picture Kitchen drew), push_step_photo
    (a picture the source page already had) and push_own_step_photo (yours)
    -- same upload, same embed, different bytes and different caption.
    `embed_markdown_for` takes the asset's URL and returns the markdown to
    append, so each caller keeps its own wording without duplicating the
    upload/patch mechanics.

    `strip_re`, IF GIVEN, is removed from the step's summary before the new
    embed is appended. CAUGHT LIVE: without this, calling this twice for the
    same step -- which happens routinely, since the idle artist's own
    progress can be lost to a container restart mid-run and it has no way to
    tell "already drawn" from "never drawn" at that point -- does not
    replace the old picture, it stacks a second one underneath it. A real
    recipe ended up with 41 duplicate illustrations on some steps this way.
    Each caller passes a regex anchored on ITS OWN fixed wording, so
    stripping a drawn illustration's old embed can never touch a scraped
    photo's or your own photo's embed on the same step.

    Best effort: called well after the recipe was already kept, from
    Kitchen's idle artist, the draw button, or a bulk backfill. A picture
    that did not make it across is a cosmetic gap, not a reason to look like
    saving the recipe failed. Returns whether it actually landed, so the
    caller can decide whether to retry later.
    """
    try:
        full = (await _get(DETAIL.format(slug=slug))).json()
        steps = full.get("recipeInstructions") or []
        recipe_id = full.get("id")
        if not recipe_id or step_index < 1 or step_index > len(steps):
            return False

        files = {"file": (filename, file_bytes, mime)}
        data = {"name": asset_name, "icon": "mdi-image",
                "extension": filename.rsplit(".", 1)[-1]}
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                core.MEALIE_URL + ASSETS.format(slug=slug),
                files=files, data=data,
                headers={"Authorization": f"Bearer {core.MEALIE_TOKEN}"})
        if r.status_code >= 400:
            return False
        file_name = r.json().get("fileName")
        if not file_name:
            return False

        url = f"/api/media/recipes/{recipe_id}/assets/{file_name}"
        current = steps[step_index - 1].get("summary") or ""
        if strip_re is not None:
            current = strip_re.sub("", current)
        steps[step_index - 1]["summary"] = current + embed_markdown_for(url)
        await _patch(DETAIL.format(slug=slug), {"recipeInstructions": steps})
        return True
    except MealieError:
        return False
    except httpx.RequestError:
        return False


# Anchored on each caller's own fixed wording, so stripping one kind of
# embed before replacing it can never touch a different kind sitting on
# the same step. The caption inside a drawn illustration's embed varies
# (art.py writes a new one each time); the surrounding markers ("AI-drawn
# illustration" as the alt text) do not, which is what these match on.
_DRAWN_STEP_RE = re.compile(
    r"\n\n!\[AI-drawn illustration\]\([^)]*\)\n\*[^*]*\*")
_SCRAPED_STEP_PHOTO_RE = re.compile(
    r"\n\n!\[[^\]]*\]\([^)]*\)\n\*From the recipe's own page\.\*")


async def push_step_illustration(slug: str, step_index: int, png: bytes,
                                caption: str) -> bool:
    """Upload a drawn step illustration (see app/art.py) to Mealie. See
    _push_step_asset for the mechanics; this just supplies the bytes and the
    "this is a drawing, not a photo" wording."""
    return await _push_step_asset(
        slug, step_index, png, f"step-{step_index}.png", "image/png",
        f"Step {step_index} illustration (AI-drawn)",
        lambda url: f"\n\n![AI-drawn illustration]({url})\n*{caption.strip()}*",
        strip_re=_DRAWN_STEP_RE)


# Real photos the source page already had, pulled off it and matched to a
# step by app/importer.py's attach_images(). Kept out of Mealie until now --
# see main.py's old _push_own_photo comment, "Mealie has one image per
# recipe, so step photos stay here on the guide" -- which was true only as
# long as the guide was the one place anyone read a recipe. It no longer is.
MAX_STEP_PHOTO_BYTES = 8 * 1024 * 1024
_PHOTO_EXT = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png",
             "image/webp": "webp", "image/gif": "gif"}


async def push_step_photo(slug: str, step_index: int, url: str, alt: str = "") -> bool:
    """Download a real photo the source page had for this step, and upload
    it to Mealie the same way push_step_illustration does. The URL comes
    from a page Kitchen already fetched to build this recipe in the first
    place -- not a new class of thing this app reaches out to.
    """
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            r = await client.get(url)
        if r.status_code >= 400 or len(r.content) > MAX_STEP_PHOTO_BYTES:
            return False
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        ext = _PHOTO_EXT.get(ctype)
        if not ext:
            return False
    except httpx.RequestError:
        return False

    caption = (alt or "").strip() or "A photo from the recipe's own page"
    return await _push_step_asset(
        slug, step_index, r.content, f"step-{step_index}.{ext}", ctype,
        f"Step {step_index} photo",
        lambda u: f"\n\n![{caption}]({u})\n*From the recipe's own page.*",
        strip_re=_SCRAPED_STEP_PHOTO_RE)


# Anchored on the caption text, which only push_own_step_photo ever writes,
# so it can never match a scraped photo's or a drawn illustration's own
# (differently worded) embed.
_OWN_STEP_PHOTO_RE = re.compile(
    r"\n\n!\[Your own photo of this step\]\([^)]*\)\n"
    r"\*Your own photo, added in Kitchen\.\*")
_STEP_PHOTO_EXT_MIME = {"jpg": "image/jpeg", "png": "image/png",
                        "gif": "image/gif", "webp": "image/webp"}


async def push_own_step_photo(slug: str, step_index: int,
                              file_bytes: bytes, ext: str) -> bool:
    """Your own uploaded step photo (app/photos.py), pushed to Mealie the
    same way a scraped or drawn one is -- see _push_step_asset, RecipeStep
    has no image field of its own, same reasoning as everywhere else here.
    Re-uploading replaces the old one rather than stacking a second
    underneath it -- see _push_step_asset's own strip_re."""
    mime = _STEP_PHOTO_EXT_MIME.get(ext)
    if not mime:
        return False
    return await _push_step_asset(
        slug, step_index, file_bytes, f"step-{step_index}.{ext}", mime,
        f"Step {step_index} photo (yours)",
        lambda u: (f"\n\n![Your own photo of this step]({u})\n"
                  f"*Your own photo, added in Kitchen.*"),
        strip_re=_OWN_STEP_PHOTO_RE)


async def remove_own_step_photo(slug: str, step_index: int) -> bool:
    """Undo push_own_step_photo. Strips the embed so the step's text is
    clean again; leaves the uploaded asset itself on Mealie -- an orphaned
    file nobody links to is harmless, and Mealie's assets API has no
    delete-by-name route worth chasing for a best-effort sync."""
    try:
        full = (await _get(DETAIL.format(slug=slug))).json()
        steps = full.get("recipeInstructions") or []
        if not (1 <= step_index <= len(steps)):
            return False
        before = steps[step_index - 1].get("summary") or ""
        after = _OWN_STEP_PHOTO_RE.sub("", before)
        if after == before:
            return False
        steps[step_index - 1]["summary"] = after
        await _patch(DETAIL.format(slug=slug), {"recipeInstructions": steps})
        return True
    except MealieError:
        return False


async def remove_step_illustration(slug: str, step_index: int) -> bool:
    """Undo push_step_illustration. Same shape as remove_own_step_photo,
    for the AI-drawn embed instead of an uploaded one.

    CAUGHT LIVE: main.py's "Remove the drawing" button only ever cleared
    the LOCAL copy (recipe["drawn"], the file on disk) -- nothing told
    Mealie to drop its own embed, so a kept recipe kept showing the old
    drawing on its own page no matter what happened here. This is the
    other half of that fix; see main.py's drop_drawing()."""
    try:
        full = (await _get(DETAIL.format(slug=slug))).json()
        steps = full.get("recipeInstructions") or []
        if not (1 <= step_index <= len(steps)):
            return False
        before = steps[step_index - 1].get("summary") or ""
        after = _DRAWN_STEP_RE.sub("", before)
        if after == before:
            return False
        steps[step_index - 1]["summary"] = after
        await _patch(DETAIL.format(slug=slug), {"recipeInstructions": steps})
        return True
    except MealieError:
        return False


def _food_baseline(food: dict) -> dict | None:
    """This food's own nutrition-per-100g, decoded from Mealie's `extras`.
    CAUGHT LIVE (see nutrition_fix.py's _load_nutrition): Mealie stores each
    extras value as its own string row, so this is JSON-encoded, not a
    nested dict -- a dict there 400s with a raw sqlite3.ProgrammingError."""
    raw = (food.get("extras") or {}).get("nutrition")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


async def _ensure_food_baseline(food: dict) -> dict | None:
    """This food's baseline, looked up and saved back to Mealie the first
    time this food is ever seen so every later recipe that uses it is free.
    Mirrors nutrition_fix.py's per-food backfill, run here for exactly the
    foods a freshly-kept recipe actually needs rather than the whole
    library -- see push_recipe_nutrition()."""
    base = _food_baseline(food)
    if base or not nutrition.configured():
        return base
    result = await nutrition.search(food["name"])
    if not result:
        # USDA HAS NOTHING -- fall back to the model, and only here, same
        # rule as nutrition_fix.py's cmd_backfill.
        result = await nutrition.llm_guess(food["name"])
    if not result:
        return None
    extras = dict(food.get("extras") or {})
    extras["nutrition"] = json.dumps(result)
    try:
        # FULL PUT, not a partial patch -- Mealie's food-update route wants
        # the whole IngredientFood back, same reasoning as cmd_backfill.
        await _put(f"/api/foods/{food['id']}", {**food, "extras": extras})
    except MealieError:
        pass  # the estimate below still uses `result` even if the save failed
    return result


def food_image(food: dict) -> dict | None:
    """This food's saved picture, decoded from Mealie's `extras`. Same
    JSON-string-per-key quirk as _food_baseline above -- see its own
    docstring for why this is not a nested dict."""
    raw = (food.get("extras") or {}).get("image")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


async def set_food_image(food: dict, url: str, source: str, alt: str = "") -> None:
    """Save a picture onto this food's extras. `url` is a Kitchen route
    (Mealie has no food-asset storage of its own, only recipe assets), and
    `source` is "internet" or "ai-drawn" so anywhere this is shown can label
    a drawn one honestly. FULL PUT, not a partial patch, same reasoning as
    _ensure_food_baseline. Unlike that function, a failed save is NOT
    swallowed here -- the caller just told a person "saved", and it needs
    to know if that was not true."""
    extras = dict(food.get("extras") or {})
    extras["image"] = json.dumps({"url": url, "source": source, "alt": alt})
    await _put(f"/api/foods/{food['id']}", {**food, "extras": extras})


async def push_ingredients(slug: str, ingredients: list) -> bool:
    """Push an updated ingredient TEXT list onto a recipe Mealie already
    has, then re-run Mealie's own parser on it -- the same two-step shape
    from_recipe() and parse_ingredients() already use for a fresh import,
    aimed here at a recipe that already exists. Used by the "refresh from
    source" backfill (main.py), where a handful of lines changed (an
    amount filled in, a new line added) and the rest should end up with
    whatever food/unit structure Mealie's parser gives them, same as any
    other import. Never raises -- a Kitchen-side save that already
    succeeded must not be reported as a failure over this."""
    try:
        await _patch(DETAIL.format(slug=slug), {
            "recipeIngredient": [{"note": line, "originalText": line}
                                 for line in ingredients],
        })
    except MealieError:
        return False
    await parse_ingredients(slug)
    return True


async def push_recipe_nutrition(slug: str) -> bool:
    """This recipe's nutrition, arithmetic against its own ingredients'
    USDA baselines (see recipe_nutrition.py), saved onto the recipe the
    moment it exists rather than waiting for the next manual
    `nutrition_fix.py --backfill`. Never raises -- a recipe that saved fine
    but has no nutrition estimate yet is cosmetic, not a failed keep."""
    if not nutrition.configured():
        return False
    try:
        full = (await _get(f"/api/recipes/{slug}")).json()
        ings = full.get("recipeIngredient") or []
        baselines: dict = {}
        for ing in ings:
            food = ing.get("food")
            if not food or not food.get("id"):
                continue
            base = await _ensure_food_baseline(food)
            if base:
                baselines[food["name"].strip().lower()] = base
        totals, used, total = recipe_nutrition.totals_for(ings, baselines)
        # A HANDFUL OF REAL MATCHES BEATS ONE LUCKY ONE -- UNLESS THAT IS
        # ALL THE RECIPE HAS. CAUGHT LIVE: on a 13-ingredient recipe, a
        # single mis-parsed unit ("15-ounce can" standing in for "28-ounce
        # can") matching one USDA baseline was enough for `used` to be
        # non-zero -- and that ONE ingredient's numbers, scaled up, stood
        # in for the whole pot: 453 kcal and 90g of fiber for a chicken
        # cacciatore. `used` on its own does not mean "this represents the
        # recipe", only "at least one ingredient happened to match" -- so a
        # THIN estimate (fewer than 3 matched) is distrusted UNLESS it is
        # also a COMPLETE one (used == total, nothing left unmatched) --
        # a genuinely two-ingredient recipe with both ingredients matched
        # is not the same failure as one out of thirteen.
        if not totals or (used < 3 and used != total):
            # Too little (or nothing) to compute anything real -- fall back
            # to whatever the SOURCE PAGE itself published (crawl.py's
            # nutrition_stated), if it published anything. Still just an
            # estimate either way, but a number the site printed on the
            # page beats one built from a single stray ingredient.
            return await _push_stated_nutrition_fallback(slug, full)
        est = recipe_nutrition.mealie_shape(totals)
        if est:
            # CAUGHT LIVE: the numbers alone are not enough -- Mealie's own
            # frontend hides the whole nutrition panel behind
            # settings.showNutrition, which defaults to False and stays
            # False on a PATCH that only touches "nutrition".
            settings = dict(full.get("settings") or {})
            settings["showNutrition"] = True
            await _patch(f"/api/recipes/{slug}", {"nutrition": est, "settings": settings})

        # The FULL totals -- macros and vitamins/minerals both, not just
        # Mealie's macro-only subset above -- saved into Kitchen's OWN
        # record of this recipe so guide() can build its %DV chart with no
        # extra Mealie round trip on every page view. See
        # recipe_nutrition.daily_values(). OWN try/except: the Mealie push
        # above already succeeded by this point, and a bookkeeping failure
        # here (Kitchen's own database being briefly unavailable, say) must
        # not turn that real success into a reported failure.
        try:
            draft = store.get_by_slug(slug)
            if draft:
                recipe = draft["recipe"]
                recipe["nutrition_full"] = totals
                # totals_for() sums the WHOLE recipe as Mealie has it right
                # now -- recipeServings is what turns that into "per
                # serving" in recipe_nutrition.daily_values(). Saved
                # alongside the totals themselves so a later change to the
                # recipe's own serving count cannot silently mismatch
                # against numbers computed under a different one.
                recipe["nutrition_servings"] = full.get("recipeServings") or 1
                store.update_recipe(draft["id"], recipe)
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


async def _push_stated_nutrition_fallback(slug: str, full: dict) -> bool:
    """push_recipe_nutrition() could not compute anything trustworthy from
    ingredient baselines -- too few ingredients matched a USDA food closely
    enough. If the source page itself published its own nutrition facts
    (crawl.from_jsonld()'s nutrition_stated), push those into Mealie
    instead. Only ever reached when the real ingredient-level estimate came
    back too thin to trust -- see the call site in push_recipe_nutrition().

    ALSO CLEARS A STALE nutrition_full -- CAUGHT LIVE: a recipe can have
    an OLD nutrition_full sitting in Kitchen's own record from before the
    used>=3 threshold existed (or from a re-parse that happened to match
    differently the first time around), and leaving it there would make
    guide()'s %DV chart show ingredient-level numbers built the same
    untrustworthy way this function just decided not to trust for Mealie's
    own copy. Cleared unconditionally on this path, whether or not a
    stated fallback exists to replace it with -- an honest "no estimate"
    beats a confident wrong one either way.
    """
    draft = store.get_by_slug(slug)
    if not draft:
        return False
    recipe = draft["recipe"]
    cleared = False
    if recipe.get("nutrition_full"):
        recipe["nutrition_full"] = None
        recipe["nutrition_servings"] = 1
        cleared = True

    stated = recipe.get("nutrition_stated") or {}
    est = recipe_nutrition.mealie_shape(stated) if stated else {}
    if not est:
        if cleared:
            store.update_recipe(draft["id"], recipe)
        return cleared

    settings = dict(full.get("settings") or {})
    settings["showNutrition"] = True
    await _patch(f"/api/recipes/{slug}", {"nutrition": est, "settings": settings})
    if cleared:
        store.update_recipe(draft["id"], recipe)
    return True


# Fire-and-forget tasks need a reference kept somewhere, or asyncio is free
# to garbage-collect a Task mid-flight with nothing else pointing at it --
# a real footgun, not a hypothetical one. This set is that somewhere.
_BACKGROUND: set = set()


def _fire_and_forget(coro) -> None:
    task = asyncio.create_task(coro)
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)


async def keep(recipe: dict) -> str:
    """Save a recipe, best route first, then parse its ingredients into
    structured foods so the Foods list and search stay populated without a
    manual backfill run. Returns the slug."""
    if not recipe.get("tools"):
        # Most source pages never publish schema.org's `tool` field at all
        # (see crawl.py's _tools_from_ld() docstring) -- ask the model to
        # work it out from the recipe's own steps rather than leave the
        # Finder page's tools filter useless for almost everything kept.
        try:
            recipe["tools"] = await labels.suggest_tools(recipe)
        except Exception:  # noqa: BLE001
            pass  # a missing tools guess is cosmetic; never fail a keep over it
    source = recipe.get("source", "")
    if source.startswith(("http://", "https://")):
        try:
            slug = await from_url(source, recipe)
            await parse_ingredients(slug)
            # Backgrounded, not awaited: this can be several sequential USDA
            # lookups (one per ingredient never priced before), which would
            # make "keep this recipe" feel slow for no reason a person
            # watching the button would understand.
            _fire_and_forget(push_recipe_nutrition(slug))
            return slug
        except MealieError:
            # Mealie's scraper could not read it either. Ours already did, so
            # push that instead of giving up.
            pass
    slug = await from_recipe(recipe)
    await parse_ingredients(slug)
    _fire_and_forget(push_recipe_nutrition(slug))
    return slug


# ============================================================ meal planning ===
#
# THIN, LIKE EVERYTHING ELSE IN THIS FILE. Mealie already has a real meal
# planner and a real shopping list -- these are not reimplemented, just
# called. Kitchen's job is a page that is quicker to use than Mealie's own.
MEALPLANS = "/api/households/mealplans"
MEALPLAN = "/api/households/mealplans/{item_id}"


async def mealplan_week(start: str, end: str) -> list:
    """Every planned entry between two YYYY-MM-DD dates, inclusive."""
    r = await _get(f"{MEALPLANS}?start_date={start}&end_date={end}&perPage=100")
    return r.json().get("items") or []


async def mealplan_add(date: str, slug: str, entry_type: str = "dinner") -> dict:
    """Put a recipe on the plan for a day. `entry_type` is one of Mealie's
    own: breakfast, lunch, dinner, side."""
    full = (await _get(DETAIL.format(slug=slug))).json()
    recipe_id = full.get("id")
    if not recipe_id:
        raise MealieError("Could not find that recipe to plan it.")
    r = await _post(MEALPLANS, {"date": date, "entryType": entry_type,
                                "recipeId": recipe_id})
    return r.json()


async def mealplan_remove(item_id) -> None:
    await _delete(MEALPLAN.format(item_id=item_id))


# NO SHOPPING-LIST WRAPPER HERE. Kitchen already has its own -- a real
# feature, not a gap: store.shopping_add()/shopping_all()/etc, its own
# SQLite table, its own page, wired to every guide's "Add to shopping list"
# button already. Checked before building a second one; Mealie's own
# shopping API would have been a second source of truth for the exact same
# list, which is the mistake this file's own docstring warns against.


# ================================================================== random ===
async def random_recipe() -> dict | None:
    """One recipe, picked at random from the whole shared library. Mealie's
    own recipe search takes an `orderBy=random` -- one call, no fetching
    every recipe just to pick one client-side.

    CAUGHT LIVE: `orderBy=random` alone 422s -- Mealie requires a
    `paginationSeed` too (it is what makes "random" reproducible across
    pages of the same request rather than actually random every row).
    A fresh uuid each call is exactly what makes THIS call random.
    """
    seed = uuid.uuid4()
    r = await _get(f"/api/recipes?orderBy=random&orderDirection=asc&perPage=1"
                   f"&paginationSeed={seed}")
    items = r.json().get("items") or []
    return items[0] if items else None


async def health() -> tuple[bool, str]:
    """For the status line. Never raises."""
    if not configured():
        return False, "no API token set"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(core.MEALIE_URL + "/api/app/about",
                                 headers=_headers())
        if r.status_code == 401:
            return False, "token rejected"
        if r.status_code >= 400:
            return False, f"said {r.status_code}"
        version = ""
        try:
            version = r.json().get("version", "")
        except ValueError:
            pass
        return True, version or "reachable"
    except Exception as e:
        return False, e.__class__.__name__
