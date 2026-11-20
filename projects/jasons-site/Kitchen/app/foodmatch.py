"""Close-matching one food/tool name against a small set of others,
shared by dedupe.py (matching EXISTING items against each other, for a
later cleanup pass) and tandoor.py (matching a NEW ingredient name against
the library WHILE a recipe is being imported, so a duplicate never gets
created in the first place). Split out into its own module specifically
so tandoor.py can call the live-matching half without importing dedupe.py
-- dedupe.py already imports tandoor.py (for all_foods/merge_food/etc.),
and the reverse import would be circular.

Jason's own request (2026-08-24): "make changes on the fly when I edit
Ingredients" -- clarified as live fuzzy-match while typing/importing, so
something like a fifth "coriander" entry gets caught and reused instead of
sitting next to four near-duplicates waiting for a dedupe run to notice.
"""
import re

from . import core, llm, vram

# Words that describe how something was cut, measured, or phrased -- NEVER
# a word that changes what the ingredient/tool actually IS. See dedupe.py's
# own comment on why "raw"/"cooked"/"dried"/"ground" are deliberately left
# OUT: those change a food's real nutrition profile.
#
# CAUGHT LIVE (2026-08-25), Jason's own example, browsing /admin/items:
# "(60ml) lemon juice from 1 lemon" and "A drizzle of lemon juice" are
# the SAME food, "lemon juice", with a portion size baked into the
# FOOD'S OWN NAME rather than left in the recipe's own ingredient line
# where it belongs. "drizzle"/"sprinkle"/etc. added below -- the exact
# vague-amount words scale.py's own _VAGUE already knows are not real
# quantities ("a pinch of salt" doesn't scale either), reused here for
# the same reason: not part of what the food IS.
# Numbers written as words, and packaging. "One 14.5 ounce can crushed
# tomatoes" normalised to "one ounce can tomatoe" -- three separate faults in
# one line, and the reason a single tinned tomato became half a dozen foods.
# The digits were already stripped; the word "One" was not, and neither was
# the container it came in. A can of tomatoes is tomatoes.
_SPELLED_NUMBERS = {
    # "an" as well as "a" -- _TIER1_FILLER has had "a" since the start and
    # never had "an", so "1 onion" and "an onion" became two separate foods.
    # Three recipes' worth, sitting next to a food used twenty times.
    "an",
    "one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
    "ten", "eleven", "twelve", "dozen", "half", "quarter", "third",
}
_PACKAGING = {
    "can", "cans", "canned", "tin", "tins", "tinned", "jar", "jars",
    "package", "packages", "packet", "packets", "pack", "packs", "box",
    "boxes", "bag", "bags", "bottle", "bottles", "carton", "cartons",
    "container", "containers", "stalk", "stalks", "sprig", "sprigs",
}
# Units survive as WORDS once the digits are stripped, so "14.5 ounce can" left
# "ounce" behind and "ounce tomato" matched nothing that "canned tomato" did.
# Food only: a tool is never measured in cups, but "measuring cup" and
# "tablespoon" are real tools and must keep their names.
_UNIT_WORDS = {
    "ounce", "ounces", "oz", "pound", "pounds", "lb", "lbs", "gram", "grams",
    "g", "kg", "kilogram", "kilograms", "ml", "millilitre", "millilitres",
    "milliliter", "milliliters", "litre", "litres", "liter", "liters", "l",
    "cup", "cups", "tablespoon", "tablespoons", "tbsp", "teaspoon",
    "teaspoons", "tsp", "quart", "quarts", "pint", "pints", "gallon",
    "gallons", "slice", "slices", "clove", "cloves", "inch", "inches",
}

_TIER1_FILLER = {
    "chopped", "finely", "coarsely", "roughly", "thinly", "thickly",
    "sliced", "diced", "minced", "crushed", "peeled", "grated", "shredded",
    "cubed", "julienned", "halved", "quartered", "trimmed", "washed",
    "rinsed", "drained", "large", "small", "medium", "chunks", "pieces",
    "cut", "into", "of", "a", "to", "the", "plus",
    "whole", "half", "juiced", "packed", "loosely", "sprigs", "sprig",
    "garnish", "drizzle", "drizzles", "sprinkle", "sprinkles", "pinch",
    "pinches", "dash", "dashes", "splash", "splashes", "handful",
    "handfuls", "knob", "knobs", "glug", "glugs", "squeeze", "squeezes",
    "touch", "firmly",
}
# NOT "extra" -- CAUGHT LIVE running the by-key collision sweep
# (2026-08-25): "extra virgin olive oil" -> "virgin olive oil". Same
# exact mistake as the "and"/"or" one below, just a different word:
# "2 tbsp plus extra for greasing" (a real, safe strip) and "extra
# virgin olive oil" (a real GRADE of olive oil, a different product
# from plain "virgin olive oil") use the same word to mean two
# different things, and nothing mechanical here can tell them apart --
# so it stays, same call already made for "and"/"or".
#
# Pure connective glue -- stripped from the food name same as everything
# else in _TIER1_FILLER, but never worth surfacing on their own as a
# note (clean_name_and_note() below): "of"/"a"/"cut" carry no real
# information without the words around them that already got removed.
_CONNECTIVE_ONLY = {"of", "a", "to", "the", "into", "cut", "plus"}

