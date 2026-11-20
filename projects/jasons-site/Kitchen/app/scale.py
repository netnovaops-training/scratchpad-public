"""Changing the portions. Arithmetic, not a language model.

WHY A MODEL NEVER TOUCHES THIS
------------------------------
"Halve this recipe" is the single most tempting thing to hand to an LLM and the
single worst. It is pure arithmetic on numbers that are right there in the
text, and a model will occasionally get one wrong -- not obviously wrong, but
2 tsp where it should be 1 1/2. You would not catch it. You would just make a
salty dinner and never know why. Multiplication in code is exact every time,
so multiplication in code is what happens here.

The model's job is the parts arithmetic cannot do, and those live elsewhere:
reading a page (crawl.py), suggesting a substitute (subs.py), thinking of
something to cook (ideas). Anything with a right answer stays here.

WHAT IT KNOWS IT CANNOT DO
--------------------------
Cooking does not scale linearly and this module does not pretend otherwise:

  * TIMES are never scaled. Half a stew still simmers for an hour. Times are
    left exactly as written and the guide page says so.
  * PAN SIZE is not adjusted, because it cannot be derived from a number.
  * EGGS come out as "1 1/2 eggs" when you halve a 3-egg cake, which is
    honest -- rounding it silently to 1 or 2 changes the recipe without
    telling you. The template flags fractional eggs so you can decide.

Anything with no number at all ("a pinch of salt", "olive oil, for frying")
is passed through untouched, which is correct: a pinch doubled is a pinch.
"""
import re
from fractions import Fraction

# The vulgar fractions that appear in real recipes, including the ones food
# sites paste from Word. Without this, "½ tsp" parses as no quantity at all
# and silently refuses to scale -- the most annoying possible failure, because
# every OTHER line on the page scaled and this one quietly did not.
_VULGAR = {
    "¼": Fraction(1, 4), "½": Fraction(1, 2), "¾": Fraction(3, 4),
    "⅓": Fraction(1, 3), "⅔": Fraction(2, 3),
    "⅕": Fraction(1, 5), "⅖": Fraction(2, 5),
    "⅗": Fraction(3, 5), "⅘": Fraction(4, 5),
    "⅙": Fraction(1, 6), "⅚": Fraction(5, 6),
    "⅛": Fraction(1, 8), "⅜": Fraction(3, 8),
    "⅝": Fraction(5, 8), "⅞": Fraction(7, 8),
}

# Written-out numbers, because "two cloves of garlic" is extremely common and
# stopping at "2 cloves" would leave half the ingredient list unscaled.
_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "a": 1, "an": 1, "half": Fraction(1, 2),
}

# 1 1/2   |   1.5   |   1/2   |   ½   |   1½
_NUM = r"""
    (?:
        (?P<whole>\d+)\s*(?P<vulgar>[¼-¾⅓-⅞])   # 1½
      | (?P<w2>\d+)\s+(?P<n2>\d+)\s*/\s*(?P<d2>\d+)                 # 1 1/2
      | (?P<n1>\d+)\s*/\s*(?P<d1>\d+)                               # 1/2
      | (?P<v1>[¼-¾⅓-⅞])                        # ½
      | (?P<dec>\d+(?:\.\d+)?)                                      # 1.5
    )
"""
# A leading quantity, optionally a range ("2-3 cloves", "1 to 2 tbsp").
#
# THE `rest` GROUP IS WHERE THE NUMBER IS ALLOWED TO END, and it used to be
# just `\s|$`. That quietly refused to scale "500g beef mince", "400ml stock"
# and "125g mozzarella" -- because the character after the number is a letter,
# not a space. On a British recipe that is most of the ingredient list, and the
# failure is invisible: the tablespoons all scaled, so the page looked like it
# had worked, and the beef was simply wrong.
#
# The third branch is a zero-width lookahead, so nothing is consumed and the
# unit stays attached to the text that follows.
_LEAD = re.compile(
    r"^\s*(?P<a>" + _NUM + r")"
    r"(?:\s*(?:-|–|—|to)\s*(?P<b>" + _NUM.replace("?P<", "?P<b_") + r"))?"
    r"(?P<rest>\s|$|(?=[a-zA-Z]))",
    re.X | re.I,
)
_LEAD_WORD = re.compile(r"^\s*(" + "|".join(sorted(_WORDS, key=len, reverse=True)) +
                        r")\b\s+(?=\S)", re.I)


