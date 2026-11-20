"""Labels, and the one trick that stops them multiplying.

THE PROBLEM WITH LETTING A MODEL INVENT TAGS
--------------------------------------------
Ask a model to label a recipe and it will do a good job. Ask it a hundred
times, once per recipe, with no memory between calls, and you get:

    weeknight · quick weeknight · fast midweek · easy weeknight

Four pills for one idea, on four different recipes, none of which find each
other. The model is not being careless -- each call is answering correctly in
isolation. The fault is the isolation.

THE FIX IS NOT A BETTER PROMPT. IT IS snap().
The obvious answer is to show the model the vocabulary it already has and ask
it to reuse. That was tried here and it failed badly on a 3B: handed a list
containing "chocolate" and "brownies", it labelled a GUACAMOLE recipe
["serves", "chocolate", "brownies", "sugar"] -- copying the nearest text in
its context rather than labelling at all.

So the model sees ONLY the recipe, which it is genuinely good at, and the
gathering-together happens afterwards in code: snap() pulls "brownies" onto
"brownie" and "chicken thighs" onto "chicken" by stem and word boundary, never
by guesswork. SEED decides in advance which of several equally-good words wins
-- "crockpot" rather than "crock pot" or "slow cooker".

Invention still works. A genuinely new idea that matches nothing becomes a new
pill, which is the point.

WHY THE SHAPE IS ENFORCED IN CODE TOO
-------------------------------------
Prompts are advice, not rules. A model told "at most four" will occasionally
return eight, or a sentence, or the word "serves" lifted off an ingredient
line. tidy() lowercases, trims to two words, drops the useless ones and caps
the count; verify_dietary() throws out any dietary claim the ingredients
contradict. A bad reply degrades to a slightly worse-labelled recipe rather
than to a browse page with a paragraph on it -- or, worse, a chicken dish
wearing a "vegetarian" pill.
"""
import os
import re

from . import core, llm

# Anything that is not a plain word, a digit, a space, & or -.
# Defined up here because made_by() below needs it.
_CLEAN = re.compile(r"[^a-z0-9 &-]")

MAX_PER_RECIPE = 5
MAX_LENGTH = 22

# ---------------------------------------------------------------------------
#  The pills we WANT to exist
# ---------------------------------------------------------------------------
# A starting vocabulary, written down rather than waited for.
#
# These do not appear as pills until a recipe actually earns one -- a browse
# page opening with forty empty categories is worse than one that fills up as
# you cook. What they do is take part in SNAPPING (see snap()), so the first
# slow-cooker recipe that arrives labelled "slow cooker", "crock pot" or
# "crockpot" lands on the same pill instead of starting three.
#
# That is the whole reason to seed a list: not to pre-populate the page, but to
# decide in advance which of several equally-good words wins.
#
# Grouped by the kind of question somebody is actually asking when they tap.
SEED = [
    # how it is cooked
    "slow-cooker", "one pot", "sheet pan", "air fryer", "skillet", "baked",
    "fried", "smothered", "grilled", "no cook", "no bake", "blender",
    "instant pot", "stand mixer", "food processor",
    # how long it takes
    "30 minutes", "quick", "all day", "overnight",
    # when you would make it
    "sunday dinner", "weeknight", "holiday", "potluck", "cookout",
    "breakfast", "lunch", "dinner", "dessert", "side",
    # what it is
    "soul food", "southern", "comfort", "greens", "casserole", "cornbread",
    "beans", "rice", "pasta", "soup", "stew", "salad", "bread", "cobbler",
    "cake", "pie", "chicken", "beef", "pork", "turkey", "fish", "seafood",
    "vegetarian", "vegan",
    # practicalities
    "budget", "freezer", "leftovers", "big batch", "kids",
]