# CAUGHT LIVE (2026-08-25): a "large pot" and a "small pot" are
# DIFFERENT tools someone owns or doesn't, the same way "brown sugar"
# and "sugar" are different foods -- but "large"/"small"/"medium" were
# in _TIER1_FILLER for the FOOD case ("1 large onion" -> "onion", size
# genuinely doesn't change what an onion is) and every function below
# used the same one global set for both kinds, so a tool's size word
# was always at risk of being silently stripped too. Every caller now
# passes `kind` and gets the size words back out for "tool".
_TOOL_KEEP = {"large", "small", "medium"}


def _filler_for(kind: str) -> set:
    # Spelled-out numbers and packaging are dropped for FOOD only. A tool
    # genuinely can be a "box grater" or a "can opener", and stripping
    # those words there would turn two real tools into one wrong one.
    base = _TIER1_FILLER | _SPELLED_NUMBERS
    if kind == "food":
        return base | _PACKAGING | _UNIT_WORDS
    return base - _TOOL_KEEP
# NOT "and"/"or" -- CAUGHT LIVE running clean_name() (below) against the
# real library: "chicken or vegetable broth" -> "chicken vegetable
# broth", "Monterey Jack cheese or mild white cheddar cheese" -> one
# nonsense mashed-together name. Stripping a CONNECTOR between two cut/
# prep words ("cut and quartered") is safe; stripping one between two
# real NOUN PHRASES silently erases "either of these work" and produces
# a name that reads as a single new product nobody typed. No mechanical
# rule here can tell those two cases apart, so both stay -- a name that
# still has "chicken or vegetable broth" in it is honest; "chicken
# vegetable broth" is not.

_LEADING_METRIC_RE = re.compile(r"^\(\s*[\d./\-]+\s*[a-zA-Z]*\.?\s*\)\s*")
# CAUGHT LIVE (2026-08-25), four more real names in a row: "(12 ounces
# each) Hawaiian sweet rolls", "(a splash) white or rice wine vinegar",
# "(or more) Old Bay seasoning", "(½ pound) thin asparagus" -- none
# of these fit _LEADING_METRIC_RE's own narrow shape (a vulgar fraction
# character isn't \d, "each"/"a splash"/"or more" aren't a unit word at
# all) but every one is still the exact same thing: a quantity/amount
# annotation sitting in front of the real food name, not a real product
# descriptor. Surveyed the WHOLE library before adding this (2026-08-25)
# -- every leading parenthetical found was a quantity artifact, zero
# were genuine variants like "(extra virgin) olive oil" would be if it
# existed -- so a general catch-all is safe here specifically: LEADING
# position, not trailing, is what makes it safe (a real descriptive
# aside reads naturally trailing the food name, "(ripe and juicy!)
# peaches"; a real product variant is normally not parenthesized at all,
# "extra virgin olive oil" not "(extra virgin) olive oil"). Runs AFTER
# _LEADING_METRIC_RE in the strip loop below, so the narrower pattern
# still gets first claim; this only catches what it didn't.
_LEADING_ANY_PAREN_RE = re.compile(r"^\([^)]*\)\s*")

# A leading amount NOT in parens -- "1 cup prepared lemon curd" -- and a
# trailing yield clarification -- "lemon juice FROM 1 LEMON". Both are
# about how MUCH / where it came from, never what it is, same rule as
# everything else in this module. The "from" pattern requires a leading
# NUMBER specifically (not just any "from") -- "orange juice from
# concentrate" is a real, different product, and "concentrate" never
# follows a bare digit the way "1 lemon"/"2 limes" do.
# CAUGHT LIVE (2026-08-25): "2/3 cup sour cream" and "8 - 10 oz pasta"
# both sailed through unstripped -- the number-only version above
# ([\d.]+) has no way to match a bare fraction or a range, both real,
# common ways a recipe actually writes an amount ("2/3 cup", "8-10 oz").
# CAUGHT LIVE AGAIN, same day: "1 5/8 oz (500g) beef mince" -- a MIXED
# number (whole + fraction together), exactly what scale.py's own
# _oz_text() renders for anything at or past 16oz. That alternative
# has to come FIRST in the group -- "1 5/8" would otherwise match only
# "1" via the bare-number alternative, leaving "5/8 oz (500g) beef
# mince" as the unstripped remainder.
_AMOUNT = r"(?:\d+\s+\d/\d|\d+\s*-\s*\d+|\d+/\d+|\d+(?:\.\d+)?)"
_LEADING_QTY_RE = re.compile(
    rf"^{_AMOUNT}\s*(?:g|kg|ml|l|oz|lb|lbs|cups?|tbsp|tablespoons?|tsp|teaspoons?|"
    r"cloves?|slices?|stalks?)\b\.?\s*", re.I)
