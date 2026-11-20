"""Things that might go well with a recipe -- an extra protein, a spice, a
side -- that nobody wrote into it.

WHY THIS IS THE MODEL AND NOT A TABLE
--------------------------------------
subs.py answers "what else is this ingredient" from a table first, because
that question has one right answer that does not change. This is a different
question: "what would suit THIS dish" depends on the dish, and there is no
table of every recipe to key it against. Same territory as ideas.py, and it
uses the same job for the same reason -- it is worked out, not looked up.

WHY EVERY SUGGESTION SAYS WHERE IT CAME FROM, EVERY TIME
----------------------------------------------------------
subs.py appends its allergy warning in CODE, after the model has spoken, so a
bad sample or an instruction hidden in a scraped page cannot make it
disappear. This does the same thing for a plainer reason: an added spice or
extra protein is not what the recipe was written with, has not been cooked by
anyone in this kitchen, and the person reading it should be told that in the
same breath every single time -- not only when the model happens to remember.
DISCLAIMER below is rendered once by the template, not asked of the model and
not something a suggestion can arrive without.

WHY THE PARSING BELOW LOOKS PARANOID
-------------------------------------
It earned it. Asked, on the SAME recipe, to return
{"suggestions": [{"kind": "...", "add": "...", "why": "..."}]}, this model
(qwen3:8b, the `chat` job -- see the long note in core.py about it being a
thinking model that sometimes returns nothing at all) has been measured
answering every one of these, across different calls:

    * the right shape                       {"suggestions": [{"kind":.., "add":..}]}
    * the right list, wrong container key    {"additions": [...]}
    * the right items, wrong field names     [{"type":.., "value":..}]
    * items that are plain strings           {"suggestions": ["Spice: Paprika", ...]}
    * the list buried a level deeper         {"recipe": {"suggestions": [...]}}
    * a different task altogether            {"original":.., "corrections":..}

The first two rows are what a "salvage" pass -- the same kind llm.py already
does one level up for malformed JSON -- can fix. The rest is this file. What
does NOT change, however the model phrases it, is that a suggestion is one
piece of text worth showing someone: so the parsing below hunts through
whatever shape came back for something recognisable as that, and gives up
quietly rather than crash on the one nobody has seen yet.

WHEN IT RUNS
------------
Once per recipe, the first time anyone opens its guide -- background-filled
the same way labels are (see _extras_later in main.py), so the guide itself
never waits on the model. [] is cached as a real answer ("nothing worth
adding"), not as "not tried yet" -- otherwise a recipe the model has nothing
to say about would ask it again on every single view.
"""
import re

from . import core, llm

# Prefixed with "AI suggested —" wherever it is shown (see guide.html), so
# this half reads on from that rather than repeating it.
DISCLAIMER = ("not tested in this kitchen. Treat it like an idea from a "
             "friend who has not cooked this dish either — use your own "
             "judgement, at your own risk.")

_KINDS = {"protein", "spice", "herb", "aromatic", "side"}

# Near-misses actually seen: "side_dish" for "side". Close enough to fold in
# rather than let a real, useful category fall through to the generic
# "idea" label just because the model added a word.
_KIND_ALIASES = {"side_dish": "side", "sidedish": "side", "side dish": "side",
                 "sides": "side", "veg": "side", "vegetable": "side",
                 "spices": "spice", "herbs": "herb", "proteins": "protein",
                 "aromatics": "aromatic"}

SYSTEM = """You suggest small additions to a recipe someone is about to cook --
not changes to what is already there. You are talking to someone standing in
their kitchen with the recipe in front of them, deciding what to add.

Rules:
- Suggest at most four things: an extra protein, a spice or herb, an
  aromatic, or a side dish -- only ones that actually fit this dish. Two good
  suggestions beat four weak ones; if nothing genuinely fits, say none.
- Each one is a short phrase for what to add, and one short sentence for why
  it fits or what it would change.
- It must fit the dish AS WRITTEN -- its cuisine, its method, what is already
  in it. A suggestion that fights the rest of the ingredients is worse than
  no suggestion.
- Real, ordinary additions a home cook would have or could easily get. Not a
  restaurant-plating flourish, not a rare ingredient.
- No preamble, no sign-off, no mention of being an AI, and say NOTHING about
  the recipe itself -- not its ingredients, not its method, not a corrected
  version of it. Only the additions."""

# A FILLED-IN EXAMPLE, not a schema description. "kind: protein|spice|..." as
# a placeholder is an abstraction the small model here drifts away from --
# wrapping the list under a different key, renaming "add" to whatever it
# feels like, sometimes answering an entirely different question. One
# concrete example of the exact shape wanted, with a real suggestion in it,
# anchors it far better than a description of the shape does.
_SHAPE = """
Return exactly this JSON shape and no other keys, with your own suggestions
in place of the example:
{"suggestions": [
  {"kind": "spice", "add": "smoked paprika", "why": "adds a smoky sweetness that suits a braised dish"}
]}"""


