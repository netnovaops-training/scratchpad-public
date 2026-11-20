"""Handing a finished recipe to Tandoor. The mealie.py of the Tandoor era.

WHY THIS FILE EXISTS SEPARATELY FROM mealie.py, NOT INSTEAD OF IT (YET)
-------------------------------------------------------------------------
Mealie is stopped, not deleted -- see core.py and main.py's own notes. This
is the new library; mealie.py stays in the tree until every route that calls
it has been retargeted here and the real recipe data has been migrated
(see the plan for the full phase list). Until then both files exist.

WHY THIS IS SIMPLER THAN mealie.py
------------------------------------
Mealie's API takes ingredients as plain text and tags/tools as objects that
have to be looked up-or-created BY HAND, with real, caught-live sharp edges
(a tag matching by slug but not by name, a RecipeTool schema that demands an
`id` even for something brand new). Tandoor's `/api/recipe/` does all of
that server-side: POST one nested payload -- steps, each with ingredients,
each ingredient naming its food and unit by NAME -- and Tandoor resolves an
existing Food/Unit/Keyword by name or creates it, in one request. Confirmed
live, this session, against the real running container before writing a
line of this file: a food named "Onion" matched Tandoor's own pre-seeded
food rather than creating a duplicate; two never-before-seen keywords came
back with real ids from the same POST that created the recipe carrying
them. So there is no equivalent of mealie.py's `_resolve_tag_like`/
`_finalize_organizers` dance here -- it would be re-solving a problem
Tandoor's own serializer already solves.

THE ONE THING THAT IS HARDER: LOGGING IN
-------------------------------------------
Mealie accepts a static bearer token (`KITCHEN_MEALIE_TOKEN`) minted once by
hand. Tandoor used to be harder, and this module used to say so: it claimed
token auth was "wired into zero views" and scraped the Django login form
instead, caching a sessionid cookie.

BOTH HALVES OF THAT TURNED OUT TO BE WRONG (2026-09-06). /api-token-auth/
does issue tokens and the API does accept them; and the login page this
module scraped is rendered by JavaScript now, so the csrf regex matched
nothing and every call had been failing with "the sign-in page did not
have the form I expected" -- a message that correctly described the symptom
and pointed at the wrong cause. So: post the admin credentials once to
/api-token-auth/, keep the token, send it as a header.

ONE TRAP, WORTH THE LINE IT COSTS. The header must read `Bearer <token>`.
Django REST's own documentation says `Token <token>`; this Tandoor answers
403 to that and 200 to Bearer. Tested both before choosing.

WHY FAILURES ARE NOT FATAL
-----------------------------
Same rule as mealie.py: Tandoor being down must not stop the guide from
rendering. Everything here raises TandoorError; main.py catches it and
shows the guide anyway with a note saying it was not saved.
"""
import asyncio
import re

import httpx

from . import (britishisms, core, foodmatch, foodphotos, iteminfo, llm, nutrition,
               portions, recipe_nutrition, scale, store, toolimages, usda_local, vram)

RECIPE = "/api/recipe/"
RECIPE_DETAIL = "/api/recipe/{id}/"
RECIPE_IMAGE = "/api/recipe/{id}/image/"
USER = "/api/user/"
INGREDIENT_PARSER = "/api/ingredient-parser/post/"
USER_FILE = "/api/user-file/"
STEP_DETAIL = "/api/step/{id}/"
FOOD = "/api/food/"
FOOD_DETAIL = "/api/food/{id}/"
KEYWORD = "/api/keyword/"
KEYWORD_DETAIL = "/api/keyword/{id}/"
MEAL_TYPE = "/api/meal-type/"
MEAL_PLAN = "/api/meal-plan/"

class TandoorError(Exception):
    """Tandoor refused or could not be reached. Never fatal to a page render."""


def configured() -> bool:
    return bool(core.TANDOOR_ADMIN_USER and core.TANDOOR_ADMIN_PASSWORD)


def public_link(recipe_id) -> str:
    """The address YOUR BROWSER can open, which is not the one we talk to.

    Same reasoning as mealie.py's public_link(): core.TANDOOR_URL is a
    Docker network name, useless outside the container.
    """
    if not core.TANDOOR_PUBLIC or not recipe_id:
        return ""
    return f"{core.TANDOOR_PUBLIC}/view/recipe/{recipe_id}"


# --- the admin session, cached and refreshed on demand ----------------------
#
# A dict, not two module-level strings, so a failed re-login can clear both
# atomically -- half a stale session (a csrftoken with no matching sessionid)
# is worse than no session, because it LOOKS configured and 403s anyway.
_session: dict = {}


async def _login() -> dict:
    """Get an API token. Was: scrape the login form, which no longer exists.

    Tandoor's /accounts/login/ is rendered by JavaScript now -- the served
    HTML has no <form> and no csrfmiddlewaretoken at all -- so the old
    regex found nothing and every call failed with "the sign-in page did
    not have the form I expected". Accurate and useless: the page had not
    changed shape, it had stopped being a server-rendered page.

    /api-token-auth/ is the supported way in and does not care what the
    front end is written in.
    """
    if not configured():
        raise TandoorError(
            "No Tandoor admin account is set. TANDOOR_ADMIN_USER and "
            "TANDOOR_ADMIN_PASSWORD need to be in .env.")
    try:
        async with httpx.AsyncClient(base_url=core.TANDOOR_URL, timeout=20) as client:
            r = await client.post(
                "/api-token-auth/",
                data={"username": core.TANDOOR_ADMIN_USER,
                      "password": core.TANDOOR_ADMIN_PASSWORD})
            if r.status_code != 200:
                raise TandoorError(
                    "Tandoor would not issue an API token for "
                    f"{core.TANDOOR_ADMIN_USER} ({r.status_code}). Check "
                    "TANDOOR_ADMIN_USER/PASSWORD, and that the account "
                    "exists and belongs to a space.")
            token = (r.json() or {}).get("token")
            if not token:
                raise TandoorError("Tandoor returned no token.")
            _session["token"] = token
            return _session
    except httpx.RequestError as e:
        raise TandoorError(f"Could not reach Tandoor ({e.__class__.__name__}).")


async def _call(method: str, path: str, *, json_body=None, files=None,
                data=None, timeout: float = 60, _retried: bool = False):
    """One authenticated call, logging in first if there is no cached
    token yet, and exactly once more if Tandoor answers 401/403 -- a token
    revoked since the last call looks identical to a bad password from
    here, so the retry is what tells them apart without guessing.
    """
    if "token" not in _session:
        await _login()
    # BEARER, NOT TOKEN, and this is not a typo. Django REST's own
    # documentation says "Token <key>"; this Tandoor answers 403 to that
    # and 200 to "Bearer <key>". Tested both before choosing. If these
    # calls ever start 403ing again, swap this word first.
    headers = {"Authorization": f"Bearer {_session['token']}"}
    try:
        async with httpx.AsyncClient(base_url=core.TANDOOR_URL, timeout=timeout) as client:
            r = await client.request(method, path, json=json_body, files=files,
                                     data=data, headers=headers)
    except httpx.RequestError as e:
        raise TandoorError(f"Could not reach Tandoor ({e.__class__.__name__}).")
    if r.status_code in (401, 403) and not _retried:
        _session.clear()
        return await _call(method, path, json_body=json_body, files=files,
                           data=data, timeout=timeout, _retried=True)
    if r.status_code >= 400:
        raise TandoorError(f"Tandoor said no ({r.status_code}): {r.text[:300]}")
    return r


async def health() -> tuple:
    """(ok, why-not). Mirrors mealie.py's health() shape so home.html's
    status dot needs no new logic, just a new call."""
    try:
        await _call("GET", USER)
        return True, ""
    except TandoorError as e:
        return False, str(e)


# --- pushing a recipe ---------------------------------------------------------

