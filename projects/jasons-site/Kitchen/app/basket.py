"""What the shopping list can work out on its own.

TWO QUESTIONS, BOTH ANSWERABLE WITHOUT A MODEL
----------------------------------------------
    "how far off am I?"        per recipe: 5 of 7 in the basket
    "what else could I make?"  recipes you nearly have the things for

Both are matching an ingredient line against another ingredient line, and both
are asked while somebody is standing in a shop -- so both are done in code.
A model here would be slower, would need the GPU that answers the phone line,
and would occasionally decide that "double cream" and "sour cream" are the same
thing, which is exactly the mistake that sends somebody home with the wrong tub.

WHAT MATCHING ACTUALLY MEANS HERE
---------------------------------
An ingredient line is mostly noise: "3/4 cup double cream, cold" carries one
useful word. So a line is reduced to the words that identify the THING, with
the quantity, the unit and the preparation stripped off, and two lines match
when those word sets overlap.

Deliberately conservative. A false match tells somebody they can make dinner
when they cannot, which is worse than not suggesting it -- they only find out
at the point they have started cooking.

STAPLES ARE NEVER "MISSING"
---------------------------
Salt, pepper, oil, water. Nobody buys them per recipe and nobody counts them
when deciding what to cook, so counting them as missing means every recipe
sits at "you need 3 more things" forever and the whole feature reads as broken.
"""
import re

# Things a kitchen simply has. Not a claim that YOUR kitchen has them -- it is
# a claim that nobody decides what to cook based on whether they do.
STAPLES = {
    "salt", "pepper", "water", "oil", "olive", "vegetable", "sunflower",
    "seasoning", "sugar", "flour", "butter", "milk", "egg", "eggs",
    "garlic", "onion", "onions", "stock", "broth", "vinegar", "ice",
}

# Words that describe an amount, a container, or what to do to the thing --
# never the thing itself.
_NOISE = {
    # amounts and units
    "cup", "cups", "tbsp", "tsp", "tablespoon", "tablespoons", "teaspoon",
    "teaspoons", "gram", "grams", "kg", "kilo", "kilos", "oz", "ounce",
    "ounces", "lb", "lbs", "pound", "pounds", "ml", "litre", "litres",
    "liter", "liters", "pint", "pints", "quart", "gallon", "pinch", "dash",
    "handful", "handfuls", "clove", "cloves", "stick", "sticks", "slice",
    "slices", "can", "cans", "tin", "tins", "packet", "pack", "jar", "bunch",
    "sprig", "sprigs", "knob", "drop", "drops", "piece", "pieces", "large",
    "small", "medium", "big", "little", "extra",
    # preparation
    "chopped", "sliced", "diced", "minced", "grated", "crushed", "peeled",
    "trimmed", "torn", "beaten", "melted", "softened", "cooked", "raw",
    "fresh", "frozen", "dried", "ground", "whole", "halved", "quartered",
    "finely", "roughly", "thinly", "optional", "taste", "needed", "serving",
    "garnish", "divided", "plus", "more", "about", "approximately",
    # glue
    "of", "the", "a", "an", "and", "or", "for", "to", "in", "at", "with",
    "into", "from", "your", "some", "any", "good", "quality", "free", "range",
}

_WORD = re.compile(r"[a-z]{3,}")


def _singular(word: str) -> str:
    """English plurals, the four cases that actually turn up in recipes.

    A bare "strip the s" turns tomatoes into "tomatoe", which then matches
    nothing -- the shopping list says tomatoes and every recipe says tomato.
    """
    w = word
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"           # berries -> berry
    if len(w) > 4 and w.endswith("oes"):
        return w[:-2]                 # tomatoes -> tomato, potatoes -> potato
    if len(w) > 5 and w.endswith(("ses", "xes", "zes", "ches", "shes")):
        return w[:-2]                 # dishes -> dish, boxes -> box
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]                 # peppers -> pepper, eggs -> egg
    return w


def raw_words(line: str) -> set:
    """The identifying words, WITHOUT collapsing plurals.

    Needed because the singular and the plural are sometimes different
    ingredients, and the staples list is exactly where that bites: "pepper" is
    a spice every kitchen has, "peppers" is a vegetable you go out and buy.
    Collapsing them made "2 peppers" a staple, which quietly removed it from
    every recipe's ingredient count -- so a fajita recipe read as needing two
    things instead of three, and the "you could make this" list was wrong in
    the direction that matters.
    """
    return {w for w in _WORD.findall((line or "").lower()) if w not in _NOISE}