async def suggest(recipe: dict) -> list:
    """[{kind, add, why}], newest-worthwhile first. [] if nothing fits, or the
    model is unavailable -- both are quiet, not errors a page needs to show."""
    title = (recipe.get("title") or "").strip() or "this dish"
    ingredients = [ln if isinstance(ln, str) else ln.get("text", "")
                  for ln in (recipe.get("ingredients") or [])][:25]

    ask = f"Recipe: {title}\n"
    if recipe.get("cuisine"):
        ask += f"Cuisine: {recipe['cuisine']}\n"
    if ingredients:
        ask += "Ingredients as written:\n" + "\n".join(f"- {i}" for i in ingredients)

    try:
        data = await llm.chat_json(SYSTEM + _SHAPE, ask, model=core.LLM_TALK,
                                   temperature=0.3, max_tokens=700)
    except llm.LLMError:
        return []

    if not isinstance(data, dict):
        return []

    # Filtered FIRST, capped SECOND. Capping before filtering means one junk
    # entry ahead of three good ones throws a good suggestion away for
    # nothing -- the cap is "at most four suggestions", not "look at the
    # model's first four attempts and take whatever survives".
    out = []
    for item in _find_list(data):
        parsed = _as_suggestion(item)
        if not parsed:
            continue
        out.append(parsed)
        if len(out) == 4:
            break
    return out


# ---------------------------------------------------------------------------
#  Finding the list, wherever it ended up
# ---------------------------------------------------------------------------
_LIST_KEYS = ("suggestions", "additions", "extras", "ideas", "items")


def _find_list(data: dict, depth: int = 0) -> list:
    """The list of suggestions, searched for by name first, then by shape,
    then one level deeper -- because it has been seen at each of those.

    Depth-limited to 2: the model has been seen nesting one level
    ({"recipe": {"suggestions": [...]}}), never two, and an unbounded search
    through an adversarial or just deeply-confused reply is a shape of
    problem this app does not need to invite.
    """
    for k in _LIST_KEYS:
        v = data.get(k)
        if isinstance(v, list):
            return v
    # A list of dicts (each presumably a suggestion) or of plain strings
    # ("Spice: Paprika") anywhere in this object, name notwithstanding.
    for v in data.values():
        if isinstance(v, list) and v and isinstance(v[0], (dict, str)):
            return v
    if depth < 2:
        for v in data.values():
            if isinstance(v, dict):
                found = _find_list(v, depth + 1)
                if found:
                    return found
    return []


# ---------------------------------------------------------------------------
#  Reading one suggestion, whatever shape it came in
# ---------------------------------------------------------------------------
# THE FIELD NAMES ARE NOT AS RELIABLE AS THE PROMPT ASKING FOR THEM -- see the
# module docstring. So each field is read from whichever of its likely names
# is present, and if the text field still is not, from whatever OTHER string
# is left in the item once the recognised keys are ruled out, because the one
# thing every version of this model's answer has agreed on is that there IS a
# piece of text worth showing, even when it will not commit to what to call
# it or where to put it.
_ADD_KEYS = ("add", "item", "value", "suggestion", "what", "name",
            "addition", "ingredient")
_KIND_KEYS = ("kind", "type", "category")
_WHY_KEYS = ("why", "reason", "note", "notes", "because", "description")

# "Spice: Paprika" -- the plain-string form. The word before the colon is
# usually one of the kinds, so it is worth a look before falling back to the
# whole string as the addition.
_LABELLED = re.compile(r"^\s*([a-z ]{2,15}?)\s*:\s*(.+)$", re.I)


def _normalize_kind(raw: str) -> str:
    """Whatever the model called it, folded onto the five kinds this app
    knows, or "idea" if it really is something else."""
    raw = (raw or "").strip().lower()
    raw = _KIND_ALIASES.get(raw, raw)
    if raw in _KINDS:
        return raw
    # One more try: the plural of a real kind ("aromatics"), stripped of just
    # the trailing 's'. Checked LAST and only against the closed set, so this
    # can never turn an unrelated word ending in 's' into a kind it is not.
    if raw.endswith("s") and raw[:-1] in _KINDS:
        return raw[:-1]
    return "idea"


def _as_suggestion(item):
    """One item, in whatever shape it arrived, or None if there is nothing
    in it worth showing."""
    if isinstance(item, dict):
        add = _pick(item, _ADD_KEYS)
        kind = _normalize_kind(_pick(item, _KIND_KEYS))
        why = _pick(item, _WHY_KEYS) or ""
    elif isinstance(item, str):
        text = item.strip()
        m = _LABELLED.match(text)
        # "Spice: Paprika" -- the label before the colon is worth reading as
        # the kind only when it actually is one; otherwise it is very likely
        # part of the suggestion itself ("Note: goes well with rice"), and
        # the whole string is kept rather than silently losing half of it.
        if m and _normalize_kind(m.group(1)) != "idea":
            kind, add, why = _normalize_kind(m.group(1)), m.group(2), ""
        else:
            kind, add, why = "idea", text, ""
    else:
        return None

    add = add.strip() if add else ""
    if not add:
        return None
    return {"kind": kind, "add": add[:120], "why": why.strip()[:220]}


def _pick(item: dict, keys: tuple) -> str:
    for k in keys:
        v = item.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    if keys is _ADD_KEYS:
        seen = _ADD_KEYS + _KIND_KEYS + _WHY_KEYS
        for k, v in item.items():
            if k not in seen and isinstance(v, str) and v.strip():
                return v.strip()
    return ""