async def _parse_ingredients(lines: list) -> list:
    """Real lines -> Tandoor's own structured guess at food/unit/amount for
    each, via /api/ingredient-parser/post/ -- CONFIRMED LIVE this session to
    be the fix for the ambiguous-ingredient problem ("does 'pepper' mean
    bell or black?"): fed 'black pepper' and 'olive oil', it came back
    matched to real, DISTINCT foods ('Black Pepper', 'Olive Oil'), not one
    flat guess. Blank lines are silently dropped by Tandoor itself.

    Never raises -- an unparsed ingredient list is a worse recipe, not a
    failed save. On any error every line falls back to food=None (Tandoor
    accepts that; see _ingredient_payload) rather than losing the text.
    """
    lines = [ln for ln in lines if (ln or "").strip()]
    if not lines:
        return []
    try:
        r = await _call("POST", INGREDIENT_PARSER, json_body={"ingredients": lines})
        return r.json().get("ingredients") or []
    except TandoorError:
        return [{"food": None, "unit": None, "amount": 0, "original_text": ln}
                for ln in lines]


# CAUGHT LIVE, running the first real migration batch: "1 onion finely
# chopped" parsed to food="finely chopped" -- Tandoor's parser picked the
# PREPARATION, not the actual food, and "finely chopped" passes every
# structural check above (no digits, no stray parens, isn't the whole
# original line). A food name made ENTIRELY of prep words like this is
# grammatically clean and factually wrong -- the one failure shape
# _food_name_looks_clean's structural checks cannot catch, because
# nothing about the STRING is malformed. Not exhaustive by design (a
# fixed word list can never be); it exists to catch the specific,
# reproducible failure found, not to guess at every possible one.
_PREP_ONLY_WORDS = {
    "chopped", "finely", "coarsely", "roughly", "thinly", "thickly",
    "sliced", "diced", "minced", "crushed", "peeled", "grated", "shredded",
    "cubed", "julienned", "halved", "quartered", "trimmed", "washed",
    "rinsed", "drained", "cooked", "raw", "fresh", "dried", "ground",
    "melted", "softened", "beaten", "whisked", "large", "small", "medium",
    "chunks", "pieces", "cut", "into", "and", "or", "of", "a", "to",
    # CAUGHT LIVE (2026-08-25): "4 6-oz boneless, skinless chicken
    # breasts" parsed to food="boneless" -- same exact failure shape as
    # the "finely chopped" case above, just a different pair of prep
    # words, and one this list didn't cover yet. Worse than that one in
    # practice: "boneless" alone passed every check, so it got REUSED
    # across seven different recipes -- some for chicken BREAST, one for
    # chicken THIGH -- silently merging two different real cuts into one
    # meaningless food with no nutrition baseline of its own.
    "boneless", "skinless",
    # CAUGHT LIVE AGAIN, same day, going looking for more instead of
    # waiting for the next report: "2 lb. bone-in, skin-on chicken
    # thighs" parsed to food="bone-in" -- real, active, affecting two
    # more recipes. The word-splitting regex below only matches letter
    # runs, so the hyphen splits this into ["bone","in"] / ["skin","on"]
    # -- none of which is safe to add on its OWN in most contexts ("in"
    # and "on" are everywhere), but this check only fires when EVERY
    # word in the whole name is prep-only, and no real food is ever
    # named just "bone"/"skin"/"in"/"on" with nothing else -- safe here
    # specifically because of that all-or-nothing gate, not in general.
    "bone", "skin", "in", "on",
}


def _food_name_looks_clean(name: str) -> bool:
    """A structural sanity check, not a taste check -- catches the shape
    of the garbled "ingredients" found by hand earlier this session
    (mis-imported listicle pages whose "ingredient list" was really a
    numbered list of OTHER recipes' titles: "27. One-Pan Chicken and
    Rice"). Generalizes mealie.py's _food_name_bad/_garbled, which were
    written against Mealie's own parser's specific failure shapes -- this
    checks the OUTPUT shape instead, so it holds regardless of which
    parser produced it."""
    name = (name or "").strip()
    if not name or len(name) > 60:
        return False
    if name.count("(") != name.count(")"):
        return False
    if re.match(r"^\d", name):
        return False
    words = re.findall(r"[a-z']+", name.lower())
    if words and all(w in _PREP_ONLY_WORDS for w in words):
        return False
    return True


def _clean_parsed_food_names(parsed: list) -> None:
    """Runs the SAME identity-vs-description split dedupe.tidy_names()
    already applies retroactively, but at IMPORT TIME instead -- so a new
    recipe never creates a food named "(71 g) packed light brown sugar"
    in the first place (Jason's own report, 2026-08-25, finding one that
    had). In place, before resolve_ingredients()/_reuse_existing_foods()
    run, so both get the cleaned name to judge/match against rather than
    the raw one.

    Whatever clean_name_and_note() strips as real descriptive filler
    ("packed", "finely chopped") is not just discarded -- it's appended
    onto this ingredient's own `note`, never the shared food's name. Same
    request, stated twice this session about two different words:
    "Finely chopped parsley... should still be parsley with a sub
    context in the recipe as finely chopped" and "(71g) packed light
    brown sugar... with a note in the recipe packed under the food
    item." Mutates `parsed` in place; never raises -- clean_name_and_note
    is pure string logic with no failure mode to catch."""
    for p in parsed:
        food = p.get("food")
        name = (food or {}).get("name") if isinstance(food, dict) else None
        if not name:
            continue
        clean, note_words = foodmatch.clean_name_and_note(name)
        # britishisms LAST -- Jason's own follow-up request (2026-08-25,
        # re-raising his earlier "ingredients my mom would not know"
        # complaint): a food genuinely named "clear honey" or "streaky
        # bacon" should become "honey"/"bacon" so a US household
        # recognizes it AND so it correctly matches an existing "honey"
        # food rather than sitting next to it as an unrecognized
        # duplicate. Applied to whatever clean_name_and_note() already
        # produced, not the raw name, so both passes compose.
        clean = britishisms.americanize_name(clean or name)
        if not clean or clean.strip().lower() == name.strip().lower():
            continue
        food["name"] = clean
        if note_words:
            existing_note = (p.get("note") or "").strip()
            if note_words.lower() not in existing_note.lower():
                p["note"] = f"{existing_note}, {note_words}" if existing_note else note_words


