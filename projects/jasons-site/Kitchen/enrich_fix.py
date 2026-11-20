"""Going back to a recipe's own source page for whatever the first import
missed: nutrition the site published, its own rating and reviews, and any
ingredient line the steps give an amount for but the list itself left bare
("salt and pepper" with nothing in front of it).

Same three pieces as the "Check the source for anything missed" button on
a recipe's own guide page (see _refresh_one() in app/main.py) -- this is
that same idea run once across the WHOLE library instead of recipe by
recipe, for everything that was imported before any of this existed. See
app/crawl.py's fill_ingredients_from_steps(), _nutrition_from_ld(), and
_reviews_from_ld() for what each piece actually does and why.

    --show        how many kept recipes are missing nutrition, reviews,
                  or have a bare ingredient line
    --backfill    go through every kept recipe with a source URL and fill
                  in whatever is missing

NOTHING WRITES WITHOUT --write, same rule as every other _fix.py script
here -- this touches every recipe in the library, and re-fetches every
recipe's source page, which is slow and impolite to run twice by accident.

    docker compose -f docker-compose.yml -f docker-compose.web.yml run --rm `
      --entrypoint python kitchen enrich_fix.py --show
"""
import asyncio
import sys

from app import core, crawl, mealie, store


def head(t: str) -> None:
    print()
    print(t)
    print("-" * len(t))


def _has_bare_ingredient(ingredients: list) -> bool:
    return any(crawl._looks_bare(i) for i in ingredients)


async def cmd_status() -> int:
    recs = store.kept_with_ids()
    no_source = missing_nutrition = missing_reviews = has_bare = 0
    for r in recs:
        recipe = r["recipe"]
        if not recipe.get("source"):
            no_source += 1
        if not recipe.get("nutrition_stated") and not recipe.get("nutrition_full"):
            missing_nutrition += 1
        if not recipe.get("source_reviews"):
            missing_reviews += 1
        if _has_bare_ingredient(recipe.get("ingredients") or []):
            has_bare += 1
    print(f"{len(recs)} recipes in the library")
    print(f"  {no_source} have no source URL to refresh from")
    print(f"  {missing_nutrition} have neither a computed nor a stated nutrition estimate")
    print(f"  {missing_reviews} have no reviews captured")
    print(f"  {has_bare} have at least one ingredient line with no real amount")
    return 0


async def _refresh_one(r: dict, write: bool) -> str:
    """Same logic as main.py's _refresh_one(), run standalone. Returns a
    one-line status for the --backfill printout, "nothing new" when there
    was nothing to add."""
    draft_id = r["id"]
    recipe = r["recipe"]
    before = recipe.get("ingredients") or []
    changed = []

    new_ings = await crawl.fill_ingredients_from_steps(before, recipe.get("steps") or [])
    if new_ings != before:
        changed.append(f"{len(new_ings) - len(before):+d} ingredient line(s)")
        recipe["ingredients"] = new_ings

    source = recipe.get("source", "")
    if source and (not recipe.get("nutrition_stated")
                   or not recipe.get("source_reviews")
                   or not recipe.get("source_rating")):
        try:
            final_url, page_html = await crawl.fetch(source)
            fresh = crawl.from_jsonld(crawl.read(page_html), final_url)
        except Exception:  # noqa: BLE001 -- a dead/blocked source page just
            fresh = None    # means nothing more to add from it, not a crash
        if fresh:
            if not recipe.get("nutrition_stated") and fresh.get("nutrition_stated"):
                recipe["nutrition_stated"] = fresh["nutrition_stated"]
                changed.append("nutrition")
            if not recipe.get("source_reviews") and fresh.get("source_reviews"):
                recipe["source_reviews"] = fresh["source_reviews"]
                changed.append(f"{len(fresh['source_reviews'])} review(s)")
            if not recipe.get("source_rating") and fresh.get("source_rating"):
                recipe["source_rating"] = fresh["source_rating"]
                changed.append("rating")

    if not changed:
        return "nothing new"

    if write:
        store.update_recipe(draft_id, recipe)
        slug = r.get("mealie_slug")
        if slug:
            try:
                await mealie.push_ingredients(slug, recipe["ingredients"])
                await mealie.push_recipe_nutrition(slug)
            except Exception:  # noqa: BLE001 -- Kitchen's own save above
                pass          # already succeeded either way
    return ("would add: " if not write else "added: ") + ", ".join(changed)


async def cmd_backfill(write: bool) -> int:
    recs = store.kept_with_ids()
    head(f"Checking {len(recs)} recipes against their own source pages")
    touched = 0
    for r in recs:
        if not r["recipe"].get("source"):
            continue
        status = await _refresh_one(r, write)
        if status != "nothing new":
            touched += 1
            print(f"  {r['recipe'].get('title', r['id'])!r}: {status}")
    print(f"\n{touched}/{len(recs)} recipes had something to add")
    return 0


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
    cmd = argv[0]
    if cmd == "--show":
        return await cmd_status()
    if cmd == "--backfill":
        if not write:
            print("DRY RUN — nothing will be changed. Add --write when it looks right.")
        return await cmd_backfill(write)
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:])))