def _to_fraction(m: re.Match, prefix: str = "") -> Fraction | None:
    """Turn one matched number into an exact Fraction."""
    g = lambda k: m.group(prefix + k) if (prefix + k) in m.groupdict() else None  # noqa: E731
    if g("whole") and g("vulgar"):
        return Fraction(int(g("whole"))) + _VULGAR[g("vulgar")]
    if g("w2"):
        return Fraction(int(g("w2"))) + Fraction(int(g("n2")), int(g("d2")))
    if g("n1"):
        return Fraction(int(g("n1")), int(g("d1")))
    if g("v1"):
        return _VULGAR[g("v1")]
    if g("dec"):
        return Fraction(g("dec"))
    return None


# Words that mean the number in front of them is a MEASUREMENT. Anything not
# followed by one of these is a COUNT of a physical object -- an onion, a
# rasher, a clove -- and counts round differently. See render().
_UNITS = re.compile(
    r"^\s*(?:cups?|tbsps?|tablespoons?|tbs|tsps?|teaspoons?"
    r"|grams?|kilos?|kilograms?|ounces?|pounds?"
    r"|millilitres?|milliliters?|litres?|liters?"
    r"|pints?|quarts?|gallons?|sticks?|cloves?|slices?|pinch(?:es)?"
    r"|handfuls?|drops?|dashes|dash|sprigs?|knobs?|cans?|tins?|packs?"
    r"|packets?|jars?|bunch(?:es)?|inch(?:es)?"
    # The abbreviations are kept separate because every one of them needs a
    # word boundary after it and the spelled-out words do not. Without the
    # boundary, "g" matches the g of "garlic" and "2 garlic cloves" is read as
    # a weight in grams -- which then rounds like a measurement and produces
    # "2 5/8 garlic cloves".
    r"|(?:c|g|kg|oz|lbs?|ml|l|fl|cm|mm)\b"
    r")", re.I)

# The units above that are an actual MEASURE, as opposed to a gesture. This
# distinction only matters for one thing: whether a leading "a" is a quantity.
#
# "a cup of sugar" doubled is two cups -- the "a" was a number.
# "a pinch of salt" doubled is still a pinch -- the "a" was not.
#
# Getting that wrong prints "2 pinch of salt", which is both wrong and
# illiterate, on a line that never needed touching.
_PRECISE_UNITS = re.compile(
    r"^\s*(?:cups?|tbsps?|tablespoons?|tbs|tsps?|teaspoons?"
    r"|grams?|kilos?|kilograms?|ounces?|pounds?"
    r"|millilitres?|milliliters?|litres?|liters?"
    r"|pints?|quarts?|gallons?|sticks?|cans?|tins?|packs?|packets?|jars?"
    r"|(?:c|g|kg|oz|lbs?|ml|l|fl)\b"
    r")", re.I)


def render(q: Fraction, countable: bool = False) -> str:
    """A number a person can act on.

    MEASUREMENTS snap to eighths, because your measuring spoons do. 0.333333
    cups is a true answer to "a third of a cup, doubled, halved" and a useless
    one; 2/3 is what the drawer contains. At or above 10 it rounds to a whole
    number, because "37 1/8 g" is false precision on a kitchen scale.

    COUNTS snap to halves, and to whole numbers once there are four or more.
    This is the difference between a page you can cook from and one you laugh
    at: scaling a lasagne from 6 to 8 is x1.375, and eighths turn "1 onion"
    into "1 3/8 onion". Nobody has ever measured three eighths of an onion.
    Half an onion is a real thing you can do, so halves are the floor; and by
    the time it says five, nobody is halving the fifth rasher of bacon.

    Neither rounding is silent -- the guide shows the original alongside every
    line it changed, so the exact figure is always one glance away.
    """
    if q <= 0:
        return "0"

    if countable:
        if q >= 4:
            return str(int(round(float(q))))
        snapped = Fraction(round(q * 2), 2)
        if snapped == 0:
            snapped = Fraction(1, 2)
    else:
        if q >= 10:
            return str(int(round(float(q))))
        snapped = Fraction(round(q * 8), 8)
        if snapped == 0:
            snapped = Fraction(1, 8)

    whole, rest = divmod(snapped, 1)
    if rest == 0:
        return str(int(whole))
    frac = f"{rest.numerator}/{rest.denominator}"
    return f"{int(whole)} {frac}" if whole else frac