async def resolve_ingredients(recipe: dict, parsed: list) -> tuple:
    """The three-tier match Jason asked for (2026-08-24): a confident
    parse is trusted as-is; a doubtful one is re-read against the
    RECIPE'S OWN context (title, every ingredient, every step) and
    corrected if the model can say what it actually is; if nothing makes
    sense even then, it's left alone and reported as a warning rather than
    guessed at.

    ONE model call for the whole recipe, not one per ingredient -- cheaper,
    and genuinely better: the model sees every ingredient together, which
    is exactly the context a person uses to work out that "pepper" in a
    chili recipe alongside "cumin" and "chili powder" almost certainly
    means the spice, not the vegetable.

    Returns (parsed, warnings) -- `parsed` is the same list, food names
    corrected in place where the model found something more specific;
    `warnings` is a list of {line, why} for anything still unresolved.
    Never raises: an LLM failure here means every line is trusted as the
    parser gave it, not that the whole import fails.
    """
    def _is_doubtful(p: dict) -> bool:
        food_name = (p.get("food") or {}).get("name", "")
        if not p.get("food") or not _food_name_looks_clean(food_name):
            return True
        # CAUGHT LIVE: when Tandoor's parser cannot make sense of a line at
        # all, it does not return food=None -- it falls back to the WHOLE
        # original line as the food name verbatim ("xzqw random garbage
        # 123" parsed to a food literally named "xzqw random garbage
        # 123"). That passes every structural check above (no stray
        # parens, no leading digit) while being exactly as unparsed as a
        # None would have been. A real successful parse's food name is
        # almost always a SUBSET of the line (quantity/unit words
        # stripped off), never the identical whole string.
        original = (p.get("original_text") or "").strip().lower()
        return bool(original) and food_name.strip().lower() == original

    doubtful = [(i, p) for i, p in enumerate(parsed) if _is_doubtful(p)]
    if not doubtful:
        return parsed, []

    # NUMBERED 1..N FOR DISPLAY ONLY -- CAUGHT LIVE, THE HARD WAY: this used
    # to number lines by their ORIGINAL absolute index into `parsed` (e.g.
    # "2." and "8." for a 16-line recipe) and match corrections back by
    # that same number. Watching it fail on real recipes: the model
    # consistently re-numbered its OWN response starting from 0 for
    # whatever subset it was shown, not the absolute indices in the
    # prompt -- so a correction meant for line 2 (the actual doubtful
    # ingredient) landed on parsed[0] instead (an entirely different,
    # already-correct line), and the real doubtful line was left
    # unfixed. Fixed by not trusting the model's own numbering AT ALL:
    # matched back by the ORIGINAL TEXT it's asked to echo verbatim,
    # which a language model reproduces far more reliably than it does
    # arithmetic on a list of numbers, with position as a same-order
    # fallback if the text doesn't match exactly.
    lines = "\n".join(f"{n}. {p.get('original_text', '')} "
                      f"(parsed as: {(p.get('food') or {}).get('name') or 'nothing'})"
                      for n, (_, p) in enumerate(doubtful, 1))
    system = (
        "You check whether a recipe's ingredient parser correctly identified "
        "each food. You are given the recipe and a list of DOUBTFUL lines "
        "(ones where the parser found nothing, or something that looks like "
        "a parsing error). For each line, decide: "
        "\"confirm\" (the parse is actually fine as-is), "
        "\"correct\" (you can tell exactly what food it should be, from context), "
        "or \"unresolved\" (you genuinely cannot tell). "
        "IMPORTANT: the parser's guess is often a PREPARATION, not the food "
        "itself -- \"finely chopped\", \"roughly torn\", \"crushed\" are "
        "never valid foods on their own, even if that is literally what the "
        "parser output. If the guess is only preparation/quantity words like "
        "that, it is NEVER \"confirm\" -- find the actual food noun in the "
        "line's own text (\"1 onion finely chopped\" -> the food is "
        "\"onion\", right there in the line) and answer \"correct\" with it. "
        "Only use \"confirm\" when the guess is already a real, specific "
        "food name. "
        "Answer ONLY with real, specific food names -- never invent an "
        "ingredient that isn't implied by the line itself. "
        "ECHO THE LINE TEXT BACK EXACTLY as given, in `line` -- do not "
        "renumber, paraphrase, or reorder it. "
        'Return JSON: {"lines": [{"line": "<exact line text>", '
        '"verdict": "correct", "food": "onion"}, ...]}, '
        "one entry per doubtful line, same order as given."
    )
    user = (f"Recipe: {recipe.get('title', '')}\n\n"
           f"All ingredients:\n" + "\n".join(recipe.get("ingredients") or []) +
           f"\n\nSteps:\n" + "\n".join(recipe.get("steps") or []) +
           f"\n\nDoubtful lines to check:\n{lines}")
    try:
        # NOT core.LLM_LABEL -- CAUGHT LIVE running the first real migration
        # batch: the same exact "1 onion finely chopped" case was correctly
        # fixed on one run and silently left wrong on another, with nothing
        # else different -- llama3.2:3b (LLM_LABEL's model) is reliable at
        # simple classification but not consistent at this kind of judgment
        # call. core.LLM_READ is the SAME "no thinking mode, survives JSON"
        # family the `mama`/`phone` jobs already rely on for exactly that
        # reason, unlike `chat`'s qwen3:8b which returns empty replies when
        # asked for JSON -- upgraded again since (2026-08-24) from 7B to
        # 14B, same family, see vram.py/litellm/config.yaml's own notes.
        # temperature=0.1, not the default 0.2: a lower bound was worth
        # spending given the ask was reliability, not creativity.
        async with vram.parsing():
            result = await llm.chat_json(system, user, model=core.LLM_READ,
                                         temperature=0.1)
    except llm.LLMError:
        return parsed, []

    rows = result.get("lines") or []

    def _find(text: str, pos: int):
        """The doubtful entry this response row is really about -- by
        exact text match first, falling back to same-position (the model
        reliably preserves ORDER even when it mangles text or numbers)."""
        text = (text or "").strip().lower()
        for idx, p in doubtful:
            if (p.get("original_text") or "").strip().lower() == text:
                return idx, p
        if 0 <= pos < len(doubtful):
            return doubtful[pos]
        return None, None

    warnings = []
    for pos, row in enumerate(rows):
        i, target = _find(row.get("line", ""), pos)
        if target is None:
            continue
        verdict = row.get("verdict")
        food_name = (row.get("food") or "").strip()
        current = (parsed[i].get("food") or {}).get("name", "")
        if verdict == "correct" and food_name and _food_name_looks_clean(food_name):
            parsed[i]["food"] = {"id": None, "name": food_name}
        elif verdict == "confirm" and not _food_name_looks_clean(current):
            # CAUGHT LIVE: the model itself sometimes says "confirm" on a
            # food name this app's OWN structural check already knows is
            # not a real food ("finely chopped" for "1 onion finely
            # chopped") -- that is the reason the line was sent to it in
            # the first place, and a model that fails to notice does not
            # get to overrule a check that already found something
            # concretely wrong. Never silently kept; flagged instead.
            warnings.append({"line": target.get("original_text", ""),
                            "why": "the parser's guess doesn't look like a "
                                   "real ingredient, and nothing could "
                                   "confirm what it should be instead"})
        elif verdict == "unresolved" or not food_name:
            warnings.append({"line": target.get("original_text", ""),
                            "why": "could not tell what this ingredient is, "
                                   "even from the recipe's own context"})
        # "confirm" with an already-clean current name -- leave it alone.
    return parsed, warnings


async def _reuse_existing_foods(parsed: list) -> list:
    """Jason's own request (2026-08-24), clarified: "make changes on the
    fly when I edit Ingredients" -- catch a close match against the
    EXISTING food library WHILE a recipe is being imported, before
    Tandoor's own resolve-or-create-by-exact-name step ever runs, so a
    5th "coriander" doesn't get created to sit next to the 4 that already
    existed by the time someone gets around to a dedupe pass. See
    foodmatch.match_new_names for the actual two-tier matching, shared
    with dedupe.py's own later cleanup pass.

    Never raises and never blocks an import on this -- a Tandoor hiccup
    fetching the current library, or an LLM failure inside foodmatch,
    just means every ingredient is created as Tandoor's own exact-name
    match/create would have done anyway, same as every other optional
    step in this pipeline (resolve_ingredients, _ensure_food_baseline)."""
    names = [(p.get("food") or {}).get("name") for p in parsed]
    names = [n for n in names if n]
    if not names:
        return parsed
    try:
        existing = await all_foods()
        matches = await foodmatch.match_new_names(names, existing)
    except TandoorError:
        return parsed
    if not matches:
        return parsed
    for p in parsed:
        food = p.get("food")
        if food and food.get("name") in matches:
            food["name"] = matches[food["name"]]
    return parsed