# CAUGHT LIVE (2026-08-25), a real food name sitting in the library:
# "one teaspoon light brown sugar" -- the same leading-quantity leak as
# _LEADING_QTY_RE above, just spelled out as a word instead of a digit.
# Word numbers only go up to "ten" -- a recipe amount bigger than that is
# vanishingly rare to see spelled out, and capping the list keeps this
# from ever eating a real food name that happens to start with a number
# word for some other reason.
_LEADING_WORDQTY_RE = re.compile(
    r"^(?:one|two|three|four|five|six|seven|eight|nine|ten|a|an|half)\s+"
    r"(?:g|kg|ml|l|oz|lb|lbs?|cups?|tbsp|tablespoons?|tsp|teaspoons?|"
    r"cloves?|slices?|stalks?|pinch(?:es)?)\b\.?\s*", re.I)
_TRAILING_FROM_RE = re.compile(r"\s+from\s+[\d.]+\s+[a-zA-Z]+\.?\s*$", re.I)
# CAUGHT LIVE (2026-08-25): "salt and pepper to taste" -> "salt and
# pepper taste" -- "to" is real, safe filler almost everywhere else
# ("cut into cubes"), but stripping it out of THIS specific trailing
# phrase orphans "taste" instead of removing the whole quantity
# qualifier. Stripped as one unit, same reasoning as _TRAILING_FROM_RE.
_TRAILING_TO_TASTE_RE = re.compile(r"\s*,?\s*to\s+taste\.?\s*$", re.I)
_LEADING_VAGUE_RE = re.compile(
    r"^(?:an?\s+)?(?:pinch(?:es)?|dash(?:es)?|drizzle[s]?|splash(?:es)?|"
    r"handful[s]?|sprinkle[s]?|knob[s]?|glug[s]?|squeeze[s]?|touch)\s+of\s+", re.I)
# CAUGHT LIVE (2026-08-25): "(300 g) / 10 oz mushrooms, sliced" -- a
# source site's own dual metric/imperial listing ("300g / 10oz"), which
# Tandoor's parser folded into the food name whole. _LEADING_METRIC_RE
# strips "(300 g) " fine, but what's left, "/ 10 oz mushrooms...", starts
# with the separating slash, not a digit -- _LEADING_QTY_RE never got a
# chance to see the SECOND quantity because it never got past the "/".
_LEADING_SEP_RE = re.compile(r"^[/,;]\s*")


def _strip_leading_and_trailing(text: str) -> str:
    text = _TRAILING_FROM_RE.sub("", text)
    text = _TRAILING_TO_TASTE_RE.sub("", text)
    # Looped, not one pass each -- a dual-unit prefix ("(300 g) / 10 oz")
    # needs the metric strip, then the separator strip, then the qty
    # strip to ALL fire before the real food name is reached; one pass
    # each stops after the first. Capped iterations, not `while True`,
    # since nothing here is proven to always shrink the string on some
    # future input -- a fixed bound simply cannot loop forever.
    for _ in range(4):
        before = text
        text = _LEADING_METRIC_RE.sub("", text)
        text = _LEADING_QTY_RE.sub("", text)
        text = _LEADING_WORDQTY_RE.sub("", text)
        text = _LEADING_VAGUE_RE.sub("", text)
        text = _LEADING_SEP_RE.sub("", text)
        text = _LEADING_ANY_PAREN_RE.sub("", text)
        if text == before:
            break
    return text