# The article that belongs to a spoken quantity: "half A cup", "half THE
# butter". Matched only after a number word -- a line that STARTS with "a cup"
# is _LEAD_WORD's own business.
_ARTICLE = re.compile(r"^(?:of\s+)?(?:a|an|the)\s+", re.I)

# "a QUARTER OF an onion". The fraction is the quantity; the "a" in front of it
# is just English.
_WORD_FRACTIONS = {
    "half": Fraction(1, 2), "third": Fraction(1, 3), "quarter": Fraction(1, 4),
    "eighth": Fraction(1, 8),
}
_WORD_FRACTION = re.compile(
    r"^(" + "|".join(_WORD_FRACTIONS) + r")\b\s*", re.I)

# Amounts that are not amounts. There is no arithmetic to do on a pinch, and
# "2 pinches" is not what anybody means by doubling a pinch of salt.
_VAGUE = re.compile(r"^\s*(?:pinch|dash|drizzle|splash|handful|sprinkle|"
                    r"knob|glug|squeeze|touch)\b", re.I)


def line(text: str, factor: Fraction) -> tuple[str, bool]:
    """Scale one ingredient line. Returns (new_text, was_scaled)."""
    if factor == 1:
        return text, False

    m = _LEAD.match(text)
    if m:
        a = _to_fraction(m)
        b = _to_fraction(m, "b_") if m.group("b") else None
        if a is not None:
            tail = text[m.end() - len(m.group("rest")):]
            # Is what follows the number a unit, or a thing? "1 tbsp oil" is a
            # measurement; "1 onion" is a count, and they round differently.
            countable = not _UNITS.match(tail)
            out = render(a * factor, countable)
            if b is not None:
                out += "-" + render(b * factor, countable)
            return out + tail, True

    mw = _LEAD_WORD.match(text)
    if mw:
        word = mw.group(1).lower()
        rest = text[mw.end():]
        amount = Fraction(_WORDS[word])

        # "A QUARTER OF AN ONION" IS ONE QUANTITY, NOT THE NUMBER ONE.
        #
        # "a" and "an" are in the word table as 1, which is right for "a cup"
        # and wrong for "a quarter of". Without this, doubling "a quarter of an
        # onion" printed "2 quarter of an onion" -- the "a" was scaled and the
        # fraction it belonged to was left sitting in the text.
        frac = _WORD_FRACTION.match(rest) if word in ("a", "an") else None
        if frac:
            amount = _WORD_FRACTIONS[frac.group(1).lower()]
            rest = rest[frac.end():]
            word = frac.group(1).lower()

        # "HALF A CUP" IS ONE PHRASE, NOT A NUMBER FOLLOWED BY A WORD.
        #
        # A number word can be followed by an article that belongs to it: half
        # a cup, half the butter, a quarter of an onion. The article is part of
        # the quantity and has to be eaten with it. Leaving it behind printed
        # "1 a cup of sugar" -- and worse, the countability test below then ran
        # against "a cup of sugar", which does not start with a unit, so "half
        # a stick of butter" rounded to halves instead of eighths and came out
        # a third short with nothing on the page to show it.
        if word not in ("a", "an"):
            article = _ARTICLE.match(rest)
            if article:
                # "HALF THE BUTTER" IS A PROPORTION, AND PROPORTIONS DO NOT
                # SCALE. It means half of whatever butter this recipe already
                # calls for -- so in a doubled recipe it is still half of it,
                # and turning it into a number ("1 butter") is both wrong and
                # nonsense. "the" is what distinguishes it from "half a
                # butter", which is an amount.
                if article.group(0).strip().lower().endswith("the"):
                    return text, False
                rest = rest[article.end():]

        # "a" and "an" only count as one when a unit follows ("a cup of"),
        # never in "a pinch of salt" -- which has no number to scale and
        # should pass through exactly as written.
        if word in ("a", "an") and not _PRECISE_UNITS.match(rest):
            return text, False
        # ...and neither does "half a pinch of salt". Same reason, same test,
        # now that the article is out of the way.
        if _VAGUE.match(rest):
            return text, False
        scaled = render(amount * factor, not _UNITS.match(rest))
        return f"{scaled} {rest}", True

    return text, False