def _ingredient_payload(parsed: dict) -> dict:
    food = parsed.get("food")
    unit = parsed.get("unit")
    out = {
        "original_text": parsed.get("original_text", ""),
        "amount": parsed.get("amount") or 0,
        "note": parsed.get("note") or "",
        "is_header": bool(parsed.get("is_header")),
        "no_amount": not unit and not parsed.get("amount"),
    }
    # BY NAME, not id -- CONFIRMED LIVE: the nested writable serializer
    # 400s on a bare {"id": ...} with no "name" alongside it (it re-derives
    # the food/unit from the name, not the id, even though the parser just
    # handed back a real id). Sending the parser's OWN `name` back is what
    # actually re-resolves to the exact food it matched -- "black pepper"
    # stays pointed at the same Black Pepper food either way, since that's
    # the name Tandoor itself chose.
    if food and food.get("name"):
        out["food"] = {"name": food["name"]}
    else:
        # Tandoor requires a food on every ingredient (confirmed live: a
        # bare original_text-only ingredient 400s). An unmatched line still
        # needs somewhere to go, so it gets its own text as a food name --
        # visibly odd in the ingredient list, which is honest: Tandoor
        # could not work out what this ingredient actually is, and no code
        # here should pretend otherwise by picking one for it.
        out["food"] = {"name": parsed.get("original_text") or "ingredient"}
    # ALWAYS PRESENT, never omitted -- CONFIRMED LIVE: Tandoor's nested
    # serializer treats a MISSING unit key as invalid ("this field is
    # required") but accepts an EXPLICIT null just fine. A line like "salt
    # to taste" has no unit at all -- the parser itself returns unit: null
    # for it -- so the honest payload is unit: null, not a key that isn't
    # there.
    out["unit"] = {"name": unit["name"]} if unit and unit.get("name") else None
    return out


# Tandoor has no equipment/tools concept of its own (checked live: no
# Tool model in cookbook/models.py at all, unlike Mealie's dedicated
# RecipeTool organizer). Tools go in as keywords under this prefix instead
# -- Keyword is the one Tandoor structure that's already a real tree and
# already auto-resolves-or-creates by name, the same way Food does. The
# prefix is what lets guide()'s Tools section (and a future /tools browse
# page) tell "Cast Iron Skillet" the tool apart from any actual keyword
# that happens to share the word, without needing real tree nesting Tandoor's
# nested recipe-create payload has no easy way to specify inline anyway.
TOOL_PREFIX = "Tool: "


async def _to_tandoor(recipe: dict, draft_id: str = "") -> dict:
    """Our shape -> Tandoor's shape.

    KEYWORDS GO IN BY NAME, UNPARSED -- Tandoor's own nested serializer
    resolves-or-creates the Keyword server-side (confirmed live), the same
    way it does Food/Unit. INGREDIENTS go through _parse_ingredients()
    first, unlike keywords, because food/unit are REQUIRED fields on every
    ingredient (confirmed live) -- there's no bare-text ingredient to fall
    back to the way mealie.py could leave `food` empty and patch it in
    later.
    """
    keywords = []
    seen = set()
    for name in (list(recipe.get("labels") or [])
                 + [recipe.get("course", ""), recipe.get("cuisine", "")]):
        name = (name or "").strip()
        key = name.lower()
        if not name or key in seen:
            continue
        seen.add(key)
        keywords.append({"name": name})
    for name in recipe.get("tools") or []:
        name = (name or "").strip()
        key = f"tool:{name.lower()}"
        if not name or key in seen:
            continue
        seen.add(key)
        keywords.append({"name": f"{TOOL_PREFIX}{name}"})

    steps = [{
        "instruction": step,
        "order": i,
        "ingredients": [],
    } for i, step in enumerate(recipe.get("steps", []))]
    # All ingredients land on the FIRST step. Tandoor has no recipe-level
    # ingredient list of its own -- ingredients belong to a step -- and
    # Kitchen's own recipe shape doesn't say which step needs which
    # ingredient, so putting them all up front (mirroring how Mealie's own
    # single recipeIngredient list reads) is the honest mapping, not a
    # guess at a per-step split nothing here actually knows.
    if not steps:
        steps = [{"instruction": "", "order": 0, "ingredients": []}]
    # PARSE from the pre-imperial/pre-britishisms text when it exists
    # (importer._add_detail() stashes it), DISPLAY the nicer converted
    # text -- see that function's own docstring for why parsing the
    # converted text directly corrupts food names ("14 oz (400g) flour"
    # parses as food="(400g) flour"). Filtered together, in lockstep,
    # the same way _parse_ingredients() itself drops blank lines -- so
    # parsed[i] and display_lines[i] still refer to the same real line
    # after Tandoor's parser (which only ever sees parse_lines) drops
    # its own blanks internally.
    display_lines = recipe.get("ingredients", [])
    parse_lines_raw = recipe.get("_parse_ingredients") or display_lines
    kept = [(p, d) for p, d in zip(parse_lines_raw, display_lines) if (p or "").strip()]
    parse_lines = [p for p, _ in kept]
    display_lines = [d for _, d in kept]
    parsed = await _parse_ingredients(parse_lines)
    _clean_parsed_food_names(parsed)
    # resolve_ingredients() runs BEFORE the original_text swap below, on
    # purpose -- its own "did the parser totally fail" check compares
    # food_name against original_text, and that check only means
    # anything against the SAME text the parser actually saw (the metric
    # parse_lines), not the converted display text swapped in next.
    parsed, warnings = await resolve_ingredients(recipe, parsed)
    for w in warnings:
        store.food_warning_add(draft_id, recipe.get("title") or "Untitled",
                              w["line"], w["why"])
    parsed = await _reuse_existing_foods(parsed)
    # NOW swap in the nicer text -- everything that needed the PARSER's
    # own original_text has already run; internal_food_usage()'s "let me
    # see the recipe where it comes from" should show what a person
    # actually reads on the page, not the metric line underneath it.
    for _i, _p in enumerate(parsed):
        if _i < len(display_lines):
            _p["original_text"] = display_lines[_i]
    steps[0]["ingredients"] = [_ingredient_payload(p) for p in parsed]

    # TRUNCATED, NOT LEFT AS-IS -- CAUGHT LIVE running the real 245-recipe
    # migration: Tandoor's `description` field caps at 512 characters and
    # `servings_text` at 32 (confirmed by the 400s it returned -- Tandoor
    # never documented these limits anywhere this app looked before
    # writing this). 7 real recipes failed outright on this before it was
    # caught -- notes scraped from a source page are often a full
    # paragraph, and a yields string like "makes about 2 dozen, depending
    # on size" is longer than it looks. Cut cleanly at a word boundary
    # rather than mid-word where possible.
    return {
        "name": recipe.get("title") or "Untitled",
        "description": _truncate(recipe.get("notes", ""), 512),
        "servings_text": _truncate(recipe.get("yields", ""), 32),
        # CAUGHT LIVE (2026-08-25), Jason's own report ("the portion
        # amounts are way off"): this payload only ever sent Tandoor the
        # human-readable servings_TEXT ("Serves 4-6") -- Tandoor's own
        # NUMERIC servings field was never set at all, so it silently
        # stayed at Tandoor's own default of 1. push_recipe_nutrition()
        # reads THIS field back to divide the recipe's whole-food totals
        # into a per-serving number -- with servings stuck at 1, every
        # nutrition figure on a 4-6-serving recipe came out 4-6x too
        # high. scale.servings_in() already exists and is already
        # trusted for portion scaling; reused here rather than a second
        # ad hoc parse of the same yields text.
        "servings": scale.servings_in(recipe.get("yields", "")) or 1,
        "working_time": _minutes(recipe.get("prep", "")),
        "waiting_time": _minutes(recipe.get("cook", "")),
        "source_url": recipe.get("source", ""),
        "keywords": keywords,
        "steps": steps,
    }


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # Prefer a word boundary if one exists reasonably close to the cut --
    # "makes about 2 doz" reads as a mistake, "makes about 2" does not.
    if " " in cut[-20:]:
        cut = cut[:cut.rfind(" ", 0, limit)]
    return cut.rstrip()


_ISO_DURATION_RE = re.compile(
    r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
    r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?",
    re.I)


