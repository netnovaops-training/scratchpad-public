"""No pork, no beef -- judged on the ingredients, where the truth is.

WHAT THIS IS AND IS NOT
-----------------------
Sashin eats meat. This is not a vegetarian filter and it must not behave
like one: chicken, lamb, fish, prawns and everything else are fine. Two
ingredients are excluded, pork and beef, because he does not eat them --
an observance, not a preference, which is why the rule is enforced in the
data layer rather than offered as a browsing toggle. A card hidden in the
browser is still a recipe that was sent to the browser.

WHY THE INGREDIENTS AND NOT THE TITLE (Jason, 2026-09-03)
---------------------------------------------------------
"they're mostly in the ingredients or food items list. not so much the
name of the title because they could be faux beef or imitation or
impossible beef."

Exactly so, and it cuts deeper than the title: "Impossible beef",
"Beyond beef", "plant-based bacon" and "veggie sausage" are not pork and
not beef ANYWHERE they appear, ingredient list included. So every match
is checked for a qualifier in front of it first, and a qualified one is
not a match at all. Getting this wrong would hide precisely the recipes
he is most likely to want.

The title is therefore used ONLY when the ingredients cannot be read at
all -- otherwise a vegan "Beef" Wellington made with seitan would be
hidden for having the word in its name, which is the mistake this whole
comment exists to avoid.

THE TWO TIERS, AND WHY THE SECOND ONE EXISTS
--------------------------------------------
CERTAIN is pork and beef under every name they go by -- bacon, gammon,
prosciutto, brisket, veal, and so on. No judgement needed.

LIKELY is the short list of words that are usually pork or beef and
occasionally are not: a sausage can be chicken, mince can be lamb. These
are excluded by default because the cost is not symmetric -- a false
exclusion means he misses a turkey-sausage recipe, a false inclusion
means he is served pork. Set KITCHEN_STRICT_MEAT=0 to allow them
through; the list is right here so the choice is his and not mine.

DELIBERATELY LOOSER THAN Kitchen's COPY. Do not "fix" the difference.
This is the BACKSTOP and it sees the browse payload -- id, title, image,
course, no ingredients at all by design, already judged by Kitchen which
alone could read them. Treating that as unreadable here hid all 191
permitted recipes along with the rest. Caught live.
"""
import os
import re

# Everybody restricted to no pork and no beef, by email. Named in the
# environment rather than inferred from a permission level, because
# "which apps may this person open" and "what may this person be shown"
# are different questions and should not share an answer.
NO_BEEF_PORK = {
    e.strip().lower()
    for e in os.getenv("RECIPEAPP_NO_BEEF_PORK",
                       os.getenv("KITCHEN_NO_BEEF_PORK", "")).split(",")
    if e.strip()
}

STRICT = os.getenv("KITCHEN_STRICT_MEAT", "1").strip() not in ("0", "false", "no")

# --- pork and beef, under every name they go by ------------------------------
_CERTAIN = (
    # beef
    r"beef|steak|sirloin|ribeye|rib-eye|brisket|oxtail|ox\s?cheek|veal|"
    r"tallow|suet|pastrami|corned\s?beef|bresaola|filet\s?mignon|"
    # pork
    r"pork|bacon|ham|gammon|lard|lardo|guanciale|pancetta|prosciutto|"
    r"speck|jamon|serrano\s?ham|capicola|coppa|mortadella|bratwurst|"
    r"carnitas|trotter|pig\s?trotter|hock|chitterlings|scrapple|"
    r"pepperoni|salami|chorizo|andouille|kielbasa|prosciutto"
)

# --- usually one of the two, occasionally not --------------------------------
#  A sausage can be chicken. Mince can be lamb. Excluded by default; see
#  the header for why the default sits where it does.
_LIKELY = (
    r"sausage|mince|minced\s?meat|ground\s?meat|meatball|meatloaf|"
    r"bolognese|ragu|gelatine|gelatin|hot\s?dog|frankfurter|wiener|"
    r"bologna|charcuterie"
)