def clean_name(name: str, kind: str = "food") -> str:
    """The same identity-vs-description split as normalize() below, but
    for the item's own DISPLAY name -- casing and plurals kept as
    written (this is what an item gets RENAMED to, not a matching key).
    "(60ml) lemon juice from 1 lemon" -> "lemon juice". "coarse sea
    salt" -> unchanged (no leading amount, no filler word describes a
    real variety away). "Finely chopped parsley" -> "parsley" (both
    words already in _TIER1_FILLER). Empty string means "nothing to
    strip safely" -- callers must leave the name alone, never rename to
    blank. `kind="tool"` keeps size words in place -- see _TOOL_KEEP."""
    filler = _filler_for(kind)
    text = _strip_leading_and_trailing((name or "").strip())
    kept = [w for w in text.split() if w.lower().strip(",.") not in filler]
    return " ".join(kept).strip(" ,.-")


def clean_name_and_note(name: str, kind: str = "food") -> tuple:
    """Same split as clean_name(), but also hands back whatever
    DESCRIPTIVE filler got stripped out along the way, instead of just
    discarding it -- Jason's own words (2026-08-25), about this same kind
    of word: "it can have sub wording like Finely chopped parsley, but
    that should still be parsley with a sub context in the recipe as
    finely chopped not as a individual food item" -- and again, a
    session later, about "packed" in "(71 g) packed light brown sugar":
    "with a note in the recipe packed under the food item". Both are the
    same request: the word describes how THIS ingredient line uses the
    food, not what the food IS, so it moves to the ingredient's own
    note, never vanishes and never taints the food's shared identity.

    Returns (clean_name, note) -- note is "" when nothing worth keeping
    was stripped. The leading quantity/metric text ("71 g") is NOT
    included -- that's the recipe's own amount restated a second time,
    already captured by the ingredient's real quantity/unit, so keeping
    it too would just be clutter, exactly Jason's own "the 71g is
    irrelevant to the food item" call."""
    filler = _filler_for(kind)
    text = _strip_leading_and_trailing((name or "").strip())
    kept, dropped = [], []
    for w in text.split():
        bare = w.lower().strip(",.")
        if bare in filler and bare not in _CONNECTIVE_ONLY:
            dropped.append(bare)
        elif bare not in filler:
            kept.append(w)
    clean = " ".join(kept).strip(" ,.-")
    note = " ".join(dict.fromkeys(dropped))
    return clean, note


# Plurals the -s rule gets wrong. "tomatoes" fell through every branch below
# and lost only its final s, giving "tomatoe" -- so "crushed tomatoes",
# "canned tomatoes" and "cherry tomatoes" each normalised to a word that
# matches nothing, and every phrasing became its own new food. Caught while
# building the canonical list, in the single most common ingredient there is.
_IRREGULAR = {
    "tomatoes": "tomato", "potatoes": "potato", "avocados": "avocado",
    "mangoes": "mango", "loaves": "loaf", "leaves": "leaf", "halves": "half",
    "knives": "knife", "wolves": "wolf", "shelves": "shelf",
    "cloves": "clove", "olives": "olive", "chives": "chive",
    "children": "child", "teeth": "tooth", "feet": "foot", "geese": "goose",
    "molasses": "molasses", "asparagus": "asparagus", "hummus": "hummus",
    "couscous": "couscous", "greens": "greens", "grits": "grits",
    "oats": "oats", "chives": "chive", "berries": "berry",
}


def _singular(word: str) -> str:
    if word in _IRREGULAR:
        return _IRREGULAR[word]
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    # "-oes" is a real plural ending in its own right (tomatoes, potatoes) and
    # the "shxz" test below never sees it, because the letter before "es" is
    # an o. Without this the s comes off alone and leaves a word that is not
    # a word.
    if len(word) > 4 and word.endswith("oes"):
        return word[:-2]
    if len(word) > 4 and word.endswith("es") and word[-3] in "shxz":
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _deaccent(text: str) -> str:
    """purée -> puree, jalapeño -> jalapeno.

    The word pattern below is [a-z'], so an accented letter is not a word
    character and SPLITS the word around it: "purée" became "pur" + "e", and
    "tomato purée" was filed as "tomato pur e". Stripping the marks first
    fixes that and folds "puree" and "purée" together, which is the same food
    typed two ways and was previously two.
    """
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch))


def normalize(name: str, kind: str = "food") -> str:
    filler = _filler_for(kind)
    name = _strip_leading_and_trailing((name or "").strip())
    words = re.findall(r"[a-z']+", _deaccent(name.lower()))
    kept = [_singular(w) for w in words if w not in filler]
    return " ".join(kept)


def core_word(name: str, kind: str = "food") -> str:
    """The single word left standing after normalizing AND dropping
    anything under 4 letters. Used only to BUCKET candidates, never to
    match anything on its own."""
    words = [w for w in normalize(name, kind).split() if len(w) >= 4]
    return words[-1] if words else ""