def _minutes(text: str) -> int:
    """'25 minutes' -> 25, '1 hr 30 min' -> 90. Tandoor's working_time/
    waiting_time are plain integers, unlike Mealie's freeform
    prepTime/cookTime strings -- best effort, 0 when nothing parses
    rather than guessing.

    CAUGHT LIVE (2026-08-24): a raw, unconverted ISO 8601 duration string
    (e.g. "P0Y0M0DT0H5M0.000S", meaning 5 minutes) reached this function
    for a real recipe -- crawl.py's `_iso_duration()` is supposed to turn
    that into a human string like "5 min" at import time, but it, too, had
    a bug on this exact format (now fixed there as well). The naive
    `re.search(r"\\d+", ...)` this function used to be matched the FIRST
    digit in the string -- the "0" in "P0Y..." -- silently producing 0
    minutes instead of 5. Handling the raw ISO shape here too, not just at
    import time, means an already-imported recipe with this stuck in its
    `recipe["cook"]`/`recipe["prep"]` field self-heals the next time it's
    pushed, without needing a data migration.
    """
    text = (text or "").strip()
    if not text:
        return 0
    if text[:1].upper() == "P":
        m = _ISO_DURATION_RE.match(text)
        if m and any(m.groups()):
            years, months, weeks, days, hours, mins, secs = m.groups()
            total = 0
            if years:
                total += int(years) * 525600
            if months:
                total += int(months) * 43200
            if weeks:
                total += int(weeks) * 10080
            if days:
                total += int(days) * 1440
            if hours:
                total += int(hours) * 60
            if mins:
                total += int(mins)
            if secs:
                total += int(float(secs) / 60)
            return total
    hours = 0
    hm = re.search(r"(\d+)\s*hr", text, re.I)
    if hm:
        hours = int(hm.group(1))
    mins = 0
    mm = re.search(r"(\d+)\s*min", text, re.I)
    if mm:
        mins = int(mm.group(1))
    if hours or mins:
        return hours * 60 + mins
    m = re.search(r"\d+", text)
    return int(m.group()) if m else 0


async def from_recipe(recipe: dict, draft_id: str = "") -> int:
    """Push what Kitchen already parsed. Create, then attach the photo.

    Tandoor's own AI-import (POST /api/recipe-import/) is not used here,
    on purpose: Kitchen's own LLM pipeline already turned this page into
    structured `recipe` before this function is ever called, so asking
    Tandoor to scrape the URL again would be redoing work already done,
    with a worse result -- Tandoor's importer doesn't have Kitchen's own
    step-by-step guide logic behind it.
    """
    r = await _call("POST", RECIPE, json_body=await _to_tandoor(recipe, draft_id))
    body = r.json()
    recipe_id = body.get("id")
    if not recipe_id:
        raise TandoorError("Tandoor created the recipe but did not return an id.")

    if recipe.get("image"):
        # ONE RETRY -- CAUGHT LIVE during the first real migration batch:
        # a push that failed on the first try (right after the recipe
        # itself was just created) succeeded immediately when retried by
        # hand moments later. Never pinned down to one specific cause
        # (a slow response, Tandoor briefly busy right after the create
        # call) -- a short pause and one more attempt is cheap insurance
        # either way, and still best-effort: a photo that fails twice is
        # cosmetic, not a reason to fail the save.
        for attempt in (1, 2):
            try:
                await push_image_from_url(recipe_id, recipe["image"])
                break
            except TandoorError:
                if attempt == 2:
                    break
                await asyncio.sleep(2)
    return recipe_id


async def push_image_from_url(recipe_id, url: str):
    """Tandoor's recipe-image endpoint takes a FILE, not a URL to fetch --
    unlike Mealie's, which will fetch a URL server-side. So this app fetches
    the bytes itself (same fetch guard as everything else that pulls a
    remote image, see core.is_public_url) and re-uploads them."""
    try:
        core.is_public_url(url)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": core.USER_AGENT,
                                                   **core.BROWSER_HEADERS})
            resp.raise_for_status()
    except (core.Blocked, httpx.HTTPError) as e:
        raise TandoorError(f"Could not fetch the photo to upload it ({e}).")
    await put_image(recipe_id, resp.content, f"image.{_ext_from_url(url)}")


def _ext_from_url(url: str) -> str:
    """The file extension a remote image URL implies, ignoring any query
    string or fragment.

    CAUGHT LIVE, running the first real migration batch: the old version
    of this (`url.rsplit(".", 1)[-1]`) took everything after the LAST dot
    in the WHOLE url, including a query string -- so
    "...classic-lasange-4a66137.jpg?resize=440,400" produced the
    "extension" "jpg?resize=440,400", truncated to four characters as
    "jpg?", which Tandoor correctly rejected as not a real extension. Real
    photo CDNs attach resize/format query params like this constantly, so
    this wasn't a rare edge case -- it was silently losing hero images on
    a meaningful fraction of real recipes.
    """
    from urllib.parse import urlparse
    path = urlparse(url).path
    return (path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else "jpg")[:4]


async def put_image(recipe_id, image_bytes: bytes, filename: str = "image.jpg"):
    """Upload photo BYTES as the recipe's image. PUT, multipart -- confirmed
    live against RecipeImageSerializer's `image`/`image_url` fields."""
    files = {"image": (filename, image_bytes, "application/octet-stream")}
    await _call("PUT", RECIPE_IMAGE.format(id=recipe_id), files=files)


async def keep(recipe: dict, draft_id: str = "") -> int:
    """The one call main.py's keep route needs: recipe becomes a real
    Tandoor entry, returns its id. Mirrors mealie.py's keep() shape (same
    name, same "return what store.mark_kept() needs to persist") so the
    call site in main.py is a small, obvious swap.
    """
    return await from_recipe(recipe, draft_id)


# --- nutrition ----------------------------------------------------------------
#
# Same arithmetic as mealie.py's version (recipe_nutrition.py, unchanged --
# ingredient baselines times grams-per-unit, summed). What's different is
# WHERE a food's own per-100g baseline is cached: Mealie had nowhere of its
# own for it (mealie.py stuffed it into a JSON string on Mealie's `extras`
# field); the cache lives in Kitchen's own database now (store.food_baseline),
# because Tandoor's own Food/Property model may or may not be worth pushing
# micronutrients into -- see store.py's food_baselines table comment.

_TANDOOR_FIELD = {
    "calories": "calories",
    "protein_g": "proteins",
    "fat_g": "fats",
    "carbs_g": "carbohydrates",
}


def _tandoor_nutrition_shape(totals: dict) -> dict:
    """totals_for()'s output, narrowed to Tandoor's four-field
    NutritionInformation -- no micronutrients there, unlike Kitchen's own
    %DV chart, which reads the FULL totals saved onto the draft below."""
    return {tandoor_key: round(totals[key], 1)
           for key, tandoor_key in _TANDOOR_FIELD.items() if key in totals}


async def _ensure_food_baseline(food_name: str) -> "dict | None":
    """This food's baseline, looked up and cached the first time it is
    ever seen so every later recipe that uses it is free. Mirrors
    mealie.py's _ensure_food_baseline(), retargeted at Kitchen's own cache.

    LOCAL USDA CHECKED FIRST, ALWAYS -- Jason's own words (2026-08-24):
    "I just want to have something that it can verify with so that it has
    a reference to pull from." usda_local.search() needs no API key, no
    network call, and no rate limit, so there is no reason to wait for
    the live API's own turn to check it -- only fall through to the live
    search (which DOES need nutrition.configured()) or the model's own
    guess when the local reference genuinely has nothing.
    """
    base = store.food_baseline(food_name)
    if base:
        return base
    result = usda_local.search(food_name)
    if not result and nutrition.configured():
        result = await nutrition.search(food_name)
    if not result:
        result = await nutrition.llm_guess(food_name)
    if not result:
        return None
    store.set_food_baseline(food_name, result)
    return result


