"""Course, and where a dish comes from.

WHY THESE ARE NOT JUST MORE LABELS
----------------------------------
labels.py handles the open-ended stuff -- "quick", "crockpot", "one pot" --
where anything the model invents is fine and snap() gathers the near-misses.
These two are different in kind:

    COURSE      a small, closed set. There are about a dozen answers and no
                thirteenth is coming. "main" and "mains" and "main course"
                must be one thing, always.

    CUISINE     open, but not open like a label is. "Where is this from" has
                an answer somebody's family owns, and getting it wrong is not
                the same sort of wrong as a mislabelled cooking time.

Both want to be their OWN field rather than two more pills in a row of
fifteen, because both are things you filter by deliberately -- "show me the
sides", "show me the Southern ones" -- rather than stumble across.

THE MODEL GUESSES, A PERSON DECIDES
-----------------------------------
Both are filled in on import by the model and both are editable afterwards on
the recipe itself. That order matters for cuisine especially. A model reading
an ingredient list will say "American" about a dish a family has made for four
generations and would call something else entirely, and it should be one tap
to correct it and have it stay corrected. See `locked` below: once a person
has set these by hand, no later re-labelling overwrites them.
"""
import os
import re

from . import core, llm

# ---------------------------------------------------------------------------
#  Course — closed, ordered the way a meal actually runs
# ---------------------------------------------------------------------------
COURSES = [
    "breakfast", "lunch", "dinner", "appetizer", "side", "bread", "soup",
    "salad", "dessert", "snack", "drink", "sauce", "preserve",
]

# The many ways people say the same course. Mapped rather than guessed,
# because "entree" means the main course in America and the starter in France
# and no amount of stemming resolves that.
_COURSE_ALIASES = {
    "main": "dinner", "mains": "dinner", "main course": "dinner",
    "entree": "dinner", "entrée": "dinner", "supper": "dinner",
    "starter": "appetizer", "starters": "appetizer", "hors d oeuvre": "appetizer",
    "appetiser": "appetizer", "小吃": "snack",
    "brunch": "breakfast", "pudding": "dessert", "sweet": "dessert",
    "sides": "side", "side dish": "side", "vegetable": "side",
    "beverage": "drink", "drinks": "drink", "cocktail": "drink",
    "stew": "dinner", "casserole": "dinner", "bake": "dinner",
    "baking": "dessert", "cake": "dessert", "pie": "dessert",
    "condiment": "sauce", "dressing": "sauce", "gravy": "sauce",
    "jam": "preserve", "jelly": "preserve", "pickle": "preserve",
    "rolls": "bread", "biscuit": "bread", "cornbread": "bread",
}

# ---------------------------------------------------------------------------
#  Where it comes from
# ---------------------------------------------------------------------------
# A starting list, not a closed one -- somebody can type anything, and a
# tradition this list does not name is the list's failing rather than theirs.
#
# The African American cooking traditions are listed SEPARATELY rather than
# folded into "American", because they are not the same thing and a family
# that cooks them does not call them that. Soul food, Southern, Creole,
# Cajun, Lowcountry and Gullah Geechee are distinct traditions with distinct
# dishes, and collapsing them into one word loses exactly the information this
# field exists to keep.
CUISINES = [
    "soul food", "southern", "creole", "cajun", "lowcountry",
    "gullah geechee", "caribbean", "jamaican", "haitian",
    "west african", "nigerian", "ghanaian", "ethiopian", "moroccan",
    "american", "tex-mex", "mexican", "cuban", "puerto rican", "brazilian",
    "italian", "french", "spanish", "greek", "german", "british", "irish",
    "polish", "jewish",
    "chinese", "japanese", "korean", "thai", "vietnamese", "filipino",
    "indian", "pakistani", "middle eastern", "lebanese", "turkish", "persian",
]

# ---------------------------------------------------------------------------
#  What each cook actually cooks
# ---------------------------------------------------------------------------
# THE LIST ABOVE IS FORTY ENTRIES AND THAT IS THE PROBLEM IT SOLVES, NOT THE
# ONE IT CREATES. On the page where somebody writes out a recipe from memory,
# a scrolling list of forty traditions is forty chances to scroll past the one
# you meant -- and the one you meant is nearly always the same handful, because
# people cook what they cook.
#
# So `/mine` shows the cook their OWN traditions as buttons, with the full list
# still one tap away under "somewhere else". Nothing is removed; the common
# case just stops being a scroll.
#
# The default below is the African American tradition and the roots it grew
# from -- soul food first, then the Southern and Gulf Coast cooking beside it,
# then the Caribbean and West African cooking underneath it. That ordering is
# not decoration: it is the order somebody cooking this food would reach for.
#
# EDITABLE WITHOUT A REBUILD. KITCHEN_HOME_CUISINES in .env, one cook per
# comma, traditions separated by | :
#
#   KITCHEN_HOME_CUISINES=mama=soul food|southern|creole,casey=soul food|thai
#
# A cook not named there gets DEFAULT_HOME. Nobody is locked to it -- the full
# list is on the same page and typing your own has always worked.
DEFAULT_HOME = [
    "soul food", "southern", "creole", "cajun", "lowcountry",
    "gullah geechee", "caribbean", "west african",
]