# ---------------------------------------------------------------------------
#  Pills the model may never touch: who actually cooked it
# ---------------------------------------------------------------------------
# "mama made and tested" is not a description of the food. It is a statement
# about who stood at the stove, and only a person can make it. The model never
# adds one of these (it cannot know) and never removes one (it does not get a
# vote). They go on via the "write out your own recipe" form and the button on
# a guide, and come off the same way.
#
# These are also the most valuable pills on the page. Anyone can paste a link.
# This is the one that says somebody has actually made this, more than once,
# and it came out right.
#
# ONE PER COOK, because "who made it" is a different answer for each of them
# and they should each get their own shelf. The mapping is address -> the name
# people actually call them, because "person3 made and tested" is nobody's
# idea of a pill.
MADE_SUFFIX = " made and tested"

COOKS = {}
for _pair in os.getenv(
        "KITCHEN_COOKS",
        "person3@example.com=mama,"
        "person2@example.com=casey,"
        "person1@example.com=jason,"
        "person4@example.com=jason").split(","):
    if "=" in _pair:
        _email, _name = _pair.split("=", 1)
        if _email.strip() and _name.strip():
            COOKS[_email.strip().lower()] = _name.strip().lower()


def friendly_name(email: str) -> str:
    """'person3@example.com' -> 'mama', if KITCHEN_COOKS says so; else the
    part before the @.

    The one place this app turns an address into a name a person would
    actually use -- "kept by mama", not "kept by person3". made_by() and
    added_by() build a pill on top of this; this is what you want anywhere
    else you are just saying who did something.
    """
    email = (email or "").strip().lower()
    name = COOKS.get(email) or email.split("@")[0] or "someone"
    return _CLEAN.sub("", name)[:20].strip() or "someone"


def made_by(email: str) -> str:
    """The pill for whoever wrote this out. Falls back to the address's name.

    An unknown address still gets a pill rather than nothing -- somebody added
    to /admin/people who is not in the map above has still cooked the thing,
    and losing that is worse than a pill reading "aunt-june made and tested".
    """
    email = (email or "").strip().lower()
    name = COOKS.get(email) or email.split("@")[0] or "somebody"
    name = _CLEAN.sub("", name)[:14].strip() or "somebody"
    return f"{name}{MADE_SUFFIX}"


# --- who put it there, as opposed to who cooked it -------------------------
#
# A SEPARATE IDEA FROM "made and tested", and worth keeping separate.
# Pasting a link is not the same as having stood at the stove, and one pill
# meaning both would make the valuable one worthless: "mama" would appear on
# forty recipes she found on the internet and three she actually cooks.
#
# So this is provenance, applied automatically to everything, and the
# made-and-tested pill stays a thing a person says on purpose.
#
# DERIVED, NEVER STORED. The draft already records who imported it in its
# who_email column; computing the pill from that means the two cannot drift,
# and renaming a cook in KITCHEN_COOKS renames their pill everywhere at once
# rather than on new recipes only.
ADDED_SUFFIX = "'s finds"


def added_by(email: str) -> str:
    """The pill for whoever put this recipe here."""
    email = (email or "").strip().lower()
    if not email:
        return ""
    name = COOKS.get(email) or email.split("@")[0] or "somebody"
    name = _CLEAN.sub("", name)[:14].strip()
    return f"{name}{ADDED_SUFFIX}" if name else ""


def is_added_pill(label: str) -> bool:
    return (label or "").endswith(ADDED_SUFFIX)


def is_person_pill(label: str) -> bool:
    """Either kind -- a pill about a person rather than about the food."""
    return is_made_pill(label) or is_added_pill(label)


def is_made_pill(label: str) -> bool:
    return (label or "").endswith(MADE_SUFFIX)


def protected(found) -> list:
    """The made-and-tested pills out of a list, in order."""
    return [f for f in (found or []) if is_made_pill(f)]

SYSTEM = """You label recipes so somebody can browse them by tapping a pill.

Rules:
- Three or four labels. Fewer is better than padding.
- Each label is ONE ordinary word where possible, two at most, lowercase.
  "chicken", "quick", "soup", "vegetarian", "baking". Never a sentence.
- Cover different KINDS of thing: what meal it is, the main ingredient, how
  much effort it is, any dietary fact. Do not give four labels about the same
  aspect.
- Label THIS recipe only, from its own title and ingredients.
- No brand names, no adjectives that just mean nice ("delicious", "amazing",
  "tasty"), no label that applies to every recipe ("food", "recipe", "dinner
  idea").
- No preamble. JSON only."""