# PLURALS MATTER, and missing them was a real hole: a recipe calling for
# "6 sausages" or "2 pork chops" matched nothing at all, because \b after
# the word fails against the s. Caught by a test, not by reading.
_CERTAIN_RE = re.compile(r"\b(" + _CERTAIN + r")(?:es|s)?\b", re.I)
_LIKELY_RE = re.compile(r"\b(" + _LIKELY + r")(?:es|s)?\b", re.I)

#  What makes a meat word stop meaning pork or beef. Checked in the
#  run-up to every match, so "impossible beef" and "plant-based bacon"
#  read as what they are rather than as what they are named after.
#
#  OTHER MEATS ARE QUALIFIERS TOO, and this is the half that is easy to
#  miss: Sashin eats meat. "Turkey bacon", "chicken sausage" and "lamb
#  meatballs" are none of them pork and none of them beef, and hiding
#  them would be the vegetarian filter this is explicitly not.
_FAUX = re.compile(
    r"("
    # not meat at all
    r"impossible|beyond|plant[\s-]?based|vegan|vegetarian|veggie|"
    r"meatless|meat[\s-]?free|faux|imitation|mock|fake|substitute|"
    r"alternative|soy|soya|tofu|tvp|seitan|jackfruit|quorn|"
    r"mushroom|lentil|walnut|cauliflower|"
    # grown rather than farmed -- Jason, 2026-09-03: "even if they
    # are chemically or lab grown, I like to include those too"
    r"lab[\s-]?grown|cultivated|cultured|cell[\s-]?based|"
    r"precision[\s-]?fermented|fermented|synthetic|"
    # meat, but not one of the two he avoids
    r"chicken|turkey|lamb|mutton|duck|goose|goat|venison|rabbit|bison|"
    r"fish|salmon|tuna|cod|haddock|prawn|shrimp|crab"
    r")"
    r"[\s-]*\w*[\s-]*$", re.I)

# Fields that might carry an ingredient, in the shapes Kitchen returns.
_INGREDIENT_KEYS = ("ingredients", "ingredient_list", "ingredients_text",
                    "recipe_ingredients")


def _ingredient_text(recipe: dict) -> str:
    """Everything this recipe says it is made of, as one string."""
    bits: list[str] = []
    for key in _INGREDIENT_KEYS:
        val = recipe.get(key)
        if isinstance(val, str):
            bits.append(val)
        elif isinstance(val, (list, tuple)):
            for item in val:
                if isinstance(item, str):
                    bits.append(item)
                elif isinstance(item, dict):
                    for k in ("name", "text", "note", "original_text",
                              "display", "ingredient"):
                        v = item.get(k)
                        if isinstance(v, str):
                            bits.append(v)
                    food = item.get("food")
                    if isinstance(food, dict) and isinstance(food.get("name"), str):
                        bits.append(food["name"])
                    elif isinstance(food, str):
                        bits.append(food)
    # One line per ingredient, so a qualifier on one line cannot reach
    # across and excuse the meat on the next.
    return "\n".join(bits)