HOME_BY_COOK: dict = {}
for _pair in os.getenv("KITCHEN_HOME_CUISINES", "").split(","):
    if "=" in _pair:
        _cook, _list = _pair.split("=", 1)
        _wanted = [c.strip().lower() for c in _list.split("|") if c.strip()]
        if _cook.strip() and _wanted:
            HOME_BY_COOK[_cook.strip().lower()] = _wanted


def home_for(cook: str) -> list:
    """The traditions this cook reaches for, most likely first.

    `cook` is the short name from labels.COOKS ("mama", "casey"), not an email --
    so the setting is written the way a person would say it and does not have
    to be updated when somebody changes address.
    """
    return HOME_BY_COOK.get((cook or "").strip().lower(), DEFAULT_HOME)


def rest_of(home: list) -> list:
    """Everything else, for the "somewhere else" list. Nothing is hidden."""
    have = {c.lower() for c in home}
    return [c for c in CUISINES if c.lower() not in have]


_CLEAN = re.compile(r"[^a-z0-9 &'-]")


def _norm(value: str) -> str:
    value = _CLEAN.sub("", (value or "").strip().lower())
    return re.sub(r"\s+", " ", value).strip()


def clean_course(value: str) -> str:
    """One of COURSES, or "". Never anything else."""
    v = _norm(value)
    if not v:
        return ""
    if v in COURSES:
        return v
    if v in _COURSE_ALIASES:
        return _COURSE_ALIASES[v]
    # Singular/plural, and "dinners" -> "dinner".
    if v.endswith("s") and v[:-1] in COURSES:
        return v[:-1]
    # Refuse rather than guess. A course this does not recognise is better
    # left empty than filed under whichever entry looked closest -- an empty
    # field reads as "nobody said", a wrong one reads as fact.
    return ""


def clean_cuisine(value: str) -> str:
    """Tidied, and matched to the list where it plainly belongs.

    UNLIKE clean_course THIS DOES NOT REFUSE. The list is a starting point,
    not an authority on where food comes from -- if somebody types a tradition
    it does not name, the answer is to keep what they typed, not to throw it
    away because it is not on a list I wrote.
    """
    v = _norm(value)
    if not v or len(v) > 24:
        return ""
    if v in CUISINES:
        return v
    flat = v.replace("-", " ").replace("  ", " ")
    for known in CUISINES:
        if flat == known.replace("-", " ") or _norm(known) == flat:
            return known
    # Common ways of saying the same thing.
    for known in CUISINES:
        if flat.startswith(known) or known.startswith(flat):
            if abs(len(flat) - len(known)) <= 3:
                return known
    return v


SYSTEM = """You read a recipe and answer two questions about it.

course: which of these it is, exactly one word from this list:
breakfast, lunch, dinner, appetizer, side, bread, soup, salad, dessert,
snack, drink, sauce, preserve.
A main course is "dinner". A starter is "appetizer".

cuisine: the cooking tradition it belongs to, in one or two words. Be
specific where the dish is specific -- "soul food", "creole", "tex-mex",
"sichuan" are better answers than "american" or "chinese" when they fit. If a
dish genuinely belongs to no particular tradition, answer "".

Answer only about the recipe in front of you. Do not guess from the language
the recipe is written in. No preamble. JSON only."""


async def read(recipe: dict) -> dict:
    """{'course': ..., 'cuisine': ...}, either possibly "".

    Never raises. These are two extra facts on a page that is already useful
    without them; failing an import over a missing course would be absurd.
    """
    ingredients = "\n".join(recipe.get("ingredients", [])[:20])
    ask = f"{recipe.get('title', '')}\n\n{ingredients}"
    try:
        data = await llm.chat_json(
            SYSTEM + '\n\nReturn JSON: {"course": "", "cuisine": ""}',
            ask, model=core.LLM_LABEL, temperature=0.1, max_tokens=200,
        )
    except llm.LLMError:
        return {"course": "", "cuisine": ""}
    return {
        "course": clean_course(str(data.get("course") or "")),
        "cuisine": clean_cuisine(str(data.get("cuisine") or "")),
    }


def locked(recipe: dict) -> bool:
    """Has a person set these by hand?

    Once somebody has, a later re-label must not quietly overwrite them. This
    is the difference between a field the model maintains and a field the
    model merely suggested once -- and for "where is this from", on a recipe a
    family has been making for generations, the person is right and the model
    is not getting another go.
    """
    return bool(recipe.get("taxonomy_by_hand"))
