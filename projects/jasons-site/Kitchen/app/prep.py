"""The briefing you get before you start cooking.

Jason, 2026-09-03: "when the recipe page loads on a recipe we're going to
need x y z and x ingredients of a b c go ahead and preheat the oven to
450 while we're preparing all this."

That is mise en place, and it is the difference between a recipe and
somebody teaching you to cook it. Three things, in the order a cook
actually needs them:

  1. the equipment, so nothing is discovered missing halfway through
  2. what you are working with
  3. the oven, EARLY -- because the whole value of hearing it up front is
     that it heats while you chop, and a recipe that buries "heat oven to
     200C" in step four has already cost you twenty minutes

NOTHING HERE IS INVENTED. The temperature is reported only if a step
actually says one; a recipe that never gives one gets no preheat line
rather than a plausible guess. The importer already refuses to invent a
cooking time or temperature, and this refuses to add one afterwards --
guessing 180C for somebody's oven is how a cake gets ruined and how a
chicken gets served underdone.

THE TRAP THAT SHAPED THE PARSING. Real steps in this library include
"Preheat 1 tablespoon olive oil over medium high" -- a pan, not an oven.
So a temperature only counts when the word oven, broiler or grill is
close enough in front of it to be talking about the same thing.
"""
import re

# 450°F, 450 F, 450 degrees F, 230C, gas mark 6 -- with the unit optional
# because plenty of steps write only the number after saying "oven".
_TEMP = re.compile(
    r"(?P<n>\d{2,3})\s*(?:°|degrees?\s*)?\s*(?P<u>[FC])\b"
    r"|(?P<n2>\d{2,3})\s*degrees\b"
    r"|gas\s*(?:mark\s*)?(?P<gas>\d)\b", re.I)

# The word that makes a temperature an OVEN temperature. Anything else --
# oil, a pan, a griddle -- is somebody heating something on the hob.
_OVEN_WORD = re.compile(r"\b(oven|broiler|grill|air[\s-]?fryer)\b", re.I)

_STEP_TEXT = lambda st: st if isinstance(st, str) else (st.get("text") or "")


def _steps(recipe: dict) -> list[str]:
    return [_STEP_TEXT(s) for s in (recipe.get("steps") or [])]


def oven(recipe: dict) -> dict | None:
    """The oven setting this recipe asks for, or None if it never says.

    Returns {f, c, gas, text, step} -- `step` being the 1-based step it
    was found in, which is what tells the page whether saying it early is
    doing the cook a favour or just repeating step one.
    """
    for i, text in enumerate(_steps(recipe), 1):
        for m in _OVEN_WORD.finditer(text):
            # The temperature has to be near the oven word, and after it:
            # "heat oven to 200C", "oven to 450°F", "oven to gas 6".
            window = text[m.end():m.end() + 60]
            t = _TEMP.search(window)
            if not t:
                continue
            f = c = gas = None
            if t.group("gas"):
                gas = int(t.group("gas"))
            elif t.group("u"):
                v = int(t.group("n"))
                (f, c) = (v, None) if t.group("u").upper() == "F" else (None, v)
            elif t.group("n2"):
                v = int(t.group("n2"))
                # A bare "400 degrees" is Fahrenheit in practice; a bare
                # "200 degrees" is Celsius. The gap between the two scales
                # is wide enough that this is safe, and both readings are
                # shown anyway so nobody is relying on the guess.
                (f, c) = (v, None) if v >= 250 else (None, v)
            # Both scales, when only one was written -- a cook with the
            # other kind of oven should not have to do the arithmetic.
            if f is not None and c is None:
                c = round((f - 32) * 5 / 9 / 5) * 5
            elif c is not None and f is None:
                f = round((c * 9 / 5 + 32) / 5) * 5
            bits = []
            if f is not None:
                bits.append(f"{f}°F")
            if c is not None:
                bits.append(f"({c}°C)")
            if gas is not None:
                bits.append(f"gas mark {gas}")
            return {"f": f, "c": c, "gas": gas,
                    "text": " ".join(bits), "step": i,
                    "word": m.group(0).lower()}
    return None


# Quantities, so a spoken list says "beef mince" and not "500 g beef
# mince, 2 x 400 g cans chopped tomatoes".
#
# THE UNIT NEEDS A WORD BOUNDARY AFTER IT. Without one, the "g" in the
# unit list matched the start of "garlic" and the briefing offered to
# fetch some "arlic". Caught by running it over the real library rather
# than by reading the pattern.
_FRAC = r"[¼½¾⅓⅔⅛⅜⅝⅞]"
_NUM = r"(?:\d+\s+" + _FRAC + r"|\d+(?:[./]\d+)?|" + _FRAC + r")"
_UNIT = (r"(?:g|kg|mg|ml|l|oz|lb|lbs|pound|pounds|ounce|ounces|cup|cups|"
         r"tbsp|tsp|tablespoon|tablespoons|teaspoon|teaspoons|clove|cloves|"
         r"can|cans|pack|packs|packet|packets|slice|slices|rasher|rashers|"
         r"sprig|sprigs|handful|handfuls|pinch|pinches|stick|sticks|"
         r"bunch|bunches|jar|jars|tin|tins|bottle|bottles|"
         r"large|medium|small|whole)")
# Repeated, because real lines say "1 lb 1 5/8 oz (500g) beef mince".
_QTY = re.compile(r"^\s*(?:" + _NUM + r"\s*(?:x\s*)?(?:" + _UNIT +
                  r"\b)?\s*(?:of\s+)?)+", re.I)


def short_ingredient(line: str) -> str:
    """An ingredient as you would say it out loud."""
    s = re.sub(r"\([^)]*\)", " ", str(line))          # drop parentheticals
    s = _QTY.sub("", s, count=1)
    s = re.split(r",| -- | – ", s)[0]            # drop ", finely chopped"
    return " ".join(s.split()).strip(" .,-") or str(line).strip()


def briefing(recipe: dict, max_spoken: int = 8) -> dict:
    """What to say and show before step one.

    `spoken` is written to be HEARD -- short clauses, no list punctuation
    a voice cannot render, and the oven last so it is the thing you act
    on as the sentence ends.
    """
    tools = [str(t).strip() for t in (recipe.get("tools") or []) if str(t).strip()]
    raw = [str(i) for i in (recipe.get("ingredients") or []) if str(i).strip()]
    names, seen = [], set()
    for line in raw:
        n = short_ingredient(line).lower()
        if n and n not in seen:
            seen.add(n)
            names.append(short_ingredient(line))
    ov = oven(recipe)

    say = []
    if tools:
        say.append("You're going to need " + _spoken_list(tools) + ".")
    if names:
        # A spoken list of twenty things is noise. Name a handful, count
        # the rest -- the full list is on the screen either way.
        head = names[:max_spoken]
        rest = len(names) - len(head)
        line = "For ingredients, " + _spoken_list(head)
        if rest > 0:
            line += f", and {rest} more on the screen"
        say.append(line + ".")
    if ov:
        # The whole point of hearing this now: it heats while you prep.
        say.append(f"Go ahead and set the {ov['word']} to {ov['text']} now, "
                   "so it's up to temperature while we get everything ready.")
    return {"tools": tools, "ingredients": names, "oven": ov,
            "spoken": " ".join(say)}


def _spoken_list(items: list) -> str:
    items = [str(i).strip() for i in items if str(i).strip()]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]