def _real_match(text: str) -> str:
    """The first genuine pork or beef mention, or '' if there is none.

    A match preceded by a faux qualifier -- impossible, plant-based,
    veggie -- is not a match. The look-back stops at the start of the
    line so a qualifier cannot reach across ingredients.
    """
    patterns = [_CERTAIN_RE] + ([_LIKELY_RE] if STRICT else [])
    for rx in patterns:
        for m in rx.finditer(text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            before = text[line_start:m.start()]
            if _FAUX.search(before):
                continue          # "impossible beef" -- not beef
            return m.group(0).lower()
    return ""


def why_hidden(recipe: dict) -> str:
    """'' if this recipe may be shown, otherwise the reason it may not."""
    if not isinstance(recipe, dict):
        return "not a recipe"
    text = _ingredient_text(recipe)
    if text.strip():
        hit = _real_match(text)
        return f"contains {hit}" if hit else ""
    if not any(k in recipe for k in _INGREDIENT_KEYS):
        return ""        # the browse payload -- Kitchen already judged it
    name = f"{recipe.get('name', '')} {recipe.get('title', '')}"
    hit = _real_match(name)
    return f"contains {hit}" if hit else "ingredients could not be read"


def allowed(recipe: dict) -> bool:
    return not why_hidden(recipe)


def restricted(email: str) -> bool:
    return bool(email) and email.strip().lower() in NO_BEEF_PORK


# The pill. A recipe earns it by having a readable ingredient list with
# no pork and no beef in it -- never by the absence of a list, which
# proves nothing.
PILL = "no pork or beef"


def pill_for(recipe: dict) -> str:
    """PILL if this recipe is verifiably free of both, else ''."""
    if not isinstance(recipe, dict):
        return ""
    if not _ingredient_text(recipe).strip():
        return ""
    return "" if _real_match(_ingredient_text(recipe)) else PILL

# --- what to use instead -----------------------------------------------------
#
#  Jason, 2026-09-03: "so that he doesn't feel like he's left out with
#  recipes so we can make things together" -- and then, deciding it:
#  "so my new partner knows he can make these items if there are
#  substitutions available and if there are adjustments to the
#  ingredients so he knows to be able to make them on his own."
#
#  So a recipe with beef in it is no longer simply gone. It is shown,
#  with the swap named and any adjustment that comes with it, because
#  being handed the adapted version is a different experience from being
#  handed a shorter list of recipes and never knowing why.
#
#  COMPUTED AT READ TIME, NEVER STORED. Every recipe imported from here
#  on is covered the moment it lands -- there is no flag to set, no
#  backfill to run, and no chance of a stored answer going stale when
#  this table is edited.
#
#  The notes carry the ADJUSTMENT, not just the name, because that is
#  what makes the swap actually work: plant mince does not render fat,
#  jackfruit needs seasoning that pork brought with it, and a cook who is
#  not told that gets a worse dinner and blames themselves.
SUBSTITUTES = {
    "beef": ("Impossible or Beyond beef, or lab-grown beef",
             "browns like mince but renders almost no fat -- add a spoon of oil "
             "to the pan, and salt a little harder at the end"),
    "steak": ("a thick portobello cap or seitan steak",
              "sear hotter and shorter; it will not need resting"),
    "brisket": ("jackfruit or seitan",
                "already tender, so add it late rather than braising it for hours"),
    "veal": ("Impossible beef or seitan", "season more assertively"),
    "mince": ("plant mince, or turkey or lamb mince",
              "plant mince needs added fat; turkey and lamb behave as normal"),
    "pork": ("jackfruit, king oyster mushroom, or plant-based pork",
             "jackfruit shreds like slow-cooked pork -- season it early, it "
             "arrives with none of its own"),
    "bacon": ("plant-based bacon, or turkey bacon",
              "crisps faster and burns sooner; take the heat down a notch"),
    "ham": ("smoked turkey, or plant-based deli slices", "no adjustment needed"),
    "gammon": ("smoked turkey joint", "cooks faster -- check it early"),
    "pancetta": ("smoked mushroom or plant-based lardons",
                 "start with oil in the pan, there is no fat to render"),
    "guanciale": ("smoked mushroom or plant-based lardons",
                  "same -- the fat has to come from the pan"),
    "prosciutto": ("thin-sliced smoked turkey", "no adjustment needed"),
    "chorizo": ("soy chorizo, or smoked paprika with plant mince",
                "soy chorizo is already seasoned; go easy on added salt"),
    "pepperoni": ("plant-based pepperoni", "no adjustment needed"),
    "salami": ("plant-based salami", "no adjustment needed"),
    "sausage": ("chicken, turkey or plant-based sausages",
                "plant sausages need oil in the pan and a gentler heat"),
    "lard": ("butter, ghee or a neutral oil", "use the same weight"),
    "suet": ("vegetable suet", "a like-for-like swap"),
    "tallow": ("a neutral oil or butter", "use the same weight"),
    "gelatine": ("agar agar", "roughly half the weight, and it must be boiled"),
    "gelatin": ("agar agar", "roughly half the weight, and it must be boiled"),
    "meatball": ("chicken, lamb or plant-based meatballs",
                 "plant versions hold together better if not stirred early"),
    "bolognese": ("plant mince, or turkey or lamb mince",
                  "add a spoon of oil and cook the soffritto a little longer"),
    "ragu": ("plant mince, or lamb", "same -- it will want a little added fat"),
}

# Longest first, so "corned beef" is answered before "beef".
_SUB_KEYS = sorted(SUBSTITUTES, key=len, reverse=True)


def _substitute_for(term: str) -> tuple[str, str]:
    t = term.lower()
    for key in _SUB_KEYS:
        if key in t:
            return SUBSTITUTES[key]
    return ("a plant-based or poultry equivalent",
            "check the seasoning at the end -- most swaps arrive with less of "
            "their own")


def all_matches(recipe: dict) -> list[str]:
    """Every genuine pork or beef term in this recipe, in order, no repeats."""
    text = _ingredient_text(recipe)
    if not text.strip():
        return []
    seen, out, claimed = set(), [], set()
    # CERTAIN first, and a line it claims is not re-reported by the
    # weaker pass: "500g beef mince" is ONE swap, not a beef swap and a
    # mince swap. Telling a cook to replace the same thing twice reads as
    # a machine that has not understood the sentence.
    for rx, is_certain in ((_CERTAIN_RE, True),
                           *(((_LIKELY_RE, False),) if STRICT else ())):
        for m in rx.finditer(text):
            line_start = text.rfind("\n", 0, m.start()) + 1
            if _FAUX.search(text[line_start:m.start()]):
                continue
            # One swap per ingredient line, first match wins. "4 beef
            # steaks" is one thing to replace, not two.
            if line_start in claimed:
                continue
            claimed.add(line_start)
            term = m.group(0).lower()
            if term not in seen:
                seen.add(term)
                out.append(term)
    return out


def adaptation(recipe: dict) -> dict:
    """How this recipe can be made without pork or beef.

    Returns:
      status  "clean"      nothing to change
              "adaptable"  swaps named below make it his
              "unknown"    the ingredients cannot be read -- see why_hidden
      swaps   [{from, to, note}] in the order they appear
    """
    if not _ingredient_text(recipe).strip():
        return {"status": "unknown", "swaps": []}
    hits = all_matches(recipe)
    if not hits:
        return {"status": "clean", "swaps": []}
    swaps = []
    for term in hits:
        to, note = _substitute_for(term)
        swaps.append({"from": term, "to": to, "note": note})
    return {"status": "adaptable", "swaps": swaps}


def filter_payload(data, email: str):
    """The backstop, applied where every Kitchen response passes through.

    Kitchen is the authority -- it is the only layer holding ingredient
    lists at browse time, and it has already judged. This exists so a
    NEW route added later cannot leak by simply forgetting to filter: it
    would have to go out of its way to bypass this.

    A payload with no ingredient field is passed through untouched. That
    is not laziness, it is the browse list, and judging it here once hid
    every permitted recipe along with the rest.
    """
    if not restricted(email):
        return data
    if isinstance(data, list):
        return [d for d in data if not isinstance(d, dict) or allowed(d)]
    if not isinstance(data, dict):
        return data
    if any(k in data for k in _INGREDIENT_KEYS) or "steps" in data:
        return data if allowed(data) else {"error": "not available",
                                           "hidden": True}
    out = dict(data)
    for key in ("recipes", "results", "items", "data"):
        if isinstance(out.get(key), list):
            kept = [d for d in out[key]
                    if not isinstance(d, dict) or allowed(d)]
            removed = len(out[key]) - len(kept)
            out[key] = kept
            if removed:
                out["hidden_count"] = out.get("hidden_count", 0) + removed
    return out
