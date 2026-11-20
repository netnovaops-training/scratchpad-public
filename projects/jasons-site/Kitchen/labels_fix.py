"""Re-labelling the whole library against the current vocabulary.

WHY THIS EXISTS
----------------
app/labels.py's whole design is that similar recipes converge on the same
pill -- snap() decides "crockpot" beats "crock pot" beats "slow cooker" so one
idea gets one pill instead of three. That convergence only happens at the
moment a recipe is labelled. A recipe imported before a word was added to
SEED (see labels.py's SEED list), or before some other recipe had already
established a pill, never gets a second chance to land on it -- it is
labelled once, at import, and never again.

This runs labels.suggest() again on every kept recipe, from scratch, against
the vocabulary as it stands today. Nothing here is additive: suggest() asks
the model fresh from the recipe's own title and ingredients (never the old
labels -- see its own docstring for why) and snap()s the result onto whatever
pills already exist, so two recipes that would have started their own separate
pills a year apart now land on the same one.

PROTECTED PILLS SURVIVE. "mama made and tested" and friends are never touched
-- labels.suggest() already carries those over unconditionally; this script
does nothing extra to protect them, because it doesn't need to.

    --show        every recipe's current labels, and the size of the shared
                  vocabulary. Reads only.
    --backfill    re-label every kept recipe.

NOTHING WRITES WITHOUT --write, same rule as mealie_fix.py and nutrition_fix.py.

    docker compose -f docker-compose.yml -f docker-compose.web.yml run --rm `
      --entrypoint python kitchen labels_fix.py --show
"""
import asyncio
import sys

from app import labels, store


def _title(recipe: dict) -> str:
    return (recipe.get("title") or "Untitled")[:60]


async def cmd_show() -> int:
    kept = store.kept_with_ids()
    vocab_size = len({l for l, _ in store.all_labels()})
    print(f"{len(kept)} recipe(s) in the library.")
    print(f"{vocab_size} pill(s) in the shared vocabulary right now.\n")
    for row in kept[:30]:
        have = row["recipe"].get("labels") or []
        print(f"  {_title(row['recipe']):<45} {', '.join(have) or '(none)'}")
    if len(kept) > 30:
        print(f"  ... and {len(kept) - 30} more")
    return 0


async def cmd_backfill(write: bool) -> int:
    kept = store.kept_with_ids()
    print(f"Re-labelling {len(kept)} recipe(s) against the current vocabulary.")
    if not write:
        print("DRY RUN -- nothing will be changed. Add --write when it looks right.\n")

    changed = 0
    for row in kept:
        recipe = row["recipe"]
        before = sorted(recipe.get("labels") or [])
        after = sorted(await labels.suggest(recipe, store.all_labels()))
        if after == before:
            continue
        changed += 1
        print(f"  {_title(recipe)}")
        print(f"    was: {', '.join(before) or '(none)'}")
        print(f"    now: {', '.join(after) or '(none)'}")
        if write:
            recipe["labels"] = after
            store.update_recipe(row["id"], recipe)

    print(f"\n{changed} of {len(kept)} recipe(s) {'relabelled' if write else 'would change'}.")
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
        return await cmd_backfill(write)
    print(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(asyncio.run(run(sys.argv[1:])))
