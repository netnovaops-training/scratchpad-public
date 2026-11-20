"""Turn 207 recipes' worth of plain text into foods, units and tags.

WHAT THE PROBE FOUND, AND WHAT EACH PART MEANS
----------------------------------------------
    recipes 207 | foods 0 | units 0 | tags 0 | categories 0

Nothing has ever been parsed. Every ingredient in the library is one string in
a `note`, with `food: None`, `unit: None`, `quantity: 0.0`. There is nothing
inside a food item because there are no food items.

    group 'Home'      private True
    household 'Guests'  private True   lockRecipeEditsFromOtherHouseholds True
    household 'Family'  private True   lockRecipeEditsFromOtherHouseholds True

That is why nobody can see anything. A private GROUP blocks all access to its
recipes -- including recipes marked public -- and a private household blocks
the same thing one level down. Two private households in a private group is
three closed doors between Mama and a recipe you saved.

WHAT THIS DOES, IN THREE SEPARATE STEPS YOU RUN YOURSELF
--------------------------------------------------------
    --show       print every group and household preference in full. Reads only.
    --share      open the doors: group not private, households not private,
                 and recipes visible across households.
    --seed       ask Mealie to load its own standard food and unit lists.
    --one SLUG   parse ONE recipe and show you before and after.
    --backfill   parse the rest.
    --tags       push tags/category/tools onto every recipe already kept,
                 from Kitchen's own copy -- catches up anything imported
                 before keep() started sending these automatically.
    --prune-organizers
                 delete every tag, category and tool attached to ZERO
                 recipes. Mostly cleans up after the tools-guessing bug
                 (see labels.py's TOOLS_SYSTEM) that once created a new
                 tool organizer for nearly every wrong guess -- fixing the
                 guess did not remove the orphaned tools it already made.

NOTHING WRITES WITHOUT --write. Every step prints what it would do and stops.
That is not politeness: `--backfill` rewrites the ingredient list of every
recipe you own, and a mistake there is 207 mistakes.

    docker compose -f docker-compose.yml -f docker-compose.web.yml run --rm `
      --entrypoint python kitchen mealie_fix.py --show
"""
import asyncio
import json
import sys

import httpx

from app import core, labels, store
# Reaching into mealie.py's own tag/category/tool builders rather than
# duplicating them -- they are the exact same functions app/mealie.py's
# keep() already runs on every new import, so a recipe backfilled by this
# script and one imported today get identically-shaped tags, not two
# implementations that can quietly drift apart.
from app.mealie import _category_for, _finalize_organizers, _tags_for, _tools_for

TIMEOUT = 60.0


class Fail(Exception):
    pass


def head(t: str) -> None:
    print()
    print(t)
    print("-" * len(t))