# NOTE ON WHY THE PROMPT NO LONGER SHOWS THE EXISTING LABELS.
#
# It used to. The idea was sound -- show the model the vocabulary so it reuses
# "quick" rather than coining "fast", "speedy" and "quick weeknight" -- and on
# a big model it works. On llama3.2:3b it failed in the most literal way
# possible: given a list containing "chocolate" and "brownies", it labelled a
# GUACAMOLE recipe ["serves", "chocolate", "brownies", "sugar"]. It was not
# labelling at all; it was copying the nearest text in its context.
#
# The prompt also used to include a "Serves: 4" line, and "serves" duly came
# back as a label on everything. A small model does not reliably tell the
# difference between the thing to label and the scaffolding around it.
#
# So the model now sees ONLY the recipe, which it is good at -- "chicken",
# "easy", "fajitas" is a fine answer -- and the grouping happens in snap()
# below, in code, where it is exact. Same rule as everywhere else here: do it
# in code if code can be exact, and only then ask the model.

# Things that are labels in form but useless in practice. A pill nobody would
# ever tap to narrow anything down is worse than no pill: it takes up space on
# a phone and dilutes the ones that work.
_USELESS = {
    "recipe", "recipes", "food", "meal", "dish", "cooking", "delicious",
    "tasty", "amazing", "easy recipe", "dinner idea", "yummy", "good",
    "homemade", "best", "classic recipe", "great",
    # Scaffolding a small model mistakes for content. Every one of these has
    # actually come back as a label: "serves" and "optional" off ingredient
    # lines, the timing words off the header.
    "serves", "optional", "serving", "servings", "prep", "cook", "total",
    "minutes", "ingredients", "method", "instructions", "garnish", "taste",
    "to taste", "divided", "chopped", "sliced", "fresh", "large", "small",
    "medium", "warm", "cold", "hot",
}

# --- the dietary claims, and why they are checked ---------------------------
#
# A wrong dietary label is not the same kind of mistake as a wrong label. Get
# "quick" wrong and somebody is annoyed; get "vegetarian" wrong and you have
# told a vegetarian that a chicken dish is fine. The model has already done
# exactly this on this machine -- it labelled "Easy Chicken Fajitas" as
# ["chicken", "fajitas", "easy", "vegetarian"], which is both wrong and
# self-contradicting in the same four words.
#
# So a dietary claim is never taken on the model's word. It survives only if
# nothing in the ingredients contradicts it, checked here. Same position
# subs.py takes on allergy warnings, for the same reason: this is the class of
# answer where being usually-right is not good enough.
_MEAT = re.compile(
    r"\b(chicken|beef|pork|lamb|bacon|ham|sausage|mince|steak|turkey|duck|"
    r"veal|venison|chorizo|pancetta|prosciutto|salami|gelatin[e]?|lard|"
    r"anchov(y|ies)|fish|salmon|tuna|cod|prawn|shrimp|crab|lobster|squid|"
    r"oyster|mussel|clam|scallop|worcestershire)\b", re.I)
_DAIRY_EGG = re.compile(
    r"\b(milk|cream|butter|cheese|yoghurt|yogurt|egg|eggs|parmesan|mozzarella|"
    r"cheddar|mascarpone|ricotta|feta|ghee|custard|buttermilk)\b", re.I)
_GLUTEN = re.compile(
    r"\b(flour|bread|breadcrumb|pasta|noodle|couscous|barley|rye|semolina|"
    r"pastry|filo|puff|orzo|wheat|cracker|biscuit|scone|cake)\b", re.I)

_DIET_CHECKS = [
    (("vegetarian", "veggie"), (_MEAT,)),
    (("vegan",), (_MEAT, _DAIRY_EGG)),
    (("dairy-free", "dairy free"), (_DAIRY_EGG,)),
    (("gluten-free", "gluten free"), (_GLUTEN,)),
]


