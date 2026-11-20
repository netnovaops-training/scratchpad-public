"""Backfilling step pictures across the whole library.

WHAT ALREADY EXISTS, AND WHAT THIS ADDS
----------------------------------------
app/art.py draws a picture for a method step that has none -- one recipe at a
time, from the recipe's own guide page, behind a "Draw pictures for the N
steps without one" button (see app/main.py's _draw_missing/_draw_each). That
already does everything right: it checks the graphics card is free, draws one
picture at a time, hands the card back the moment it is done, and pushes each
picture to Mealie as an asset with a caption.

What is missing is a way to run that across every recipe already in the
library without opening each one's page and clicking the button. This is
that -- the same function, called once per recipe that needs it, stopping the
whole run rather than the current recipe the moment the card is wanted for
something else, because a bulk run has no reason to keep polling.

    --show            how many recipes are missing step pictures, and how
                       many steps in total. Reads only, never touches the
                       graphics card.
    --backfill [N]     draw up to N pictures total this run (default 20),
                       across as many recipes as that takes, stopping early
                       if the card is wanted for something else.
    --recipe SLUG      just one recipe, by its Mealie slug.

Only covers recipes that were imported through Kitchen -- a recipe added
straight in Mealie's own UI has no Kitchen draft row for this to save
progress against. --show lists those separately rather than silently
skipping them.

NOTHING WRITES WITHOUT --write, same rule as mealie_fix.py and nutrition_fix.py.

    docker compose -f docker-compose.yml -f docker-compose.web.yml run --rm `
      --entrypoint python kitchen draw_fix.py --show
"""
import asyncio
import sys

from app import art, store
from app.main import _draw_missing


def _title(recipe: dict) -> str:
    return (recipe.get("title") or "Untitled")[:60]


async def cmd_show() -> int:
    kept = store.kept_with_ids()
    needs = []
    total_steps = 0
    for row in kept:
        missing = art.missing_steps(row["recipe"])
        if missing:
            needs.append((row, len(missing)))
            total_steps += len(missing)

    print(f"{len(kept)} recipe(s) in the library have a Kitchen draft.")
    print(f"{len(needs)} of them are missing at least one step picture "
          f"({total_steps} step(s) total).\n")
    for row, n in sorted(needs, key=lambda p: -p[1])[:30]:
        print(f"  {n:>3}  {_title(row['recipe'])}  ({row['mealie_slug']})")
    if len(needs) > 30:
        print(f"  ... and {len(needs) - 30} more")
    return 0


async def _run_one(row: dict, remaining: int) -> tuple:
    """(pictures drawn, stop_reason or ''). stop_reason set only when the
    whole run should stop -- the card is busy, not just this recipe."""
    made, why = await _draw_missing(row["id"], limit=remaining)
    if made == 0 and why:
        return 0, why
    return made, ""


async def cmd_backfill(write: bool, total_limit: int) -> int:
    kept = store.kept_with_ids()
    needs = [row for row in kept if art.missing_steps(row["recipe"])]
    if not needs:
        print("Nothing to draw. Every recipe with a Kitchen draft is caught up.")
        return 0

    print(f"{len(needs)} recipe(s) need at least one step picture.")
    if not write:
        for row in needs[:30]:
            n = len(art.missing_steps(row["recipe"]))
            print(f"  would draw up to {n} for {_title(row['recipe'])} "
                 f"({row['mealie_slug']})")
        if len(needs) > 30:
            print(f"  ... and {len(needs) - 30} more")
        print("\nDRY RUN -- nothing was drawn. Add --write to actually draw them.")
        return 0

    drawn_total = 0
    for row in needs:
        if drawn_total >= total_limit:
            print(f"\nReached this run's limit ({total_limit}). "
                 f"Run again for more.")
            break
        made, stop = await _run_one(row, total_limit - drawn_total)
        if made:
            print(f"  drew {made} for {_title(row['recipe'])} ({row['mealie_slug']})")
            drawn_total += made
        if stop:
            print(f"\nStopping here: {stop}")
            break

    print(f"\n{drawn_total} picture(s) drawn this run.")
    return 0


async def cmd_recipe(write: bool, slug: str) -> int:
    row = next((r for r in store.kept_with_ids() if r["mealie_slug"] == slug), None)
    if not row:
        print(f"No Kitchen draft found for slug {slug!r}.")
        return 1
    missing = art.missing_steps(row["recipe"])
    print(f"{_title(row['recipe'])}: {len(missing)} step(s) missing a picture.")
    if not missing:
        return 0
    if not write:
        print("DRY RUN -- nothing was drawn. Add --write to actually draw them.")
        return 0
    made, why = await _draw_missing(row["id"], limit=len(missing))
    print(f"drew {made}" + (f" -- stopped: {why}" if why else ""))
    return 0


USAGE = __doc__


async def run(argv: list) -> int:
    write = "--write" in argv
    argv = [a for a in argv if a != "--write"]
    if not argv:
        print(USAGE)
        return 2
    cmd = argv[0]
    if cmd == "--show":
        return await cmd_show()
    if cmd == "--backfill":
        limit = int(argv[1]) if len(argv) > 1 else 20
        return await cmd_backfill(write, limit)
    if cmd == "--recipe":
        if len(argv) < 2:
            print("  --recipe needs a Mealie slug")
            return 2
        return await cmd_recipe(write, argv[1])
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:])))