async def push_recipe_nutrition(recipe_id) -> bool:
    """This recipe's nutrition, arithmetic against its own ingredients'
    USDA baselines. Never raises -- a recipe that saved fine but has no
    nutrition estimate yet is cosmetic, not a failed keep."""
    if not nutrition.configured():
        return False
    try:
        full = await get_recipe(recipe_id)
        if not full:
            return False
        # Tandoor's own ingredient shape uses `amount`, not Mealie's
        # `quantity` -- recipe_nutrition.totals_for() expects the latter,
        # so this rebuilds the list rather than changing shared arithmetic
        # that mealie.py still uses too.
        ings = [{"food": ing.get("food"), "unit": ing.get("unit"),
                "quantity": ing.get("amount") or 0}
               for step in (full.get("steps") or [])
               for ing in (step.get("ingredients") or [])]
        baselines: dict = {}
        for ing in ings:
            food = ing.get("food")
            if not food or not food.get("name"):
                continue
            base = await _ensure_food_baseline(food["name"])
            if base:
                baselines[food["name"].strip().lower()] = base
            # A COUNTED ingredient ("1 stalk", "2 cloves") has no gram
            # conversion of its own -- recipe_nutrition.grams_for()'s own
            # docstring says plainly "skipped, not guessed", so it drops
            # out of the total silently. Jason's own request (2026-08-25):
            # "I just want a baseline" -- portions.py's own per-food
            # reference (USDA SR Legacy, or hand-corrected in /admin)
            # turns "1 stalk" into an honest gram estimate instead,
            # converting it to an equivalent gram-based line so
            # totals_for()'s own math needs no changes at all to use it.
            unit_obj = ing.get("unit")
            unit_name = (unit_obj or {}).get("name") if isinstance(unit_obj, dict) else unit_obj
            if recipe_nutrition.grams_for(ing.get("quantity") or 0, unit_name or "") is None:
                pbase = portions.get(food.get("id"))
                if pbase and pbase.get("grams"):
                    ing["quantity"] = (ing.get("quantity") or 0) * float(pbase["grams"])
                    ing["unit"] = {"name": "g"}
        totals, used, total = recipe_nutrition.totals_for(ings, baselines)
        if not totals or (used < 3 and used != total):
            return await _push_stated_nutrition_fallback(recipe_id, full)
        est = _tandoor_nutrition_shape(totals)
        if est:
            await _call("PATCH", RECIPE_DETAIL.format(id=recipe_id),
                       json_body={"nutrition": est})

        # The FULL totals -- macros and vitamins/minerals both -- saved
        # onto Kitchen's OWN record, same reasoning as mealie.py's version:
        # guide()'s %DV chart reads this, not Tandoor, so no extra round
        # trip on every page view.
        try:
            draft = store.get_by_tandoor_id(recipe_id)
            if draft:
                recipe = draft["recipe"]
                recipe["nutrition_full"] = totals
                recipe["nutrition_servings"] = full.get("servings") or 1
                store.update_recipe(draft["id"], recipe)
        except Exception:  # noqa: BLE001
            pass
        return True
    except Exception:  # noqa: BLE001
        return False


async def _push_stated_nutrition_fallback(recipe_id, full: dict) -> bool:
    """push_recipe_nutrition() could not compute anything trustworthy from
    ingredient baselines. If the source page itself published its own
    nutrition facts (crawl.from_jsonld()'s nutrition_stated), push those
    instead. Mirrors mealie.py's version -- see its own docstring for why
    a stale nutrition_full is cleared unconditionally on this path."""
    draft = store.get_by_tandoor_id(recipe_id)
    if not draft:
        return False
    recipe = draft["recipe"]
    cleared = False
    if recipe.get("nutrition_full"):
        recipe["nutrition_full"] = None
        recipe["nutrition_servings"] = 1
        cleared = True

    stated = recipe.get("nutrition_stated") or {}
    est = _tandoor_nutrition_shape(stated) if stated else {}
    if not est:
        if cleared:
            store.update_recipe(draft["id"], recipe)
        return cleared

    try:
        await _call("PATCH", RECIPE_DETAIL.format(id=recipe_id),
                   json_body={"nutrition": est})
    except TandoorError:
        pass
    if cleared:
        store.update_recipe(draft["id"], recipe)
    return True


async def add_tool_keywords(recipe_id, tool_names: list) -> bool:
    """Add tools to an already-kept recipe WITHOUT touching anything
    else. Jason's own request (2026-08-26): "let's rebuild the recipe
    steps with more context about the food items and tools" -- backing
    ~85% of the library's missing tools onto real recipes.

    A KEYWORDS-ONLY PATCH, CONFIRMED LIVE (2026-08-26): sent alone (not
    through update_recipe()/_to_tandoor() above, which rebuilds the
    WHOLE recipe including steps), Tandoor leaves every step -- and any
    picture already on one -- completely untouched; same step ids,
    before and after. update_recipe() would have recreated every step
    row and orphaned its picture just to add a tool, which is exactly
    the kind of cost a purely additive backfill must not carry.
    """
    if not tool_names:
        return True
    recipe = await get_recipe(recipe_id)
    if not recipe:
        return False
    existing = {(k.get("name") or "").lower() for k in (recipe.get("keywords") or [])}
    kws = list(recipe.get("keywords") or [])
    added = False
    for name in tool_names:
        kw_name = f"{TOOL_PREFIX}{name}"
        if kw_name.lower() not in existing:
            kws.append({"name": kw_name})
            existing.add(kw_name.lower())
            added = True
    if not added:
        return True
    try:
        await _call("PATCH", RECIPE_DETAIL.format(id=recipe_id), json_body={"keywords": kws})
    except TandoorError:
        return False
    return True


async def update_recipe(recipe_id, recipe: dict, draft_id: str = "") -> bool:
    """Push an edit onto a recipe Tandoor already has.

    CONFIRMED LIVE, THE HARD WAY: PATCHing `steps` does not update the
    existing Step rows in place -- it CREATES BRAND NEW ONES, every time,
    even when the text is identical, and the old rows' `file` (the step's
    picture) does not carry over to the new one. So an edit that touches
    steps at all silently loses every step picture unless something
    re-attaches them afterward -- see main.py's edit route, which calls
    _push_step_pictures()/re-draws right after this to put them back using
    Kitchen's own local copies (nothing here is lost on Kitchen's side;
    only the Tandoor-side attachment breaks).
    """
    await _call("PATCH", RECIPE_DETAIL.format(id=recipe_id),
               json_body=await _to_tandoor(recipe, draft_id))
    return True


async def fix_missing_servings() -> dict:
    """One-time backfill for every recipe already stuck at Tandoor's
    default servings=1 -- see _to_tandoor()'s own comment for the root
    cause. Pure arithmetic (scale.servings_in(), already trusted
    elsewhere for portion scaling), so this runs as one bulk pass
    rather than needing review one recipe at a time -- there is no
    judgment call in reading a number back out of a yields string a
    person already wrote ("Serves 4-6" -> 4), only a bug in a field
    that was never being sent at all. Returns a tally, and the first
    few real corrections made, so the result is checkable at a glance
    rather than just trusted."""
    fixed = []
    skipped = 0
    for r in store.kept_with_ids():
        recipe = r.get("recipe") or {}
        tandoor_id = r.get("tandoor_id")
        if not tandoor_id:
            continue
        correct = scale.servings_in(recipe.get("yields") or "")
        if not correct:
            skipped += 1
            continue
        current = recipe.get("nutrition_servings")
        if current == correct and not recipe.get("nutrition_full"):
            continue  # nothing here depends on the number anyway
        try:
            await _call("PATCH", RECIPE_DETAIL.format(id=tandoor_id),
                       json_body={"servings": correct})
        except TandoorError:
            skipped += 1
            continue
        if recipe.get("nutrition_full") and current != correct:
            recipe["nutrition_servings"] = correct
            store.update_recipe(r["id"], recipe)
            fixed.append({"tandoor_id": tandoor_id, "title": recipe.get("title") or "Untitled",
                          "was": current, "now": correct})
    return {"fixed": len(fixed), "skipped": skipped, "sample": fixed[:20]}


async def delete_recipe(recipe_id):
    """Best-effort cleanup. Used by test/cleanup scripts, not the app
    itself -- nothing in the normal flow ever un-keeps a recipe."""
    await _call("DELETE", RECIPE_DETAIL.format(id=recipe_id))


