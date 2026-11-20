"""Nutrition facts per food (USDA FoodData Central), and a recipe-level
estimate built from arithmetic against those baselines -- no second lookup.

See app/nutrition.py for why USDA and not a model, and app/recipe_nutrition.py
for exactly what the recipe-level estimate is (and is not).

    --show        status: how many foods have a baseline, how many recipes
                  got an estimate from them
    --one NAME    look up ONE food by name, print what USDA matched
    --backfill    look up every food that has no baseline yet, then estimate
                  nutrition for every recipe from the baselines now on hand

NOTHING WRITES WITHOUT --write, same rule as mealie_fix.py and the same
reason: --backfill touches every food and every recipe in the library.

    docker compose -f docker-compose.yml -f docker-compose.web.yml run --rm `
      --entrypoint python kitchen nutrition_fix.py --show
"""
import asyncio
import json
import sys

from mealie_fix import Fail, Mealie, head

from app import core, nutrition, recipe_nutrition, store


def _load_used_in(extras: dict) -> list:
    """extras["usedIn"] on the wire -- same JSON-string-column reasoning as
    _load_nutrition(). Empty list, never None, since "used in nothing" is a
    real and common answer, not a missing-data case."""
    raw = (extras or {}).get("usedIn")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return []


def _load_nutrition(extras: dict):
    """extras["nutrition"] on the wire, or None. CAUGHT LIVE: Mealie stores
    each `extras` value as its own SQL row (ingredient_food_extras), and the
    column is a plain string -- a nested dict there 400s with a raw
    sqlite3.ProgrammingError. So this is stored JSON-encoded, not as a dict,
    and has to be decoded back out on every read."""
    raw = (extras or {}).get("nutrition")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None


async def load_foods(m: Mealie) -> list:
    out, page = [], 1
    while True:
        data = await m.get(f"/api/foods?perPage=100&page={page}")
        items = data.get("items") or []
        out.extend(items)
        if len(items) < 100:
            break
        page += 1
    return out


async def load_recipes(m: Mealie) -> list:
    out, page = [], 1
    while True:
        data = await m.get(f"/api/recipes?perPage=100&page={page}")
        items = data.get("items") or []
        out.extend(items)
        if len(items) < 100:
            break
        page += 1
    return out


def _baseline_map(foods: list) -> dict:
    """name.lower() -> nutrition dict, for whatever foods already carry one."""
    out = {}
    for f in foods:
        n = _load_nutrition(f.get("extras"))
        if n:
            out[f["name"].strip().lower()] = n
    return out


async def cmd_status(m: Mealie) -> int:
    if not nutrition.configured():
        print("KITCHEN_USDA_KEY is not set -- see .env for how to get a free one.")
    else:
        ok, why = await nutrition.health()
        print(f"USDA FoodData Central: {'ok' if ok else 'FAILED'} -- {why}")

    foods = await load_foods(m)
    have = sum(1 for f in foods if _load_nutrition(f.get("extras")))
    have_used_in = sum(1 for f in foods if _load_used_in(f.get("extras")))
    print(f"\nFoods: {len(foods)} total, {have} with a nutrition baseline, "
         f"{len(foods) - have} still to look up")
    print(f"      {have_used_in} used in at least one recipe")

    recipes = await load_recipes(m)
    print(f"Recipes: {len(recipes)} total")
    return 0


async def cmd_one(m: Mealie, name: str) -> int:
    if not nutrition.configured():
        print("KITCHEN_USDA_KEY is not set -- see .env.")
        return 1
    result = await nutrition.search(name)
    if not result:
        print(f"  no USDA match for {name!r} -- asking the LLM instead")
        result = await nutrition.llm_guess(name)
    if not result:
        print(f"  no match at all (USDA or LLM) for {name!r}")
        return 1
    fdc = f", fdcId {result['fdcId']}" if "fdcId" in result else ""
    print(f"  matched: {result['matchedName']}  ({result['dataType']}{fdc})")
    for key, cell in result["nutrients"].items():
        print(f"    {key:20} {cell['value']} {cell['unit']}")
    return 0


