"""Read-only: ask Mealie what it actually has, and what it will accept.

WHY A PROBE RATHER THAN A FIX
-----------------------------
Your library shows "No results found" for foods and tags, and the cause is
visible in this app's own code -- `mealie.py::_to_mealie` sends every
ingredient as a plain `note` and sends no tags at all:

    "recipeIngredient": [{"note": line} for line in recipe["ingredients"]],

Nothing has ever created a Food or a Tag, so both lists are empty and there is
nothing to look inside. That part is certain.

What is NOT certain from here is the exact shape YOUR Mealie wants back --
v3.23.1's parser route, whether a food is auto-created when it is named without
an id, and how your two households are set up. Guessing at that and writing it
straight into a working library is how recipes get mangled at scale.

So this asks, and writes NOTHING. Every request below is a GET. Run it, paste
me the output, and the fix gets written against what your machine actually
says rather than what a forum post said in 2024.

    docker compose -f docker-compose.yml -f docker-compose.web.yml `
      run --rm --entrypoint python kitchen probe_mealie.py
"""
import asyncio
import json
import sys

import httpx

from app import core


def head(title: str) -> None:
    print()
    print(title)
    print("-" * len(title))


async def get(client: httpx.AsyncClient, path: str):
    """GET, and never raise. A 404 on one route must not stop the rest."""
    try:
        r = await client.get(core.MEALIE_URL + path, timeout=20)
    except httpx.RequestError as e:
        return None, f"unreachable ({e.__class__.__name__})"
    if r.status_code >= 400:
        return None, f"HTTP {r.status_code}"
    try:
        return r.json(), ""
    except ValueError:
        return None, "not JSON"


async def main() -> int:
    if not core.MEALIE_TOKEN:
        print("KITCHEN_MEALIE_TOKEN is not set, so I cannot ask Mealie anything.")
        return 1

    client = httpx.AsyncClient(
        headers={"Authorization": f"Bearer {core.MEALIE_TOKEN}"})

    async with client:
        head("Mealie")
        about, err = await get(client, "/api/app/about")
        print(f"  url      {core.MEALIE_URL}")
        print(f"  version  {(about or {}).get('version', err or '?')}")

        head("What the library holds")
        for label, path in (("recipes", "/api/recipes?perPage=1"),
                            ("foods", "/api/foods?perPage=1"),
                            ("units", "/api/units?perPage=1"),
                            ("tags", "/api/organizers/tags?perPage=1"),
                            ("categories", "/api/organizers/categories?perPage=1")):
            data, err = await get(client, path)
            total = (data or {}).get("total")
            print(f"  {label:12} {total if total is not None else (err or '?')}")

        head("Who can see what")
        groups, err = await get(client, "/api/admin/groups?perPage=50")
        for g in (groups or {}).get("items", []):
            prefs = g.get("preferences") or {}
            print(f"  group '{g.get('name')}'")
            # THE ONE THAT BLOCKS EVERYTHING. A private group blocks all
            # outside access, including to recipes marked public.
            print(f"      private            {prefs.get('privateGroup')}")
        households, err = await get(client, "/api/admin/households?perPage=100")
        for h in (households or {}).get("items", []):
            prefs = h.get("preferences") or {}
            print(f"  household '{h.get('name')}'")
            print(f"      private            {prefs.get('privateHousehold')}")
            # THIS is almost certainly why Mama cannot see your recipes: two
            # households, and recipes do not cross between them unless this is
            # on. Settings > Admin > Households, in the web page.
            print(f"      others may see it  {prefs.get('lockRecipeEditsFromOtherHouseholds') is False or prefs.get('recipePublic')}")
            print(f"      raw preferences    {json.dumps(prefs, sort_keys=True)[:300]}")

        head("A recipe, as Mealie actually stores it")
        recipes, err = await get(client, "/api/recipes?perPage=1")
        items = (recipes or {}).get("items") or []
        if not items:
            print(f"  no recipes yet ({err or 'the library is empty'})")
        else:
            slug = items[0].get("slug")
            full, err = await get(client, f"/api/recipes/{slug}")
            if not full:
                print(f"  could not read '{slug}': {err}")
            else:
                print(f"  '{full.get('name')}'")
                print(f"      household        {full.get('householdId') or full.get('groupId')}")
                print(f"      public           {(full.get('settings') or {}).get('public')}")
                # WHEN THIS IS TRUE, Mealie shows the plain text and ignores
                # food/unit entirely -- so even correctly structured
                # ingredients would not populate the Foods list.
                print(f"      disableAmount    {(full.get('settings') or {}).get('disableAmount')}")
                print(f"      tags             {[t.get('name') for t in (full.get('tags') or [])]}")
                ings = full.get("recipeIngredient") or []
                print(f"      ingredients      {len(ings)}")
                for ing in ings[:3]:
                    print(f"        note   {ing.get('note')!r}")
                    print(f"        food   {ing.get('food')}")
                    print(f"        unit   {ing.get('unit')}")
                    print(f"        qty    {ing.get('quantity')}")

        head("Does the ingredient parser exist on this version?")
        spec, err = await get(client, "/openapi.json")
        if not spec:
            print(f"  could not read the API spec ({err}) -- open "
                  f"{core.MEALIE_PUBLIC or core.MEALIE_URL}/docs and look for 'parser'")
        else:
            paths = sorted(p for p in spec.get("paths", {})
                           if "parser" in p or "/foods" in p or "/units" in p)
            for p in paths:
                verbs = ",".join(v.upper() for v in spec["paths"][p]
                                 if v in ("get", "post", "put", "patch", "delete"))
                print(f"  {verbs:20} {p}")
            # The exact body it wants, straight from the spec rather than from
            # a forum post -- this is the bit worth having.
            for p in paths:
                if "parser" not in p:
                    continue
                body = (spec["paths"][p].get("post") or {}).get("requestBody") or {}
                ref = json.dumps(body)[:400]
                print(f"\n  body for POST {p}:\n    {ref}")

    print()
    print("Nothing was changed. Paste the above back to me.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