# --- step pictures ------------------------------------------------------------
#
# GENUINELY SIMPLER THAN mealie.py's version of this, not just different.
# Mealie has no per-step image field at all -- a step picture there is an
# uploaded asset PLUS a markdown image link spliced into that step's text,
# which is why mealie.py needed strip-then-replace regexes (_DRAWN_STEP_RE
# etc.) to avoid stacking duplicates, and why removing one could leave the
# text looking edited. Tandoor's `Step.file` is a REAL foreign key
# (confirmed live: POST the bytes to /api/user-file/, PATCH the step's
# `file` to the new file's id) -- replacing a picture is just pointing the
# same field at a new file, and removing one is pointing it at null. No
# text to keep clean, no regex to keep in sync with itself.


def tools_from(recipe: dict) -> list:
    """This recipe's tools, pulled back out of its keywords -- the reverse
    of the TOOL_PREFIX convention _to_tandoor() writes. `recipe` is a full
    get_recipe() response, not Kitchen's own recipe dict."""
    out = []
    for kw in recipe.get("keywords") or []:
        name = kw.get("name") or ""
        if name.startswith(TOOL_PREFIX):
            out.append(name[len(TOOL_PREFIX):])
    return out


async def _all_pages(path: str) -> list:
    """Every result across Tandoor's DRF pagination, page_size pushed to
    200 -- CONFIRMED LIVE: asking for more than that (tried 500) is
    silently capped back down to 200 server-side, so this walks `next`
    instead of assuming one page is ever enough (1505 foods, 370
    keywords, both well past a single page)."""
    out: list = []
    sep = "&" if "?" in path else "?"
    url = f"{path}{sep}page_size=200"
    while url:
        r = await _call("GET", url)
        data = r.json()
        out.extend(data.get("results") or [])
        nxt = data.get("next")
        if not nxt:
            break
        # `next` is a FULL url (http://tandoor/api/...); _call wants a
        # path relative to the api base, so only the path+query survives.
        url = nxt.split("/api", 1)[-1]
        url = "/api" + url if not url.startswith("/api") else url
    return out


async def all_foods() -> list:
    """Every Food Tandoor knows about (1505+, real households accumulate
    a lot) -- the Tandoor-side replacement for mealie.all_foods(). CAUGHT
    LIVE (2026-08-24): main.py's foods_index()/_get_icon_map() were still
    calling the OLD mealie.* functions after the migration -- harmless
    while Mealie was still running, but Mealie is stopped now (see
    TANDOOR_MIGRATION_STATUS.md), so every one of those routes, including
    guide()'s own icon lookup, was throwing on every single page view.
    This function existing at all is the fix."""
    return await _all_pages(FOOD)


async def get_food(food_id) -> "dict | None":
    if not food_id:
        return None
    try:
        return (await _call("GET", FOOD_DETAIL.format(id=food_id))).json()
    except TandoorError:
        return None


async def all_tools() -> list:
    """Every tool, same TOOL_PREFIX convention tools_from() reads back
    per-recipe -- here read across ALL keywords at once, for the /tools
    browse page and the missing-pictures backlog. Tandoor has no
    first-class "kitchen tool organizer" the way Mealie did (confirmed
    against its schema); tools live as a Keyword subtree instead, same
    design call already made and documented in _to_tandoor()."""
    out = []
    for kw in await _all_pages(KEYWORD):
        name = kw.get("name") or ""
        if name.startswith(TOOL_PREFIX):
            out.append({"id": kw.get("id"), "name": name[len(TOOL_PREFIX):]})
    return out


async def get_tool(tool_id) -> "dict | None":
    if not tool_id:
        return None
    try:
        kw = (await _call("GET", KEYWORD_DETAIL.format(id=tool_id))).json()
    except TandoorError:
        return None
    name = kw.get("name") or ""
    if not name.startswith(TOOL_PREFIX):
        return None
    return {"id": kw.get("id"), "name": name[len(TOOL_PREFIX):]}


async def create_tool(name: str) -> "int | None":
    """A tool that exists ready for a picture even though no recipe has
    used it yet -- Jason's own request (2026-08-24): "an extra set of
    tools that are commonly found like a colander, strainer, blender...
    I'll have pictures for that too." Confirmed live: POSTing straight to
    /api/keyword/ works the same way Tandoor auto-creates a keyword when
    a recipe references a new one -- nothing here needs a recipe to exist
    first. Returns the new Keyword's id, or None if Tandoor already has a
    tool by this name (never creates a duplicate)."""
    for t in await all_tools():
        if t["name"].strip().lower() == name.strip().lower():
            return None
    try:
        r = await _call("POST", KEYWORD, json_body={"name": f"{TOOL_PREFIX}{name}"})
        return r.json().get("id")
    except TandoorError:
        return None


async def rename_food(food_id, new_name: str) -> bool:
    """Jason's own request (2026-08-24): "I would like to be able change
    the name of the food item... when I update it update the recipe."
    Nothing extra needed for the second half -- Tandoor's Food is a
    single shared row every recipe's ingredient references BY ID, not by
    copying its name in, so a rename here is already live everywhere
    that food is used, the same instant, with no separate per-recipe
    update step."""
    try:
        await _call("PATCH", FOOD_DETAIL.format(id=food_id), json_body={"name": new_name})
        return True
    except TandoorError:
        return False


async def rename_tool(tool_id, new_name: str) -> bool:
    """Same as rename_food(), for a Keyword -- re-applies the TOOL_PREFIX
    convention so it stays a real tool afterward, not a bare keyword."""
    try:
        await _call("PATCH", KEYWORD_DETAIL.format(id=tool_id),
                   json_body={"name": f"{TOOL_PREFIX}{new_name}"})
        return True
    except TandoorError:
        return False


async def add_food(name: str) -> "int | None":
    """Bare create, no existence check -- unlike create_tool(), which can
    afford to re-fetch all_tools() (370 keywords, ~2 pages) on every call.
    all_foods() is 1500+ entries across ~8 pages; a seed script adding a
    few dozen foods would pay that fetch cost once per candidate for no
    reason. Callers that need the dedupe (a seed script, say) fetch
    all_foods() ONCE themselves and check membership before calling this."""
    try:
        r = await _call("POST", FOOD, json_body={"name": name})
        return r.json().get("id")
    except TandoorError:
        return None


# --- meal planning, and the random pick ---------------------------------------
#
# Both /plan and /surprise were still calling mealie.* after the migration
# -- caught live (2026-08-24) reading through main.py for every remaining
# mealie.* call, the same audit that found the guide()/foods/tools bugs
# earlier this session. Neither crashed outright (both were wrapped in
# try/except mealie.MealieError), but Mealie is stopped, so both have been
# answering with a plain error page since then. Confirmed live before
# writing any of this: Tandoor's /api/recipe/?random=true works, and
# /api/meal-plan/ takes a plain integer for `recipe`/`meal_type` on write
# even though it reads back nested (tested directly: create, inspect,
# delete).

_MEAL_TYPE_NAMES = ["Breakfast", "Lunch", "Dinner", "Side"]
_meal_type_cache: dict = {}


async def _ensure_meal_types() -> dict:
    """name (lowercased) -> id, creating Breakfast/Lunch/Dinner/Side the
    first time anything asks -- confirmed live there were ZERO MealTypes
    on Tandoor's side (meal planning was never used there; Kitchen's own
    /plan page always talked to Mealie instead). Matches the same four
    entry_types /plan's own HTML already offers."""
    if _meal_type_cache:
        return _meal_type_cache
    results = await _all_pages(MEAL_TYPE)
    cache = {m["name"].strip().lower(): m["id"] for m in results}
    for name in _MEAL_TYPE_NAMES:
        if name.lower() not in cache:
            try:
                r = await _call("POST", MEAL_TYPE, json_body={"name": name})
                cache[name.lower()] = r.json()["id"]
            except TandoorError:
                pass
    _meal_type_cache.update(cache)
    return _meal_type_cache


async def meal_plan_range(start: str, end: str) -> list:
    """Every entry whose from_date falls in [start, end] (YYYY-MM-DD),
    Tandoor's own from_date/to_date filter on /api/meal-plan/."""
    return await _all_pages(f"{MEAL_PLAN}?from_date={start}&to_date={end}")