def key_words(line: str) -> set:
    """The words that identify the THING, for MATCHING two lines.

    "3/4 cup double cream, cold"  ->  {"double", "cream", "cold"}
    "2 cloves of garlic, minced"  ->  {"garlic"}

    Plurals collapsed here, because a list says "tomatoes" where a recipe says
    "tomato" and those are the same shopping trip. Use raw_words() instead
    wherever the singular and plural mean different things.
    """
    return {_singular(w) for w in raw_words(line)}


def is_staple(line: str) -> bool:
    """Something nobody buys per recipe.

    Compared on RAW words, not collapsed ones -- see raw_words(). "pepper" is
    a staple; "peppers" is dinner.
    """
    words = raw_words(line)
    return bool(words) and words <= STAPLES


def same_thing(a: str, b: str) -> bool:
    """Do two ingredient lines name the same ingredient?

    THE BAR IS EVERY SIGNIFICANT WORD OF THE SHORTER LINE, not "any word in
    common". Overlap of one word makes "double cream" match "cream of tartar",
    "soured cream" and "cream crackers" -- and a shopping list that ticks off
    the wrong thing is worse than one that ticks off nothing.
    """
    wa, wb = key_words(a), key_words(b)
    if not wa or not wb:
        return False
    shorter, longer = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
    return shorter <= longer


# ---------------------------------------------------------------------------
#  How far off is each recipe?
# ---------------------------------------------------------------------------
def by_recipe(items: list) -> list:
    """Group the list by which recipe each line came from.

    Returns [{title, draft_id, got, needed, items[]}], the ones with the most
    still to find first -- because that is the one you are shopping for. A
    recipe you have everything for needs no attention.
    """
    groups = {}
    for item in items:
        key = item.get("draft_id") or ""
        title = item.get("source") or ""
        # Lines added by hand have no recipe. They are their own group and are
        # named as such rather than being hidden or lumped into a real recipe.
        g = groups.setdefault(key, {
            "title": title or "Added by hand",
            "draft_id": key, "items": [], "got": 0, "needed": 0,
        })
        g["items"].append(item)
        if item.get("done"):
            g["got"] += 1
        else:
            g["needed"] += 1

    out = list(groups.values())
    out.sort(key=lambda g: (g["draft_id"] == "", -g["needed"], g["title"]))
    return out


# ---------------------------------------------------------------------------
#  What else could you make?
# ---------------------------------------------------------------------------
def could_make(basket: list, recipes: list, limit: int = 6) -> list:
    """Recipes you nearly have the ingredients for.

    `basket` is the lines you have TICKED -- what is actually in the trolley,
    not what you still mean to buy. `recipes` is [{id, title, ingredients}].

    For each: how many of its ingredients are in the basket, how many are
    missing, and what the missing ones are. Staples are ignored on both sides.

    THIS IS FOR CHANGING YOUR MIND IN THE SHOP. You went in for a lasagne, you
    are standing in front of the chicken, and the question is what else the
    trolley is already most of the way towards. So a recipe with nothing
    missing is not the interesting answer -- one or two things away is.
    """
    have = [b for b in basket if not is_staple(b)]
    if not have:
        return []

    scored = []
    for r in recipes:
        lines = [ln for ln in (r.get("ingredients") or []) if not is_staple(ln)]
        if len(lines) < 2:
            # A "recipe" with one real ingredient is not a suggestion, it is a
            # bad import. Offering it makes the whole list look untrustworthy.
            continue
        missing = [ln for ln in lines if not any(same_thing(ln, b) for b in have)]
        got = len(lines) - len(missing)
        if got == 0:
            continue
        scored.append({
            "id": r.get("id", ""),
            "title": r.get("title", ""),
            "got": got,
            "total": len(lines),
            "missing": missing[:6],
            "missing_count": len(missing),
            # The fraction, not the count. Three of four beats three of
            # twenty, and the count alone would put every long recipe on top.
            "share": got / len(lines),
        })

    # Most nearly-complete first, and among equals the one needing fewest extra
    # things -- that is the one you can actually decide to make while standing
    # there.
    scored.sort(key=lambda s: (-s["share"], s["missing_count"]))
    return scored[:limit]