def verify_dietary(found: list, recipe: dict) -> list:
    """Drop any dietary claim the ingredients contradict.

    Only ever REMOVES. It never adds "vegetarian" to something that looks
    meat-free, because absence of a meat word is not evidence -- a recipe
    calling for "stock" or "the usual sauce" could be anything, and claiming a
    diet on that basis is the same mistake in the other direction.
    """
    text = " ".join(recipe.get("ingredients", []) or [])
    if not text:
        return found
    out = []
    for label in found:
        contradicted = False
        for names, patterns in _DIET_CHECKS:
            if label in names and any(p.search(text) for p in patterns):
                contradicted = True
                break
        if not contradicted:
            out.append(label)
    return out



def tidy(raw: list) -> list:
    """Enforce the shape the prompt asked for. Never trusts it to have done so."""
    out = []
    for item in raw or []:
        label = _CLEAN.sub("", str(item).strip().lower())
        label = re.sub(r"\s+", " ", label).strip(" -&")
        # TRIM BEFORE MEASURING. The other way round -- length check first --
        # threw away "quick weeknight chicken dinner" entirely because it is
        # 30 characters, when the whole point of the word-trim is to rescue
        # exactly that case down to "quick weeknight".
        words = label.split()
        if len(words) > 2:
            label = " ".join(words[:2])
        if not label or len(label) > MAX_LENGTH or label in _USELESS:
            continue
        if label not in out:
            out.append(label)
        if len(out) >= MAX_PER_RECIPE:
            break
    return out


def _squash(word: str) -> str:
    """'crock pot' and 'crock-pot' both become 'crockpot'.

    Also squashes the plural, so 'crock pots' lands there too -- otherwise the
    squash and the stem checks each catch half the problem and a label needs
    to be spelled exactly one of two ways to be gathered up.
    """
    flat = re.sub(r"[ \-]", "", (word or "").strip().lower())
    return flat[:-1] if len(flat) > 4 and flat.endswith("s") else flat


def _stems(word: str) -> set:
    """Every plausible root of one word. A SET, not a single answer.

    A one-answer stemmer has to guess, and guessing wrong is silent: "brownies"
    under an "ies -> y" rule becomes "browny", which does not match "brownie",
    so the two stay as separate pills and nobody ever finds out why. Returning
    all the candidates and asking whether ANY overlap means the awkward cases
    -- brownies/brownie, curries/curry, potatoes/potato -- all land together
    without needing to know which rule was the right one.

    Crude on purpose: no library, and only the endings that actually differ
    between two labels for the same idea.
    """
    w = word.strip().lower()
    out = {w}
    for suffix, replacement in (("ies", "y"), ("ies", "ie"), ("es", ""),
                                ("s", ""), ("ing", ""), ("ing", "e")):
        if len(w) > len(suffix) + 2 and w.endswith(suffix):
            out.add(w[: -len(suffix)] + replacement)
    return out


def vocabulary(used: list) -> list:
    """What snap() matches against: the pills in use, plus the seeded ones.

    USED FIRST, and that ordering is the point. snap() takes the first match it
    finds, so a word already on real recipes beats a seed word -- the list you
    have actually grown wins over the list I guessed at.

    Seeds come in with a count of zero so they can be told apart, which is what
    keeps them off the browse page until something earns them.

    THE PERSON PILLS ARE HELD BACK, and this is the load-bearing part.
    "mama made and tested" and "casey's finds" say a specific person did a
    specific thing. They are awarded by main.py when that person presses the
    button, and by nothing else. But snap() matches a bare word inside a longer
    pill, so with them in this list the label "mama" -- which is exactly what a
    model emits for a recipe called "Mama's Cornbread" -- snapped straight onto
    "mama made and tested", and the app claimed she had cooked something she
    had never seen. Filtering at the point of use in suggest() could not catch
    it, because the pill is created BY the snap, after that filter has run.
    """
    have = {name for name, _ in used}
    keep = [(name, n) for name, n in used if not is_person_pill(name)]
    return keep + [(s, 0) for s in SEED if s not in have]