def ingredients(lines: list, factor: Fraction) -> list:
    """Scale a whole list. Each entry is {'text', 'scaled', 'original'}."""
    out = []
    for raw in lines:
        new, did = line(raw, factor)
        out.append({"text": new, "scaled": did, "original": raw})
    return out


# ---------------------------------------------------------------------------
#  How many does it currently serve?
# ---------------------------------------------------------------------------
_SERVES = re.compile(
    r"(?:serves|makes|yield[s]?|for)?\s*(\d+)(?:\s*(?:-|–|to)\s*(\d+))?"
    r"\s*(?:servings?|portions?|people|persons?|pieces?|slices?|cookies?|muffins?)?",
    re.I,
)


def servings_in(yields: str) -> int | None:
    """Pull a serving count out of '4 servings', 'Serves 4-6', 'Makes 12'.

    Returns the LOW end of a range on purpose. If it serves 4-6 and you ask
    for 4, you should get the recipe unchanged rather than two thirds of it.
    """
    if not yields:
        return None
    m = _SERVES.search(yields)
    if not m:
        return None
    try:
        n = int(m.group(1))
    except (TypeError, ValueError):
        return None
    return n if 0 < n <= 500 else None


def factor_for(yields: str, want: int) -> tuple[Fraction, int | None]:
    """(multiplier, what it serves now). Multiplier is 1 when we cannot tell."""
    have = servings_in(yields)
    if not have or not want or want <= 0:
        return Fraction(1), have
    return Fraction(want, have), have


# ---------------------------------------------------------------------------
#  Metric -> imperial. Jason's own request (2026-08-24): "can we convert
#  all metrics into imperial." Same reasoning as this file's own opening
#  docstring -- a gram is EXACTLY 1/28.3495 of an ounce, there is no
#  judgment call in that arithmetic, so a model never touches it.
#
#  WEIGHT (g/kg), NOT VOLUME (cups), FOR SOLIDS -- DELIBERATELY
#  -----------------------------------------------------------------
#  "225g flour" could become "1 3/4 cups flour", but only by ASSUMING a
#  density (flour packs differently bag to bag and cup to cup -- serious
#  baking sources measure it by weight for exactly this reason). Ounces
#  are a straight, exact weight-to-weight conversion with no ingredient-
#  specific guessing anywhere in it -- the same "never guess what code
#  can get exactly right" rule as everything else in this file. Volume
#  (ml/l) DOES convert to cups/tbsp/tsp, because that is still a straight
#  volume-to-volume conversion, no density involved either way.
#
#  THE ORIGINAL METRIC ALWAYS STAYS, IN PARENTHESES
#  -----------------------------------------------------
#  Every other estimate in this app keeps the number it was worked out
#  from next to the result (nutrition.py's own baseline, dedupe.py's own
#  merge reasons) so a wrong guess is checkable at a glance instead of
#  silently trusted. A conversion has no failure mode a person reading it
#  can't catch instantly here -- "8 oz (225g)" is either obviously right
#  or obviously not.
# ---------------------------------------------------------------------------
_G_PER_OZ = Fraction(283495, 10000)
_ML_PER_CUP = Fraction(2365882, 10000)
_ML_PER_TBSP = Fraction(147868, 10000)
_ML_PER_TSP = _ML_PER_TBSP / 3

