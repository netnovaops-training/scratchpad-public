"""Americanizing UK recipe vocabulary -- Jason's own request (2026-08-24,
about recipe 28, a BBC Good Food lasagne): "there are some ingredients my
mom would not know, it's weird." Confirmed against that exact recipe:
"rashers smoked streaky bacon", "clear honey", "celery stick" -- all real
British terms for things his mom would recognize immediately under their
American name, sitting unrecognizable under a name she's never shopped
for. Distinct from tonight's earlier metric/imperial fix (a UNIT problem,
already solved in scale.py) -- this is a NAMING problem.

MECHANICAL, NOT A MODEL CALL, SAME REASONING AS clean_name()
--------------------------------------------------------------
A fixed word list is auditable: every entry here is checkable at a
glance, and nothing gets added that isn't a genuine 1:1 renaming of the
exact same product under a different word. This is deliberately a much
SHORTER list than it could be, because most UK/US food-vocabulary pairs
are NOT 1:1 -- they are either genuinely ambiguous ("grill" means broil
in the UK and outdoor charcoal grilling in the US -- the single most
dangerous word to get this wrong on, so it is not here) or a real
product substitution rather than a naming difference ("digestive
biscuit" -> "graham cracker" changes the flavor and texture, it doesn't
just rename the same thing -- that is subs.py's job, not this module's).
When in doubt, a term is left OUT of this list -- same standing rule as
foodmatch.py's own "and"/"or"/"extra" removals: a word that stays
foreign is honest; a word that gets silently swapped for something
subtly wrong is not, and there is no way to fact-check a swap that
never happened.

Applied in TWO places, same split as scale.py's imperial_line() (food
identity) vs imperial_text() (prose): americanize_name() for a food's
own NAME (import time, alongside foodmatch.clean_name()), and
americanize_text() for a STEP's own prose, where the same word can show
up mid-sentence ("Heat the oil... snip the bacon...").
"""
import re

# UK phrase -> US phrase. Longest phrases first within each category so a
# multi-word entry (e.g. "self-raising flour") is checked before anything
# that could partially overlap it. Every entry here is a straight rename
# of the SAME product -- nothing that changes flavor, texture, or what a
# shopper would actually buy (see module docstring for the calibration).
_TERMS = [
    ("self-raising flour", "self-rising flour"),
    ("self raising flour", "self-rising flour"),
    ("plain flour", "all-purpose flour"),
    ("cornflour", "cornstarch"),
    ("caster sugar", "superfine sugar"),
    ("icing sugar", "powdered sugar"),
    ("double cream", "heavy cream"),
    ("single cream", "light cream"),
    ("soured cream", "sour cream"),
    ("wholemeal", "whole wheat"),
    ("courgettes", "zucchini"),
    ("courgette", "zucchini"),
    ("aubergines", "eggplants"),
    ("aubergine", "eggplant"),
    ("swede", "rutabaga"),
    ("spring onions", "scallions"),
    ("spring onion", "scallion"),
    ("rocket leaves", "arugula"),
    ("rocket", "arugula"),
    ("mangetout", "snow peas"),
    ("mange tout", "snow peas"),
    ("beetroot", "beets"),
    ("cos lettuce", "romaine lettuce"),
    ("clear honey", "honey"),
    ("smoked streaky bacon", "smoked bacon"),
    ("streaky bacon", "bacon"),
    ("rashers of bacon", "slices of bacon"),
    ("bacon rashers", "bacon slices"),
    ("rashers", "slices"),
    ("celery sticks", "celery stalks"),
    ("celery stick", "celery stalk"),
    ("stock cubes", "bouillon cubes"),
    ("stock cube", "bouillon cube"),
    ("bicarbonate of soda", "baking soda"),
    ("yoghurt", "yogurt"),
    ("clingfilm", "plastic wrap"),
    ("cling film", "plastic wrap"),
    ("greaseproof paper", "parchment paper"),
    ("kitchen roll", "paper towels"),
    ("tea towel", "dish towel"),
    ("muslin", "cheesecloth"),
]

# Sanity check at import time, not at call time -- a duplicate or
# out-of-order entry above would silently shadow another one every time
# this module is used; failing once at startup catches it immediately
# instead of leaving a live substitution quietly wrong.
#
# CAUGHT LIVE writing this check itself, twice: get the direction wrong
# and it either never fires or fires on the CORRECT order. The actual
# failure mode: if a SHORTER phrase is listed BEFORE a LONGER one that
# contains it ("streaky bacon" before "smoked streaky bacon"), the
# shorter one's .sub() pass runs first and eats its half of every
# occurrence, so the longer, more specific pattern never gets a whole
# match to find by the time its own turn comes. So: for every pair
# (earlier, later) in list order, `earlier` must NOT be a substring of
# `later` -- if it is, `later` needed to be listed first instead.
assert len(_TERMS) == len({t[0] for t in _TERMS}), "duplicate UK phrase in _TERMS"
for _i, (_earlier, _) in enumerate(_TERMS):
    for _later, _ in _TERMS[_i + 1:]:
        assert _earlier not in _later, \
            f"{_earlier!r} (listed first) is a substring of {_later!r} (listed after) -- list {_later!r} first"

_PATTERNS = [(re.compile(r"(?<!\w)" + re.escape(uk) + r"(?!\w)", re.I), us)
            for uk, us in _TERMS]


def _replace_casing(match: "re.Match", us: str) -> str:
    """Keep the substitution readable in context: capitalized if the
    matched UK phrase started a sentence or was itself capitalized,
    lowercase otherwise -- never shouting-case just because the source
    happened to write it that way somewhere mid-sentence."""
    original = match.group(0)
    if original[:1].isupper():
        return us[:1].upper() + us[1:]
    return us


def americanize_text(text: str) -> tuple[str, bool]:
    """Any of the known UK terms mentioned anywhere in a string -- a
    step's own prose. Returns (new_text, changed)."""
    changed = [False]

    def _sub(pattern, us):
        def _do(m):
            changed[0] = True
            return _replace_casing(m, us)
        return _do

    for pattern, us in _PATTERNS:
        text = pattern.sub(_sub(pattern, us), text)
    return text, changed[0]


def americanize_name(name: str) -> str:
    """A food's own identity name -- same substitutions, casing handling
    included, but returns just the string (no separate changed flag --
    every caller here already checks name != result to detect a change,
    same convention as foodmatch.clean_name())."""
    new_text, _ = americanize_text(name or "")
    return new_text


def americanize_ingredients(lines: list) -> list:
    return [americanize_text(ln)[0] for ln in lines]


def americanize_steps(lines: list) -> list:
    return [americanize_text(ln)[0] for ln in lines]