# Genuinely different words for the same thing. Squashing (below) only
# catches spacing/hyphen variants of the SAME word -- "crock pot" and
# "crockpot" share the word "pot", so squashing sees it on its own, with
# no alias needed. "crockpot" itself shares no word with "slow-cooker" at
# all, so nothing in snap() would ever join them without saying so here.
#
# "slow-cooker" is the canonical spelling as of this entry (Jason's own
# call) -- was "crockpot" earlier in this file's history; every recipe
# already carrying "crockpot" gets converted by labels_fix.py's backfill,
# same as the "slow cooker" -> "crockpot" direction was before it.
ALIAS = {"crockpot": "slow-cooker"}


def snap(found: list, vocabulary: list) -> list:
    """Pull new labels onto existing ones where they mean the same thing.

    THIS IS WHAT KEEPS THE PILLS FEW. Without it you get "brownie" and
    "brownies" as two pills with one recipe each, and a browse page that grows
    a new pill for every recipe rather than gathering them.

    Three ways a new label snaps to an old one, all exact rather than fuzzy:
        same word            "chicken" -> "chicken"
        same stem            "brownies" -> "brownie"
        one contains the other, on a word boundary
                             "chicken thighs" -> "chicken"

    Anything that matches none of those is genuinely new and stays as it is --
    which is what "invent freely" means. The vocabulary is ordered most-used
    first, so a tie snaps to the more popular pill.
    """
    known = [name for name, _ in vocabulary]
    by_stem = {}
    by_squash = {}
    # Aliases go in first, keyed by their own squashed form -- checked the
    # same way any other squash match is, so "crockpot" and "crockpots"
    # both resolve without listing every spacing/plural by hand.
    for alias, canonical in ALIAS.items():
        by_squash[_squash(alias)] = canonical
    for name in known:
        for stem in _stems(name):
            by_stem.setdefault(stem, name)
        # SPACES AND HYPHENS REMOVED. "crock pot" and "crock-pot" are the
        # same word said two ways, and no amount of stemming sees that --
        # the stem of "pot" is "pot". Squashing is the only check that
        # catches it (they both still route to "slow-cooker" via ALIAS
        # above, since "crockpot"'s squashed form is an alias key too).
        by_squash.setdefault(_squash(name), name)

    alias_squash = {_squash(a) for a in ALIAS}
    out = []
    for label in found:
        pick = label
        # ALIAS BEATS "ALREADY KNOWN". A label can be an established pill in
        # its own right -- CAUGHT LIVE: "slow-cooker" had already been used
        # on real recipes before ALIAS existed, so it was already sitting in
        # `known` with a real count, and the plain `label in known` check
        # below would keep waving it through forever. An alias means "this
        # spelling never gets to be its own pill", which has to win even
        # over a spelling that already snuck in before the alias existed.
        if _squash(label) in alias_squash:
            pick = by_squash[_squash(label)]
        elif label in known:
            pick = label
        elif _squash(label) in by_squash:
            pick = by_squash[_squash(label)]
        elif _stems(label) & set(by_stem):
            # Most-used first, so a label matching two pills joins the busier
            # one rather than whichever happened to be checked first.
            pick = next(by_stem[s] for s in _stems(label) if s in by_stem)
        else:
            for name in known:
                # Word-boundary containment only. Without the boundary,
                # "pear" would snap to "spear" and "ham" to "hamburger".
                if re.search(rf"\b{re.escape(name)}\b", label) or \
                   re.search(rf"\b{re.escape(label)}\b", name):
                    pick = name
                    break
        if pick not in out:
            out.append(pick)
    return out