_METRIC_WEIGHT = re.compile(r"^\s*(kilograms?|kilos?|kg|grams?|g)\b", re.I)
_METRIC_VOLUME = re.compile(r"^\s*(litres?|liters?|l|millilitres?|milliliters?|ml)\b", re.I)
_MID_WEIGHT = re.compile(r"\b(\d+(?:\.\d+)?)\s*(kilograms?|kilos?|kg|grams?|g)\b", re.I)
_MID_VOLUME = re.compile(r"\b(\d+(?:\.\d+)?)\s*(litres?|liters?|l|millilitres?|milliliters?|ml)\b", re.I)
# CAUGHT LIVE (2026-08-25), a real BBC Good Food recipe ("Easy classic
# lasagne"): "Heat oven to 200C/180C fan/gas 6" has no degree symbol and
# no word "degrees" -- the UK convention is a bare number directly
# against the C, which the first two alternatives never matched, so the
# whole line sailed through unconverted. Added a third alternative for
# exactly that shape, restricted to 100-300 (a real oven's actual range)
# so it can't misfire on something unrelated like "2C" as informal cup
# shorthand -- no oven is set to 2 degrees.
_TEMP_C = re.compile(
    r"(\d+)\s*°\s*C\b|(\d+)\s*deg(?:rees?)?\.?\s*C(?:elsius)?\b|\b([12]\d\d|300)C\b",
    re.I)

# CAUGHT LIVE (2026-08-25): re-running imperial_text() on a step that had
# ALREADY been converted once turned "390°F (200C)" into
# "390°F (390°F (200C))" -- the mid-string scanners have no memory of
# what they already did, so a second pass just found "200C" sitting
# there (now inside parens) and converted it AGAIN. This can genuinely
# happen -- reparse.reparse_one_recipe() can run on the same recipe more
# than once. The signal that a number is the ORIGINAL half of an
# existing conversion, not a fresh mention, is exactly what always
# precedes it in that shape: an imperial unit or a °F/°C reading,
# directly followed by "(". A fresh, never-converted "(400g)" (see the
# real "1 cup (71 g) packed light brown sugar" example) has no such
# lead-in, so it still converts normally.
#
# CAUGHT LIVE (2026-08-25), a second real case of the same root problem:
# "Turn on the oven and set the temperature to 400 degrees F (200
# degrees C)" -- a US recipe site's OWN native dual-unit text (common;
# they write both for an international audience), faithfully
# transcribed verbatim same as everything else. The guard above only
# recognized the SYMBOL form ("°F ("); this is the exact same situation
# in WORD form ("degrees F ("), so it slipped through and got converted
# a second time into "400 degrees F (390°F (200 degrees C))" -- two
# different Fahrenheit numbers for one oven. Added the word/abbreviated
# forms _TEMP_C itself already recognizes as valid readings, so anything
# that pattern can produce is also recognized here as already-paired.
_ALREADY_CONVERTED_LEAD = re.compile(
    r"(?:°[CF]|degrees?\s+[CF]\b|deg\.?\s+[CF]\b|"
    r"oz|lbs?|cups?|tbsp|tablespoons?|tsp|teaspoons?)\s*\(\s*$", re.I)


def _already_converted(text: str, pos: int) -> bool:
    return bool(_ALREADY_CONVERTED_LEAD.search(text[:pos]))


def _oz_text(grams: Fraction) -> str:
    oz = grams / _G_PER_OZ
    if oz >= 16:
        lb, rem_oz = divmod(oz, 16)
        rem = render(rem_oz)
        return f"{int(lb)} lb" + (f" {rem} oz" if rem != "0" else "")
    return f"{render(oz)} oz"


def _cup_text(ml: Fraction) -> str:
    cups = ml / _ML_PER_CUP
    if cups >= Fraction(1, 4):
        # Pluralized off the RENDERED (snapped-to-eighths) string, not
        # the raw fraction -- 0.97 cups snaps to "1", and checking the
        # unrounded value first would print "1 cups".
        rendered = render(cups)
        return f"{rendered} cup" + ("" if rendered == "1" else "s")
    tbsp = ml / _ML_PER_TBSP
    if tbsp >= 1:
        return f"{render(tbsp)} tbsp"
    return f"{render(ml / _ML_PER_TSP)} tsp"