MATCH_SYSTEM = """You check whether a NEW kitchen ingredient or tool name
is the same real-world thing as one of a short list of EXISTING candidates
already in the database, described differently -- never a genuinely
different thing, even if related.

Calibration, exact: "cold water" matches an existing "water". "brown
sugar" does NOT match an existing "sugar" -- different product. Only match
when the difference is pure phrasing/scraping noise (plural, case, a
stray descriptor that doesn't change the product) -- never match across a
difference in color, variety, brand, cooked-vs-raw, fresh-vs-dried,
alcoholic-vs-non-alcoholic, or any qualifier that changes what a shopper
would actually buy. When in doubt, no match -- a genuinely new item is
normal and fine; a wrong match is not.

You are given a list of NEW names, each with its own small list of
EXISTING candidates. For each NEW name, decide which ONE candidate (if
any) is really the same thing.

Return JSON: {"matches": [{"new": "<new name, verbatim>",
"existing": "<the matching candidate, verbatim>" or null}, ...]},
one entry per NEW name, same order given. "existing" must be copied
EXACTLY from that name's own candidate list, or JSON null."""


async def _ask_match(batch: list) -> dict:
    """batch is [(new_name, [candidate names]), ...]. Returns
    {new_name: matched_existing_name} for confirmed matches only --
    everything else is left for the caller to create as a new item.
    Never raises, same convention as every other LLM call in this app:
    a failed match here means "create it new," not a failed import."""
    if not batch:
        return {}
    lines = "\n".join(
        f"{i}. NEW: {name}\n   candidates: {', '.join(cands)}"
        for i, (name, cands) in enumerate(batch, 1)
    )
    user = f"Items:\n{lines}"
    try:
        async with vram.parsing():
            data = await llm.chat_json(MATCH_SYSTEM, user, model=core.LLM_READ,
                                       temperature=0.1, max_tokens=1000)
    except llm.LLMError:
        return {}
    rows = data.get("matches") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return {}
    by_new = {name.strip().lower(): name for name, _ in batch}
    cand_lookup = {name.strip().lower(): {c.strip().lower(): c for c in cands}
                  for name, cands in batch}
    out = {}
    for row in rows:
        new_name = (row.get("new") or "").strip()
        existing = row.get("existing")
        if not isinstance(existing, str) or not existing.strip():
            continue
        key = new_name.lower()
        if key not in cand_lookup:
            continue
        real = cand_lookup[key].get(existing.strip().lower())
        if real:
            out[by_new[key]] = real
    return out


async def match_new_names(names: list, existing: list, kind: str = "food") -> dict:
    """`names` are new food/tool names about to be created; `existing` is
    the full current library, as [{"id":..., "name":...}, ...] the caller
    already fetched (kept out of this module so it stays Tandoor-agnostic).
    Returns {new_name: real_existing_name} for every name that should
    reuse an existing row instead of creating a new one -- a name absent
    from the result should be created as-is. `kind="tool"` keeps size
    words significant (see _TOOL_KEEP) -- a "large pot" never silently
    matches a "small pot" here either.

    SAME TWO TIERS AS dedupe.py's own run(), same reasoning, but Tier 2
    behaves differently here on purpose: dedupe.run() always queues its
    tier-2 proposals for Jason's later approval, because a real, already-
    used item exists on both sides of a proposed merge. Here the "new"
    side doesn't exist yet -- there is nothing to review, only "reuse this
    row" vs. "create a new one" -- so a confident tier-2 match applies
    immediately. Bounded to the same core-word bucket as dedupe.py's own
    tier 2, so this never asks the model to pick from the whole library."""
    seen = set()
    names = [n for n in names if (n or "").strip() and not (n.strip().lower() in seen or seen.add(n.strip().lower()))]
    if not names:
        return {}
    existing = [{"id": it.get("id"), "name": it.get("name") or ""} for it in existing if it.get("name")]

    by_key: dict = {}
    for it in existing:
        by_key.setdefault(normalize(it["name"], kind), it["name"])

    by_core: dict = {}
    for it in existing:
        cw = core_word(it["name"], kind)
        if cw:
            by_core.setdefault(cw, []).append(it["name"])

    result = {}
    tier2_batch = []
    for name in names:
        key = normalize(name, kind)
        if len(key) >= 3 and key in by_key and by_key[key].strip().lower() != name.strip().lower():
            result[name] = by_key[key]
            continue
        cw = core_word(name, kind)
        candidates = [c for c in by_core.get(cw, []) if normalize(c, kind) != key] if cw else []
        if candidates:
            tier2_batch.append((name, candidates[:8]))

    if tier2_batch:
        result.update(await _ask_match(tier2_batch))
    return result
