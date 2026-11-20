"""Strip junk punctuation picked up from scraped recipe sites --
trademark symbols on branded ingredients (McCormick(R), Zatarain's(R)),
the {alt ingredient} bracket notation some sites use for swaps (turned
into a plain "(or alt ingredient)"), stray trailing periods left on a
lone word, a metric/imperial duplicate on one site's own unit-toggle
widget ("1.5kg / 3lb" -> "3 lb"), a leading comma left over from a
parenthetical the source wrote awkwardly ("(, about 5 ounces each)"),
doubled-up parentheses from a nested aside collapsing onto itself
("((ripe and juicy!))"), and a source site's own footnote reference
("(Note 3)"), which points at nothing once the page it came from is
gone. Deliberately leaves real punctuation alone: commas in a natural
adjective list ("skin-on, bone-in chicken thighs"), parentheses that
carry real meaning ("(dried)", "(gochujang)"), and a genuine name
alternative ("caster / superfine sugar", "cornflour / cornstarch") --
that one is not a unit, so it is never touched.

WHY IMPERIAL WINS OVER METRIC, NOT THE OTHER WAY AROUND
Every other measurement already in this household's library is
imperial (cups, tbsp, tsp, lb, oz) -- keeping whichever side of a
site's metric/imperial toggle is already imperial keeps a recipe
consistent with itself, and is also the more useful default for
American measuring cups and spoons.

CAUGHT LIVE, AND WHY THIS MATTERS MORE THAN IT LOOKS
This is not just cosmetic. "1.5kg / 3lb (8) yellow peaches" is exactly
the shape of ingredient line Mealie's own parser gets confused by
-- checked directly against a real recipe: it turned "1 tsp cornflour"
into a completely different unit ("1 Tbsps") on the very same import.
A parser is far more likely to read an ingredient line correctly when
it is only ever given ONE number and ONE unit to begin with.
"""

import re

_TRADEMARK_RE = re.compile(r"[®™©]")  # (R) (TM) (C)
_BRACE_RE = re.compile(r"\s*\{([^{}]+)\}")
_TRAILING_DOT_RE = re.compile(r"(?<=[a-zA-Z])\.\s*$")
_WS_RE = re.compile(r"[ \t]{2,}")
_SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+([,;])")
_LEADING_COMMA_PAREN_RE = re.compile(r"\(\s*,\s*")
# Only a TRUE double-wrap -- "((text))" with nothing else nested inside --
# collapses to "(text)". Deliberately does NOT match something shaped like
# "((ripe and juicy!), peeled, cut into wedges)": that outer paren legally
# wraps more than just the inner one, so blindly merging "((" would strip
# an open-paren the text still needs to balance against its own final
# close, leaving a dangling ")" behind. Requiring no further "(" or ")"
# between the two pairs is what tells the two shapes apart.
_DOUBLE_WRAP_RE = re.compile(r"\(\(([^()]*)\)\)")
_DOUBLE_COMMA_RE = re.compile(r",\s*,")
# What a footnote strip inside a double-wrap leaves behind --
# "((Note 3))" loses its inner "(Note 3)" to _FOOTNOTE_REF_RE and would
# otherwise leave the empty outer pair sitting there on its own.
_EMPTY_PAREN_RE = re.compile(r"\s*\(\s*\)")
_FOOTNOTE_REF_RE = re.compile(r"\s*\(\s*note\s+\d+\s*\)", re.I)
# A markdown bullet marker left on the front of a line -- crawl.py's own
# steps-mining backfill (fill_ingredients_from_steps) sometimes hands
# back an added ingredient still wearing the "- " it was written with
# in the model's own list, since nothing downstream expected one.
_LEADING_BULLET_RE = re.compile(r"^[-*]\s+")

_NUM = r"[\d]+(?:\.[\d]+)?"
_METRIC_UNIT = r"(?:kg|mg|ml|g)"
_IMPERIAL_UNIT = r"(?:lbs?|oz|tbsps?|tsps?|cups?)"
# A site's own metric/imperial toggle, joined into one string by the
# scrape ("1.5kg / 3lb", "85g / 6 tbsp") -- either order, since different
# sites lead with a different system. NOT the same shape as a genuine
# name alternative ("caster / superfine sugar"): both sides here start
# with a real number, which "caster" and "cornflour" never do.
_METRIC_THEN_IMPERIAL_RE = re.compile(
    rf"^({_NUM})\s*({_METRIC_UNIT})\s*/\s*({_NUM})\s*({_IMPERIAL_UNIT})\b", re.I)
_IMPERIAL_THEN_METRIC_RE = re.compile(
    rf"^({_NUM})\s*({_IMPERIAL_UNIT})\s*/\s*({_NUM})\s*({_METRIC_UNIT})\b", re.I)


def _drop_metric_duplicate(text: str) -> str:
    m = _METRIC_THEN_IMPERIAL_RE.match(text)
    if m:
        return text[:m.start()] + f"{m.group(3)} {m.group(4)}" + text[m.end():]
    m = _IMPERIAL_THEN_METRIC_RE.match(text)
    if m:
        return text[:m.start()] + f"{m.group(1)} {m.group(2)}" + text[m.end():]
    return text


def clean(text: str) -> str:
    if not text:
        return text
    out = text
    out = _LEADING_BULLET_RE.sub("", out)
    out = _TRADEMARK_RE.sub("", out)
    out = _BRACE_RE.sub(lambda m: f" (or {m.group(1).strip()})", out)
    out = _TRAILING_DOT_RE.sub("", out)
    out = _drop_metric_duplicate(out)
    out = _FOOTNOTE_REF_RE.sub("", out)
    out = _LEADING_COMMA_PAREN_RE.sub("(", out)
    # Repeated, not once: "(((deep))) " collapses one layer per pass,
    # same reasoning a single pass would miss anything nested more than
    # two deep (rare, but cheap to handle rather than assume away).
    while True:
        collapsed = _DOUBLE_WRAP_RE.sub(r"(\1)", out)
        if collapsed == out:
            break
        out = collapsed
    out = _DOUBLE_COMMA_RE.sub(",", out)
    out = _EMPTY_PAREN_RE.sub("", out)
    out = _SPACE_BEFORE_PUNCT_RE.sub(r"\1", out)
    out = _WS_RE.sub(" ", out)
    return out.strip()