class Mealie:
    def __init__(self, write: bool) -> None:
        self.write = write
        self.client = httpx.AsyncClient(
            base_url=core.MEALIE_URL,
            headers={"Authorization": f"Bearer {core.MEALIE_TOKEN}"},
            timeout=TIMEOUT)
        # name -> id, filled as we go. 207 recipes share a few hundred foods;
        # looking each one up every time would be thousands of round trips for
        # an answer that does not change.
        self.foods: dict = {}
        self.units: dict = {}

    async def close(self):
        await self.client.aclose()

    async def _req(self, method: str, path: str, **kw):
        r = await self.client.request(method, path, **kw)
        if r.status_code >= 400:
            # The BODY, not just the code. Mealie's 422s name the field that
            # was wrong, and that is the entire diagnostic.
            raise Fail(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.content else {}

    async def get(self, path: str, **kw):
        return await self._req("GET", path, **kw)

    async def put(self, path: str, body: dict):
        if not self.write:
            print(f"    would PUT {path}  {json.dumps(body)[:160]}")
            return {}
        return await self._req("PUT", path, json=body)

    async def post(self, path: str, body: dict):
        if not self.write:
            print(f"    would POST {path}  {json.dumps(body)[:160]}")
            return {}
        return await self._req("POST", path, json=body)

    async def patch(self, path: str, body: dict):
        if not self.write:
            print(f"    would PATCH {path}  {json.dumps(body)[:200]}")
            return {}
        return await self._req("PATCH", path, json=body)

    async def delete(self, path: str):
        if not self.write:
            print(f"    would DELETE {path}")
            return {}
        return await self._req("DELETE", path)


async def try_put(m: "Mealie", attempts: list) -> bool:
    """Try each (path, body) until one is accepted.

    WHY THIS IS NOT ONE CALL. Mealie moves these routes between versions and a
    422 here names a field, not a fix -- so rather than pin one shape and have
    the whole thing quietly do nothing on the next upgrade, try the shapes that
    have existed and SAY which one worked. On a total failure it prints every
    refusal, which is the thing worth having at 11pm.
    """
    problems = []
    for path, body in attempts:
        try:
            await m.put(path, body)
            if m.write:
                print(f"    accepted by {path}")
            return True
        except Fail as e:
            problems.append(str(e))
    print("    NOT APPLIED. Mealie refused every shape:")
    for p in problems:
        print(f"      {p}")
    print("    Do it by hand: Settings > Admin > Households (and Groups),")
    print("    untick 'private' and the setting about other households.")
    return False


# --------------------------------------------------------------------- show

async def cmd_show(m: Mealie) -> int:
    head("Groups")
    for g in (await m.get("/api/admin/groups?perPage=50")).get("items", []):
        print(f"  {g.get('name')}  ({g.get('id')})")
        print(f"    {json.dumps(g.get('preferences') or {}, indent=6, sort_keys=True)}")
    head("Households")
    for h in (await m.get("/api/admin/households?perPage=100")).get("items", []):
        print(f"  {h.get('name')}  ({h.get('id')})  group={h.get('groupId')}")
        print(f"    {json.dumps(h.get('preferences') or {}, indent=6, sort_keys=True)}")
    head("Where the recipes actually live")
    counts: dict = {}
    page = 1
    while True:
        data = await m.get(f"/api/recipes?perPage=100&page={page}")
        items = data.get("items") or []
        if not items:
            break
        for r in items:
            key = (r.get("householdId"), r.get("groupId"))
            counts[key] = counts.get(key, 0) + 1
        if len(items) < 100:
            break
        page += 1
    for (hid, gid), n in sorted(counts.items(), key=lambda p: -p[1]):
        print(f"  {n:4} recipes   household={hid}  group={gid}")
    return 0


# -------------------------------------------------------------------- share

async def cmd_share(m: Mealie) -> int:
    """Open the three doors, in the order they actually block.

    A PRIVATE GROUP BLOCKS EVERYTHING, so it goes first -- opening the
    households while the group stays private changes nothing you can see, and
    it would look like this did not work.
    """
    head("Group")
    for g in (await m.get("/api/admin/groups?perPage=50")).get("items", []):
        prefs = dict(g.get("preferences") or {})
        if prefs.get("privateGroup") is False:
            print(f"  '{g.get('name')}' is already open.")
            continue
        prefs["privateGroup"] = False
        print(f"  '{g.get('name')}': private -> open")
        await try_put(m, [
            (f"/api/admin/groups/{g['id']}",
             {"id": g["id"], "name": g["name"], "preferences": prefs}),
            # Fallback: the non-admin route, which edits the token owner's own
            # group. Enough when there is only one, which there is here.
            ("/api/groups/preferences", prefs),
        ])

    head("Households")
    for h in (await m.get("/api/admin/households?perPage=100")).get("items", []):
        prefs = dict(h.get("preferences") or {})
        want = {
            # Blocks everything for this household, same as the group flag.
            "privateHousehold": False,
            # THIS is the one that lets Mama see a recipe you saved. Its name
            # is about EDITS but in v3 it is what gates visibility across
            # households -- which is why turning off "private" alone was not
            # enough and the library still looked empty.
            "lockRecipeEditsFromOtherHouseholds": False,
            # New recipes are shareable rather than sealed. Without it every
            # recipe from here on would need the toggle flipped by hand.
            "recipePublic": True,
        }
        changes = {k: v for k, v in want.items() if prefs.get(k) != v}
        if not changes:
            print(f"  '{h.get('name')}' is already open.")
            continue
        prefs.update(want)
        print(f"  '{h.get('name')}': {', '.join(changes)}")
        await try_put(m, [
            (f"/api/admin/households/{h['id']}",
             {"id": h["id"], "name": h["name"], "groupId": h.get("groupId"),
              "preferences": prefs}),
            (f"/api/admin/households/{h['id']}", {"preferences": prefs}),
            # Last resort, and only good for the token owner's own household.
            ("/api/households/preferences", prefs),
        ])

    print()
    print("  Existing recipes each carry their own public flag as well.")
    print("  Run  --publish  to turn that on for the ones already saved.")
    return 0


async def cmd_publish(m: Mealie) -> int:
    head("Marking existing recipes shareable")
    page, done = 1, 0
    while True:
        data = await m.get(f"/api/recipes?perPage=50&page={page}")
        items = data.get("items") or []
        if not items:
            break
        for r in items:
            slug = r.get("slug")
            full = await m.get(f"/api/recipes/{slug}")
            settings = dict(full.get("settings") or {})
            if settings.get("public") is True:
                continue
            settings["public"] = True
            await m.patch(f"/api/recipes/{slug}", {"settings": settings})
            done += 1
        if len(items) < 50:
            break
        page += 1
    print(f"  {done} recipes {'updated' if m.write else 'would be updated'}.")
    return 0


# --------------------------------------------------------------------- seed

async def cmd_seed(m: Mealie) -> int:
    """Mealie ships its own food and unit lists. Use them rather than inventing.

    Seeding first means the parser MATCHES rather than creating a near-duplicate
    every time -- "onion" and "onions" and "Onion" becoming three foods is the
    thing that makes a food list useless.
    """
    head("Seeding Mealie's own lists")
    for what in ("foods", "units"):
        print(f"  {what}")
        try:
            await m.post(f"/api/groups/seeders/{what}", {"locale": "en-US"})
        except Fail as e:
            print(f"    {e}")
    if m.write:
        for what in ("foods", "units"):
            n = (await m.get(f"/api/{what}?perPage=1")).get("total")
            print(f"  {what} now: {n}")
    return 0


# ------------------------------------------------------------------- parsing

async def parse_lines(m: Mealie, lines: list) -> list:
    """Mealie's own parser. Tries the good one, falls back to the blunt one."""
    for parser in ("nlp", "brute"):
        try:
            return await m._req("POST", "/api/parser/ingredients",
                                json={"parser": parser, "ingredients": lines})
        except Fail as e:
            print(f"    parser '{parser}' refused: {e}")
    raise Fail("neither parser would take that")


async def load_lookup(m: Mealie) -> None:
    for what, into in (("foods", m.foods), ("units", m.units)):
        page = 1
        while True:
            data = await m.get(f"/api/{what}?perPage=100&page={page}")
            items = data.get("items") or []
            for it in items:
                name = (it.get("name") or "").strip().lower()
                if name:
                    into[name] = it["id"]
            if len(items) < 100:
                break
            page += 1
    print(f"  known already: {len(m.foods)} foods, {len(m.units)} units")


async def ensure(m: Mealie, what: str, cache: dict, name: str):
    """The id for this food/unit, creating it only if it is genuinely new."""
    key = (name or "").strip().lower()
    if not key:
        return None
    if key in cache:
        return cache[key]
    if not m.write:
        return None
    made = await m._req("POST", f"/api/{what}", json={"name": name.strip()})
    cache[key] = made.get("id")
    return cache[key]


async def fix_recipe(m: Mealie, slug: str, loud: bool = False) -> str:
    full = await m.get(f"/api/recipes/{slug}")
    ings = full.get("recipeIngredient") or []
    lines = [(i.get("originalText") or i.get("note") or "").strip() for i in ings]
    lines = [l for l in lines if l]
    if not lines:
        return "no ingredients"
    if all(i.get("food") for i in ings):
        return "already done"

    parsed = await parse_lines(m, lines)
    if loud:
        head(f"{full.get('name')} — what the parser made of it")
        for p in parsed:
            ing = p.get("ingredient") or {}
            print(f"  {p.get('input','')!r}")
            print(f"      qty  {ing.get('quantity')}")
            print(f"      unit {(ing.get('unit') or {}).get('name')}")
            print(f"      food {(ing.get('food') or {}).get('name')}")
            print(f"      note {ing.get('note')!r}")

    out = []
    for original, p in zip(lines, parsed):
        ing = p.get("ingredient") or {}
        food = ing.get("food") or {}
        unit = ing.get("unit") or {}
        food_name = food.get("name", "")
        unit_name = unit.get("name", "")
        food_id = food.get("id") or await ensure(m, "foods", m.foods, food_name)
        unit_id = unit.get("id") or await ensure(m, "units", m.units, unit_name)
        entry = {
            "quantity": ing.get("quantity") or 0,
            "note": ing.get("note") or "",
            # KEPT VERBATIM. If the parser got it wrong, the words somebody
            # actually wrote are still on the recipe and can be read.
            "originalText": original,
            "disableAmount": False,
        }
        # CAUGHT LIVE: {"id": ...} alone 422s -- Mealie's IngredientFood-Input
        # and IngredientUnit-Input both require `name` too, not just `id`.
        if food_id:
            entry["food"] = {"id": food_id, "name": food_name}
        if unit_id:
            entry["unit"] = {"id": unit_id, "name": unit_name}
        out.append(entry)

    settings = dict(full.get("settings") or {})
    # WITHOUT THIS THE WHOLE EXERCISE IS INVISIBLE. When disableAmount is true
    # Mealie shows the plain text and ignores food and unit entirely -- the
    # foods would exist in the database and the recipe would look unchanged.
    settings["disableAmount"] = False
    await m.patch(f"/api/recipes/{slug}",
                  {"recipeIngredient": out, "settings": settings})
    return f"{len(out)} ingredients"


async def cmd_one(m: Mealie, slug: str) -> int:
    await load_lookup(m)
    print(f"  {slug}: {await fix_recipe(m, slug, loud=True)}")
    if m.write:
        full = await m.get(f"/api/recipes/{slug}")
        head("And how it reads back")
        for i in (full.get("recipeIngredient") or [])[:6]:
            print(f"  qty={i.get('quantity')} unit={(i.get('unit') or {}).get('name')} "
                  f"food={(i.get('food') or {}).get('name')} note={i.get('note')!r}")
    return 0


async def cmd_backfill(m: Mealie, limit: int) -> int:
    head("Backfilling")
    await load_lookup(m)
    page, done, skipped, failed = 1, 0, 0, 0
    while done + skipped < limit:
        data = await m.get(f"/api/recipes?perPage=50&page={page}")
        items = data.get("items") or []
        if not items:
            break
        for r in items:
            if done + skipped >= limit:
                break
            slug = r.get("slug")
            try:
                result = await fix_recipe(m, slug)
            except Fail as e:
                failed += 1
                print(f"  !! {slug}: {e}")
                continue
            if result == "already done":
                skipped += 1
            else:
                done += 1
                print(f"  {slug}: {result}")
        if len(items) < 50:
            break
        page += 1
    print()
    print(f"  {done} done, {skipped} already had foods, {failed} failed")
    if m.write:
        print(f"  foods now: {(await m.get('/api/foods?perPage=1')).get('total')}")
        print(f"  units now: {(await m.get('/api/units?perPage=1')).get('total')}")
    return 0


# ------------------------------------------------------------------- status

async def cmd_status(m: Mealie) -> int:
    """What is done and what is not, in words rather than JSON.

    THIS EXISTS BECAUSE THE PREVIOUS INSTRUCTIONS WERE SIX COMMANDS LONG and
    every one of them was a dry run unless you remembered --write. "It still
    does not work" and "I ran the dry run" look identical from here, so the
    tool should be able to say which it was.
    """
    head("Where things stand")
    counts = {}
    for what in ("recipes", "foods", "units"):
        counts[what] = (await m.get(f"/api/{what}?perPage=1")).get("total") or 0
    tags = (await m.get("/api/organizers/tags?perPage=1")).get("total") or 0
    print(f"  recipes {counts['recipes']}   foods {counts['foods']}   "
          f"units {counts['units']}   tags {tags}")

    print()
    if counts["foods"] == 0:
        print("  FOODS: none. The parse has not run (or ran without --write).")
    else:
        print(f"  FOODS: {counts['foods']}. The parse has run.")

    # Sample a few recipes rather than all of them -- enough to tell "none" from
    # "some" without 207 round trips.
    data = await m.get("/api/recipes?perPage=8")
    structured = plain = amounts_off = 0
    for r in (data.get("items") or []):
        full = await m.get(f"/api/recipes/{r['slug']}")
        ings = full.get("recipeIngredient") or []
        if any(i.get("food") for i in ings):
            structured += 1
        elif ings:
            plain += 1
        if (full.get("settings") or {}).get("disableAmount"):
            amounts_off += 1
    print(f"  Of 8 recipes sampled: {structured} have foods, {plain} are still plain text.")
    if amounts_off:
        # The trap. Everything can be correct underneath and still look
        # unchanged, because Mealie is told to show the text and ignore it.
        print(f"  {amounts_off} of them have 'disableAmount' ON, which makes Mealie")
        print("  show the plain text and ignore food and unit entirely.")

    print()
    for g in (await m.get("/api/admin/groups?perPage=50")).get("items", []):
        p = g.get("preferences") or {}
        state = "CLOSED - blocks everything below it" if p.get("privateGroup") else "open"
        print(f"  group '{g.get('name')}': {state}")
    for h in (await m.get("/api/admin/households?perPage=100")).get("items", []):
        p = h.get("preferences") or {}
        bits = []
        if p.get("privateHousehold"):
            bits.append("private")
        if p.get("lockRecipeEditsFromOtherHouseholds"):
            bits.append("hidden from other households")
        print(f"  household '{h.get('name')}': {', '.join(bits) if bits else 'open'}")
    return 0


async def cmd_tags(m: Mealie) -> int:
    """Push tags, category and tools onto every recipe already in the
    library, from Kitchen's own stored copy of each one.

    WHY THIS EXISTS: keep()'s auto-tagging only started running the day it
    was added to mealie.py. Every recipe imported before that -- the whole
    library, at the time -- has an empty tag rail and no tools in Mealie
    even though Kitchen has always had the labels and course/cuisine
    sitting right there. This is the one-time catch-up; going forward,
    keep() keeps every new import in sync on its own.
    """
    kept = store.all_kept()
    updated, failed = 0, []
    for row in kept:
        slug, recipe = row["mealie_slug"], row["recipe"]
        if not recipe.get("tools"):
            # Almost none of the source pages published schema.org's `tool`
            # field (see crawl.py's _tools_from_ld() docstring) -- same
            # model fallback keep() now runs on every new import, applied
            # here to the recipes that predate that.
            try:
                recipe["tools"] = await labels.suggest_tools(recipe)
            except Exception:  # noqa: BLE001
                pass
        tags = _tags_for(recipe)
        category = _category_for(recipe)
        tools = _tools_for(recipe)
        if not (tags or category or tools):
            continue
        # ONE BAD RECIPE MUST NOT STOP THE OTHER 199. CAUGHT LIVE: a slug
        # Kitchen still has on record but Mealie no longer does (deleted
        # directly in Mealie's own UI, say) 404s -- and a second, nastier
        # bug: {"name": ..., "slug": ...} with no `id`, even for a tag that
        # already exists under that exact name and slug, makes Mealie try
        # to CREATE a new one anyway and 400 as "Recipe already exists" --
        # see _resolve_tag_like() in app/mealie.py, which
        # _finalize_organizers() runs here to attach the real id instead.
        # Either way, one bad recipe is reported at the end, not an
        # aborted run.
        try:
            payload = await _finalize_organizers(
                {"tags": tags, "recipeCategory": category, "tools": tools})
            await m.patch(f"/api/recipes/{slug}", payload)
        except Fail as e:
            failed.append((recipe.get("title", slug), str(e)))
            continue
        updated += 1
        print(f"  {recipe.get('title', slug)!r}: "
             f"{len(tags)} tag(s), {len(category)} categor{'y' if len(category)==1 else 'ies'}, "
             f"{len(tools)} tool(s)")
    print(f"\n{updated}/{len(kept)} recipes updated")
    if failed:
        print(f"{len(failed)} failed:")
        for title, why in failed:
            print(f"  {title!r}: {why[:200]}")
    return 0


_ORGANIZER_KIND = {
    "tags": "/api/organizers/tags",
    "categories": "/api/organizers/categories",
    "tools": "/api/organizers/tools",
}


async def cmd_prune_organizers(m: Mealie) -> int:
    """Delete every tag, category and tool that no recipe actually uses.

    Usage comes from the recipe LIST endpoint's own summary, which already
    carries each recipe's tags/recipeCategory/tools -- one paginated read,
    not 264 individual recipe fetches for something this cheap.
    """
    head("Reading every recipe's tags, category and tools")
    used: dict = {"tags": set(), "categories": set(), "tools": set()}
    recipes, page = [], 1
    while True:
        data = await m.get(f"/api/recipes?perPage=100&page={page}")
        items = data.get("items") or []
        recipes.extend(items)
        if len(items) < 100:
            break
        page += 1
    for r in recipes:
        for t in (r.get("tags") or []):
            used["tags"].add(t.get("id"))
        for c in (r.get("recipeCategory") or []):
            used["categories"].add(c.get("id"))
        for t in (r.get("tools") or []):
            used["tools"].add(t.get("id"))
    print(f"  {len(recipes)} recipes read")

    removed = 0
    for kind, endpoint in _ORGANIZER_KIND.items():
        head(f"Pruning unused {kind}")
        all_items, page = [], 1
        while True:
            data = await m.get(f"{endpoint}?perPage=500&page={page}")
            items = data.get("items") or []
            all_items.extend(items)
            if len(items) < 500:
                break
            page += 1
        orphans = [i for i in all_items if i.get("id") not in used[kind]]
        print(f"  {len(orphans)}/{len(all_items)} {kind} attached to zero recipes")
        for item in orphans:
            try:
                await m.delete(f"{endpoint}/{item['id']}")
            except Fail as e:
                print(f"    could not delete {item.get('name')!r}: {e}")
                continue
            removed += 1
            if m.write:
                print(f"    deleted {item.get('name')!r}")
    print(f"\n{removed} unused tag/category/tool(s) removed")
    return 0


async def cmd_all(m: Mealie) -> int:
    """Everything, in the order that works. One command, because six was too many.

    ORDER MATTERS AND IT IS NOT ARBITRARY: opening the households while the
    group is still private changes nothing you can see, and parsing before
    seeding makes the parser coin a new food for every near-miss instead of
    matching Mealie's own list.
    """
    await cmd_share(m)
    await cmd_publish(m)
    await cmd_seed(m)
    await cmd_backfill(m, 10_000)
    await cmd_tags(m)
    await cmd_status(m)
    return 0


# --------------------------------------------------------------------- main

USAGE = __doc__


async def run(argv: list) -> int:
    if not core.MEALIE_TOKEN:
        print("KITCHEN_MEALIE_TOKEN is not set.")
        return 1
    write = "--write" in argv
    argv = [a for a in argv if a != "--write"]
    if not argv:
        print(USAGE)
        return 2

    m = Mealie(write)
    if not write:
        print("DRY RUN — nothing will be changed. Add --write when it looks right.")
    try:
        cmd = argv[0]
        if cmd == "--status":
            return await cmd_status(m)
        if cmd == "--all":
            return await cmd_all(m)
        if cmd == "--show":
            return await cmd_show(m)
        if cmd == "--share":
            return await cmd_share(m)
        if cmd == "--publish":
            return await cmd_publish(m)
        if cmd == "--seed":
            return await cmd_seed(m)
        if cmd == "--one":
            if len(argv) < 2:
                print("  --one needs a recipe slug")
                return 2
            return await cmd_one(m, argv[1])
        if cmd == "--backfill":
            limit = int(argv[1]) if len(argv) > 1 else 10_000
            return await cmd_backfill(m, limit)
        if cmd == "--tags":
            return await cmd_tags(m)
        if cmd == "--prune-organizers":
            return await cmd_prune_organizers(m)
        print(USAGE)
        return 2
    except Fail as e:
        print(f"\nMealie said no: {e}")
        return 1
    finally:
        await m.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:])))