async def cmd_backfill(m: Mealie, limit: int) -> int:
    if not nutrition.configured():
        print("KITCHEN_USDA_KEY is not set -- see .env.")
        return 1

    head("Looking up food baselines")
    foods = await load_foods(m)
    baselines = _baseline_map(foods)
    looked_up = matched = 0
    for f in foods:
        key = f["name"].strip().lower()
        if key in baselines:
            continue
        if looked_up >= limit:
            continue
        looked_up += 1
        result = await nutrition.search(f["name"])
        guessed = False
        if not result:
            # USDA HAS NOTHING -- fall back to the model, and only here.
            # See nutrition.py's llm_guess() docstring for why this is the
            # one place a guess is allowed to stand in for real data.
            result = await nutrition.llm_guess(f["name"])
            guessed = result is not None
        if not result:
            print(f"  no match at all (USDA or LLM): {f['name']!r}")
            continue
        matched += 1
        baselines[key] = result
        extras = dict(f.get("extras") or {})
        extras["nutrition"] = json.dumps(result)
        # FULL PUT, not a partial patch -- Mealie's food-update route wants
        # the whole IngredientFood back, and dropping fields it required
        # (id, name) here is how a "just update extras" call 422s instead.
        await m.put(f"/api/foods/{f['id']}", {**f, "extras": extras})
        how = "LLM ESTIMATE, not measured" if guessed else result["dataType"]
        print(f"  {f['name']!r} -> {result['matchedName']} ({how})")
    print(f"\n{matched}/{looked_up} newly looked up matched "
         f"({len(baselines)}/{len(foods)} foods have a baseline now)")

    head("Estimating recipe nutrition, and who uses what, in one pass")
    recipes = await load_recipes(m)
    estimated = 0
    used_in: dict = {}  # food id -> set of recipe names, built as we go
    for r in recipes:
        full = await m.get(f"/api/recipes/{r['slug']}")
        ings = full.get("recipeIngredient") or []
        name = full.get("name") or r.get("name") or r["slug"]
        for ing in ings:
            fid = (ing.get("food") or {}).get("id")
            if fid:
                used_in.setdefault(fid, set()).add(name)
        if not ings:
            continue
        totals, used, total = recipe_nutrition.totals_for(ings, baselines)
        if not used:
            continue
        est = recipe_nutrition.mealie_shape(totals)
        if est:
            # CAUGHT LIVE: the numbers alone are not enough -- Mealie's OWN
            # frontend hides the whole nutrition panel per recipe behind
            # settings.showNutrition, which defaults to False and stays
            # False on a PATCH that only touches "nutrition". Every recipe
            # had real data and nothing showed at :9443 because of this one
            # flag.
            settings = dict(full.get("settings") or {})
            settings["showNutrition"] = True
            await m.patch(f"/api/recipes/{r['slug']}", {"nutrition": est, "settings": settings})
            estimated += 1

        # The FULL totals -- vitamins and minerals too, which Mealie's own
        # nutrition field has no room for -- saved into Kitchen's own
        # record so /kitchen's %DV chart (see recipe_nutrition.py) has
        # something to draw without a live Mealie round trip per page view.
        draft = store.get_by_slug(r["slug"])
        if draft:
            recipe = draft["recipe"]
            recipe["nutrition_full"] = totals
            recipe["nutrition_servings"] = full.get("recipeServings") or 1
            store.update_recipe(draft["id"], recipe)

        print(f"  {r['name']!r}: estimated from {used}/{total} ingredients")
    print(f"\n{estimated}/{len(recipes)} recipes got a nutrition estimate "
         "(some ingredients have no convertible unit -- see the module "
         "docstring in app/recipe_nutrition.py)")

    head("Recording which recipes use each food")
    touched = 0
    for f in foods:
        names = sorted(used_in.get(f["id"], set()))
        encoded = json.dumps(names)
        if (f.get("extras") or {}).get("usedIn") == encoded:
            continue
        extras = dict(f.get("extras") or {})
        extras["usedIn"] = encoded
        await m.put(f"/api/foods/{f['id']}", {**f, "extras": extras})
        touched += 1
    print(f"\n{touched}/{len(foods)} foods' used-in list updated")
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
        if cmd == "--show":
            return await cmd_status(m)
        if cmd == "--one":
            if len(argv) < 2:
                print("  --one needs a food name")
                return 2
            return await cmd_one(m, argv[1])
        if cmd == "--backfill":
            limit = int(argv[1]) if len(argv) > 1 else 10_000
            return await cmd_backfill(m, limit)
        print(USAGE)
        return 2
    except Fail as e:
        print(f"\nMealie said no: {e}")
        return 1
    finally:
        await m.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:])))
