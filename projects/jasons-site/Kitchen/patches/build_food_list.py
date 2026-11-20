#!/usr/bin/env python3
"""Build the canonical food list and link every recipe ingredient to it.

Jason, 2026-09-07: "can we normalize the food list and update the recipes to
use the new list."

NOTHING WRITTEN INTO AN INGREDIENT LINE. Ingredients are stored as plain
strings -- "1 tbsp olive oil" -- with the quantity, the unit and the food all
in one piece of text. Rewriting those to a canonical name would throw away the
amount, which is the one thing a recipe cannot lose. So the canonical name
goes in a PARALLEL array, `ingredient_foods`, in the same order:

    ingredients      = ["1 tbsp olive oil", "2 slices smoked bacon"]
    ingredient_foods = ["olive oil",        "smoked bacon"]

Every existing reader keeps seeing exactly the string it saw before. Anything
that wants to match, count or shop by food reads the new array. Additive, so
the migration cannot corrupt a recipe; reversible by deleting one key.

WHAT IS MERGED AUTOMATICALLY, AND WHAT IS NOT.

Automatic: names that normalise to the same string. "One 14.5 ounce can
crushed tomatoes", "canned tomatoes" and "2 cans of tomatoes" are the same
food by every reading, and the normaliser now agrees.

NOT automatic: anything needing a judgement about what a food IS. "kosher
salt" and "salt" are probably one item; "olive oil" and "extra virgin olive
oil" are probably two; "ground beef" and "beef" are definitely two. No
threshold separates those, so they are written to a review file instead of
being decided here. A migration that quietly merges two real products is
worse than one that leaves a duplicate visible.

COMPOUND LINES ARE LEFT UNLINKED. "kosher salt and freshly ground black
pepper" is two foods in one line. Guessing which one it "is" would file the
recipe under the wrong item; the line keeps its text and gets no canonical
name until somebody splits it.
"""
import collections
import json
import re
import sqlite3
import sys

sys.path.insert(0, "/app")
from app import foodmatch as fm  # noqa: E402

DB = "/data/kitchen.db"
MIN_USES = 2          # a food seen once is not yet a pantry item
DRY = "--write" not in sys.argv


def ingredient_lines(recipe: dict) -> list:
    out = []
    for ing in (recipe.get("ingredients") or []):
        if isinstance(ing, dict):
            out.append(ing.get("text") or ing.get("original") or "")
        else:
            out.append(str(ing))
    return out


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "select id, title, recipe from drafts where recipe is not null").fetchall()

    # --- pass one: what foods exist, and how often ---------------------------
    counts = collections.Counter()
    for r in rows:
        try:
            j = json.loads(r["recipe"])
        except Exception:  # noqa: BLE001
            continue
        for line in ingredient_lines(j):
            name = fm.normalize(line, "food")
            if not name or len(name) < 2:
                continue
            if re.search(r"\band\b", name):   # compound, see the docstring
                continue
            counts[name] += 1

    pantry = {n for n, k in counts.items() if k >= MIN_USES}
    print(f"  distinct foods seen : {len(counts)}")
    print(f"  pantry (>= {MIN_USES} uses): {len(pantry)}")
    print(f"  seen once only      : {len(counts) - len(pantry)}")

    # --- the judgement calls, for review rather than for deciding here -------
    # Grouped by their last word, which is the noun that usually carries what
    # the food IS ("kosher SALT", "sea SALT"). A group of one is not a
    # question; only groups of two or more get written out.
    by_head = collections.defaultdict(list)
    for n in sorted(pantry):
        by_head[n.split()[-1]].append(n)
    review = {h: v for h, v in by_head.items() if len(v) > 1}
    print(f"  groups wanting a decision: {len(review)}")

    # --- pass two: link each line to its food -------------------------------
    linked = unlinked = compound = touched = 0
    for r in rows:
        try:
            j = json.loads(r["recipe"])
        except Exception:  # noqa: BLE001
            continue
        lines = ingredient_lines(j)
        foods = []
        for line in lines:
            name = fm.normalize(line, "food")
            if not name or len(name) < 2:
                foods.append(""); unlinked += 1
            elif re.search(r"\band\b", name):
                foods.append(""); compound += 1
            elif name in pantry:
                foods.append(name); linked += 1
            else:
                # Seen once. Still recorded -- it IS the food -- but it is not
                # yet pantry, so it does not pull other lines toward it.
                foods.append(name); linked += 1
        if j.get("ingredient_foods") == foods:
            continue
        j["ingredient_foods"] = foods
        touched += 1
        if not DRY:
            c.execute("update drafts set recipe=? where id=?",
                      (json.dumps(j), r["id"]))

    if not DRY:
        c.commit()
        with open("/tmp/food-review.json", "w", encoding="utf-8") as f:
            json.dump({"pantry": sorted(pantry),
                       "counts": dict(counts),
                       "needs_decision": review}, f, indent=1)

    print()
    print(f"  ingredient lines linked : {linked}")
    print(f"  compound, left unlinked : {compound}")
    print(f"  unreadable, left blank  : {unlinked}")
    print(f"  recipes {'that would be' if DRY else ''} updated : {touched}")
    print()
    print("  DRY RUN -- nothing written. Re-run with --write." if DRY
          else "  written. Review file at /tmp/food-review.json")


if __name__ == "__main__":
    main()