def _f_from_c(celsius: int) -> int:
    # Snapped to the nearest 5°F -- an oven dial turns in 5s/25s, not
    # single degrees; "356°F" is false precision nobody's oven has.
    return int(5 * round((celsius * 9 / 5 + 32) / 5))


def imperial_line(text: str) -> tuple[str, bool]:
    """One ingredient LINE, metric -> imperial -- anchored at the line's
    own LEADING quantity, so it reuses _LEAD/_to_fraction and gets vulgar
    fractions ("½kg") and word numbers ("a litre") for free, same as
    line()'s own scaling. Returns (new_text, changed)."""
    m = _LEAD.match(text)
    if not m or m.group("b"):  # a range ("200-300g") -- too ambiguous to convert cleanly, leave it
        return text, False
    a = _to_fraction(m)
    if a is None:
        return text, False
    num_end = m.end() - len(m.group("rest"))
    tail = text[num_end:]
    wmatch = _METRIC_WEIGHT.match(tail)
    vmatch = None if wmatch else _METRIC_VOLUME.match(tail)
    unit_match = wmatch or vmatch
    if not unit_match:
        return text, False
    original = text[:num_end + unit_match.end()].strip()
    rest = text[num_end + unit_match.end():]
    new_amount = _oz_text(a) if wmatch else _cup_text(a * 1000 if unit_match.group(1).lower().startswith("l")
                                                       else a)
    return f"{new_amount} ({original}){rest}", True


def imperial_text(text: str) -> tuple[str, bool]:
    """Any metric weight/volume/temperature mentioned ANYWHERE in a
    string -- a step's own prose, not just an ingredient line's leading
    quantity. Plain digit amounts only (no vulgar fractions or word
    numbers mid-sentence) -- steps write metric amounts as plain numbers
    in practice ("add 500ml stock"); a line that genuinely needs fraction
    parsing goes through imperial_line() instead."""
    changed = [False]

    def _weight(m):
        if _already_converted(text, m.start()):
            return m.group(0)
        changed[0] = True
        amount = Fraction(m.group(1))
        grams = amount * 1000 if m.group(2).lower().startswith("k") else amount
        return f"{_oz_text(grams)} ({m.group(0)})"

    def _volume(m):
        if _already_converted(text, m.start()):
            return m.group(0)
        changed[0] = True
        amount = Fraction(m.group(1))
        ml = amount * 1000 if m.group(2).lower().startswith("l") else amount
        return f"{_cup_text(ml)} ({m.group(0)})"

    def _temp(m):
        if _already_converted(text, m.start()):
            return m.group(0)
        changed[0] = True
        c = int(m.group(1) or m.group(2) or m.group(3))
        return f"{_f_from_c(c)}°F ({m.group(0)})"

    text = _TEMP_C.sub(_temp, text)
    text = _MID_WEIGHT.sub(_weight, text)
    text = _MID_VOLUME.sub(_volume, text)
    return text, changed[0]


def imperial_ingredients(lines: list) -> list:
    """A whole ingredient list, metric -> imperial. Returns plain strings
    -- unlike ingredients() above, this is a one-time content change at
    import/reparse time, not a live per-view scaling, so there is no
    {'text','scaled','original'} shape to preserve here."""
    return [imperial_line(ln)[0] for ln in lines]


def imperial_steps(lines: list) -> list:
    """A whole step list, metric -> imperial, using the mid-string
    scanner (a step's own metric mention is rarely at the very start of
    the sentence the way an ingredient line's quantity is)."""
    return [imperial_text(ln)[0] for ln in lines]


def flags(scaled_lines: list) -> list:
    """Warnings worth putting on the page. Deliberately few.

    A page that warns about everything gets scrolled past, and then it warns
    about nothing. Only two things here are worth interrupting someone for.
    """
    out = []
    for item in scaled_lines:
        text = item["text"]
        if re.search(r"\b\d+\s+\d/\d\s+(eggs?|yolks?|whites?)\b", text, re.I) or \
           re.search(r"^\s*\d?\s*\d/\d\s+(eggs?|yolks?|whites?)\b", text, re.I):
            out.append(f"“{text}” — you cannot measure part of an "
                       "egg without beating one and weighing it. Round it, or "
                       "pick a serving count that divides evenly.")
    return out