async def suggest(recipe: dict, used: list) -> list:
    """Label one recipe. Returns [] rather than raising -- labels are a nicety.

    A failure here must never fail an import. The recipe, the guide, the
    scaling and the substitutions are the product; pills are how you find it
    again. Losing them costs a browse, and refusing the import over it would
    cost the whole thing.
    """
    # ONLY the title and the ingredients. No serving count, no existing
    # vocabulary, no field names the model might mistake for content -- see
    # the long note above SYSTEM for what happened when there were.
    vocab = vocabulary(used)
    ingredients = "\n".join(recipe.get("ingredients", [])[:25])
    ask = f"{recipe.get('title', '')}\n\n{ingredients}"
    try:
        data = await llm.chat_json(
            SYSTEM + '\n\nReturn JSON: {"labels": ["", ""]}',
            ask, model=core.LLM_LABEL, temperature=0.2, max_tokens=300,
        )
    except llm.LLMError:
        return []
    found = tidy(data.get("labels") or [])
    found = verify_dietary(found, recipe)
    # PROTECTED pills are stripped from whatever the model said and put back
    # from the recipe afterwards. The model does not get to award "mama made
    # and tested" because it cannot possibly know, and it does not get to take
    # one away either.
    found = [f for f in found if not is_made_pill(f)]
    found = snap(found, vocab)
    keep = protected(recipe.get("labels"))
    return keep + [f for f in found if f not in keep]


# CAUGHT LIVE: TOOLS_SYSTEM below used to list example equipment by name (a
# stand mixer, a food processor, a blender, a Dutch oven, a meat thermometer,
# a slow cooker, ...) and llama3.2:3b copied that same list back on nearly
# every recipe regardless of content -- a shrimp bake and a green salad both
# came back needing a stand mixer, a Dutch oven and a blender, and one
# recipe whose entire premise WAS a slow cooker didn't get "slow cooker" at
# all. Exact same failure the file's SYSTEM prompt above already hit and
# fixed (see the NOTE above _USELESS): a small model does not reliably
# separate the thing to label from the scaffolding around it. So this note
# is a plain comment, not part of the string the model reads -- the prompt
# itself names no equipment at all, the same fix applied there.
TOOLS_SYSTEM = """You read a recipe's own ingredients and steps below and name
the specific kitchen equipment THIS recipe's own text implies -- something
beyond a knife, a cutting board, a bowl, a spoon, or a plain pot or pan,
which every kitchen already has. Point to the exact instruction that implies
each item; if you cannot point to one, leave it out. Judge only the text
given to you, never what recipes like this one usually need or what a
similar recipe elsewhere might call for. Most recipes need zero or one
extra item, not several. If nothing in the steps calls for anything beyond
the basics, return an empty list rather than inventing equipment to have
something to say."""


async def suggest_tools(recipe: dict, model: str = core.LLM_LABEL) -> list:
    """What equipment this recipe needs, worked out from its own steps.

    Used only when the source page published nothing under schema.org's
    `tool` field -- see crawl.py's _tools_from_ld() docstring for why that
    is most pages, not a few. Never raises; a missing tools list is a
    cosmetic gap, not a reason to fail an import or a backfill run.

    `model` defaults to LLM_LABEL (llama3.2:3b) -- fine for a fresh
    import, where speed matters and a miss just means "try again later."
    A one-time BACKFILL across the whole library is a different trade:
    Jason's own choice (2026-08-26), asked directly, was LLM_READ
    (Phi-4, long-context) instead -- proven JSON-reliable in this app
    already (that is the whole reason recipe PARSING uses it), where
    LLM_LABEL's own small model is a documented, deliberate choice for
    short/fast answers, not a guarantee it never returns nothing. See
    reparse.py / the backfill script for the actual override.
    """
    ingredients = "\n".join(recipe.get("ingredients", [])[:25])
    steps = "\n".join(recipe.get("steps", [])[:25])
    ask = f"{recipe.get('title', '')}\n\nIngredients:\n{ingredients}\n\nSteps:\n{steps}"
    try:
        data = await llm.chat_json(
            TOOLS_SYSTEM + '\n\nReturn JSON: {"tools": ["", ""]}',
            ask, model=model, temperature=0.2, max_tokens=200,
        )
    except llm.LLMError:
        return []
    tools = [str(t).strip() for t in (data.get("tools") or []) if str(t).strip()]
    return tools[:6]