async def meal_plan_add(date: str, recipe_id, meal_type: str, servings: float = 1) -> "int | None":
    types = await _ensure_meal_types()
    type_id = types.get(meal_type.strip().lower())
    if not type_id:
        return None
    try:
        r = await _call("POST", MEAL_PLAN, json_body={
            "recipe": recipe_id, "servings": servings, "from_date": date,
            "meal_type": type_id})
        return r.json().get("id")
    except TandoorError:
        return None


async def meal_plan_remove(entry_id) -> bool:
    try:
        await _call("DELETE", f"{MEAL_PLAN}{entry_id}/")
        return True
    except TandoorError:
        return False


async def random_recipe() -> "dict | None":
    """One recipe, Tandoor's own real random pick (confirmed live:
    ?random=true is a genuine server-side filter, not something faked up
    client-side against the whole list)."""
    try:
        r = await _call("GET", f"{RECIPE}?random=true&limit=1")
    except TandoorError:
        return None
    results = r.json().get("results") or []
    return results[0] if results else None


async def recipe_names_using_food(food_id) -> list:
    """Just the recipe names/ids using this food -- no per-recipe detail
    fetch, unlike recipes_using_food() below (which needs the real
    ingredient-line text for the usage page, at the cost of one extra
    call per recipe). Cheap enough to call for EVERY row on /admin/items'
    own condensed list -- Jason's own request (2026-08-24): "I want like
    how I can see what recipe and it's from" right there, not one click
    away."""
    results = await _all_pages(f"{RECIPE}?foods={food_id}")
    return [{"id": r.get("id"), "name": r.get("name") or "Untitled"} for r in results]


async def recipes_using_food(food_id) -> list:
    """Every recipe that has this food in an ingredient line, plus the
    line's own ORIGINAL TEXT as written -- Jason's own request
    (2026-08-24): "let me see the recipe where it comes from... open up
    the actual recipe and read it for myself" before deciding whether a
    proposed merge (or a food he's just browsing) actually makes sense.
    Confirmed live: Tandoor's own `foods=` filter on /api/recipe/ finds
    every recipe using a food WITHOUT walking all 1500+ foods by hand;
    `original_text` on each ingredient (confirmed live, a real field) is
    what the source page or the model actually wrote, not Tandoor's own
    normalized food/unit/amount split of it."""
    results = await _all_pages(f"{RECIPE}?foods={food_id}")
    out = []
    for rec in results:
        full = await get_recipe(rec.get("id"))
        text = ""
        for step in (full or {}).get("steps") or []:
            for ing in step.get("ingredients") or []:
                if (ing.get("food") or {}).get("id") == food_id:
                    text = ing.get("original_text") or text
        out.append({"id": rec.get("id"), "name": rec.get("name") or "Untitled",
                   "original_text": text})
    return out


async def recipes_using_tool(tool_id) -> list:
    """Same as recipes_using_food() above, for a tool -- there is no
    per-line text for a tool (it is a recipe-level Keyword, not an
    ingredient), so this is just which recipes carry it."""
    results = await _all_pages(f"{RECIPE}?keywords={tool_id}")
    return [{"id": rec.get("id"), "name": rec.get("name") or "Untitled"}
           for rec in results]


async def merge_food(food_id, into_id) -> bool:
    """Tandoor's own real merge, confirmed live against its OpenAPI schema
    (`PUT /api/food/{id}/merge/{target}/`) -- `food_id` is absorbed into
    `into_id`: every ingredient reference gets reassigned on Tandoor's own
    side and `food_id` stops existing. This is why dedupe.py never has to
    hand-roll reassigning ingredient foreign keys itself.

    CAUGHT LIVE (2026-08-24), the expensive way: a real merge pass ran
    for a while before anyone noticed it was silently orphaning already-
    found pictures -- Tandoor's own merge has no idea foodphotos.py's
    picture cache or iteminfo.py's info card even exist, since neither
    ever lived in Tandoor to begin with. 30 pictures were lost before
    this was caught and fixed; carrying both over is now part of what
    "merge" means here, not an afterthought."""
    try:
        await _call("PUT", f"/api/food/{food_id}/merge/{into_id}/",
                   json_body={"name": ""})
    except TandoorError:
        return False
    foodphotos.carry_over(food_id, into_id)
    iteminfo.carry_over("food", food_id, into_id)
    return True


async def merge_tool(tool_id, into_id) -> bool:
    """Same as merge_food(), for the Keyword tools live under
    (`PUT /api/keyword/{id}/merge/{target}/`) -- see merge_food()'s own
    comment for why the picture/info carry-over exists at all."""
    try:
        await _call("PUT", f"/api/keyword/{tool_id}/merge/{into_id}/",
                   json_body={"name": ""})
    except TandoorError:
        return False
    toolimages.carry_over(tool_id, into_id)
    iteminfo.carry_over("tool", tool_id, into_id)
    return True


async def get_recipe(recipe_id) -> "dict | None":
    """The full recipe as Tandoor has it right now, or None. Same
    reasoning as mealie.py's get_recipe(): needed to look up a step's real
    id from its 1-based index before any per-step call."""
    if not recipe_id:
        return None
    try:
        return (await _call("GET", RECIPE_DETAIL.format(id=recipe_id))).json()
    except TandoorError:
        return None


async def _step_id_for(recipe_id, step_index: int):
    recipe = await get_recipe(recipe_id)
    if not recipe:
        return None
    steps = recipe.get("steps") or []
    if not (1 <= step_index <= len(steps)):
        return None
    return steps[step_index - 1]["id"]


async def _set_step_file(recipe_id, step_index: int, image_bytes: bytes,
                        filename: str) -> bool:
    step_id = await _step_id_for(recipe_id, step_index)
    if step_id is None:
        return False
    files = {"file": (filename, image_bytes, "application/octet-stream")}
    r = await _call("POST", USER_FILE, files=files, data={"name": filename})
    file_id = r.json().get("id")
    if not file_id:
        return False
    await _call("PATCH", STEP_DETAIL.format(id=step_id), json_body={"file": file_id})
    return True


async def push_step_illustration(recipe_id, step_index: int, png: bytes,
                                caption: str = "") -> bool:
    """Upload a drawn step illustration (see app/art.py). `caption` is
    accepted for call-site symmetry with the old mealie.py function but
    unused -- there is no markdown text to embed it into any more; the
    picture is the field, not a link inside a paragraph."""
    return await _set_step_file(recipe_id, step_index, png, f"step-{step_index}.png")


async def push_step_photo(recipe_id, step_index: int, url: str, alt: str = "") -> bool:
    """A real photo the source page already had, matched to a step by
    importer.attach_images(). Fetched and re-uploaded, same reasoning as
    push_image_from_url() for the hero photo -- Tandoor takes bytes, not
    a URL to fetch server-side."""
    try:
        core.is_public_url(url)
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": core.USER_AGENT,
                                                   **core.BROWSER_HEADERS})
            resp.raise_for_status()
    except (core.Blocked, httpx.HTTPError):
        return False
    return await _set_step_file(recipe_id, step_index, resp.content,
                               f"step-{step_index}.{_ext_from_url(url)}")


async def push_own_step_photo(recipe_id, step_index: int, file_bytes: bytes,
                             ext: str) -> bool:
    """The person's own uploaded photo of a step."""
    return await _set_step_file(recipe_id, step_index, file_bytes,
                               f"step-{step_index}.{ext}")


async def remove_step_file(recipe_id, step_index: int) -> bool:
    """Undo any of the three push_* functions above -- point the step's
    file back at null. Unlike mealie.py's remove_*, this needs no regex and
    leaves nothing orphaned in the text, because there was never any text
    involved."""
    step_id = await _step_id_for(recipe_id, step_index)
    if step_id is None:
        return False
    await _call("PATCH", STEP_DETAIL.format(id=step_id), json_body={"file": None})
    return True
