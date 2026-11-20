"""Making terse recipe steps followable, without inventing anything.
Jason's own request (2026-08-24): "these are two simple to follow and
there's a lot of interpretation which means more mistakes... I don't
need a detailed instructions about how to add eight cups of water if
it's one step. but if something requires more steps from the original
recipe, I want to have more details about that using contacts from the
original recipe, but keeping simple what needs to be simple and giving
more details where it needs to be." Also, explicitly, for two downstream
readers of this same text: "I need more details for my llms to draw
pictures for also for help for drawing conclusions for cooking" --
diagram.py's own art.draw() and anything reasoning about a step both
read the SAME text this module produces.

WHY THIS IS A SEPARATE PASS FROM importer.READ_SYSTEM, NOT A CHANGE TO IT
---------------------------------------------------------------------------
READ_SYSTEM's own rule is "Copy ingredients and steps VERBATIM... you are
transcribing, not improving" -- deliberately, so a faithful record of
what the source actually said always exists before anything touches it.
This module runs AFTER that transcription and does the improving, with
its own much narrower rule: expand HOW to do something already stated,
never WHAT is being done -- no new quantity, time, temperature or
ingredient that was not already in the verbatim text. Same anti-
hallucination discipline as resolve_ingredients()/dedupe.py's own model
calls elsewhere in this app: a model being confident is not the same as
a model being right, so the rule is enforced in the prompt, not assumed.

WHY UNIT CONVERSION IS NOT PART OF THIS MODEL CALL
------------------------------------------------------
Same reasoning as scale.py's own opening docstring: "do it in code if
code can be exact, and only then ask the model." A gram is always
1/28.3495 of an ounce; there is no judgment call in that arithmetic, so
it is not this module's job -- see scale.imperial_ingredients/
imperial_steps, pure code, called separately (main.py's callers run both
passes, this one first).
"""
import re

from . import core, llm, vram

# CAUGHT LIVE (2026-08-24), the very first real test of this module: asked
# to add technique to "Roll out and cut into 8 rounds", the model added
# "to about 1cm thickness" -- a completely normal-sounding thing for a
# recipe to say, and a fact this recipe never actually stated. The prompt
# above was tightened after finding this, but a prompt is a request, not
# a guarantee (same lesson dedupe.py's own Tier-2 already learned about
# LLM identity judgments) -- so this is a second, CODE-level backstop:
# any number+unit combination in an expanded step that doesn't already
# appear anywhere in the recipe's own title/ingredients/steps gets that
# expansion thrown out, falling back to the verbatim original for that
# one step. Bare digits with no unit letters are exempt -- too common a
# false-positive source (the "8" in "8 rounds" reused honestly elsewhere)
# to be worth flagging on their own.
_NUM_TOKEN = re.compile(r"\d+(?:\.\d+)?\s*(?:°\s*[cf]\b|[a-zA-Z]+)?")


def _number_tokens(text: str) -> set:
    return {re.sub(r"\s+", "", m.group(0).lower()) for m in _NUM_TOKEN.finditer(text or "")}


def _introduces_new_number(known: set, candidate: str) -> bool:
    for tok in _number_tokens(candidate):
        if re.fullmatch(r"\d+(?:\.\d+)?", tok):  # a bare number, no unit -- too ambiguous to police
            continue
        if tok not in known:
            return True
    return False

SYSTEM = """You make a recipe's own steps easier to actually follow,
without changing what the recipe says to do.

THE ONE RULE THAT MATTERS: expand HOW to do something already stated,
never WHAT is being done. NEVER ADD A NUMBER THAT ISN'T ALREADY IN THIS
RECIPE -- not a quantity, not a time, not a temperature, not a size or
thickness or distance, not a count. This is the single most common
mistake to make here: describing HOW to roll out dough is expanding the
method; giving it a thickness ("to about 1cm", "a quarter inch thick")
that the original never stated is INVENTING A FACT, even though it
sounds like a completely normal thing a recipe would say. If a number
isn't already written down somewhere in this recipe, it does not appear
in your answer either, however standard or obvious it seems.

WHEN TO LEAVE A STEP ALONE: if it is already a single, clear action
("add 8 cups of water", "stir in the cheese", "preheat the oven to
350F"), return it unchanged, or nearly so. A short step is not a
problem to fix.

WHEN TO EXPAND A STEP: if it bundles more than one real action together,
or names a technique without saying how to do it or what it should look
like when it's right, add that -- grounded in standard, well-known
cooking practice for exactly that action, described concretely enough
that someone who has never made this could follow it, and specific
enough that another reader could picture or draw the scene (what it
looks like, the motion, the texture or color that means it's done).
Sizes, thicknesses, distances and counts stay OUT of that description
even when expanding -- describe the METHOD ("roll it out evenly, giving
it a bit of thickness so the rounds hold together") never a MEASUREMENT
that was never given.

Examples, exact:
- "Add 8 cups of water." -> unchanged. One action, already clear.
- "Rub the butter into the flour until it looks like breadcrumbs." ->
  may add HOW ("using your fingertips, working quickly so the butter
  stays cold") -- the visual cue is already there, so add technique,
  not more description of the result.
- "Roll out and cut into 8 rounds." -> two actions with no technique at
  all: "Roll the dough out on a lightly floured surface until it's an
  even thickness, then cut into 8 rounds using a cutter or the rim of a
  glass, flouring it between cuts so it doesn't stick." NOT "roll to
  1cm/a quarter inch" -- no thickness was given, so none appears.
- "Cook until done." -> too vague to expand safely without inventing a
  time -- return it close to unchanged, or add only a generic, widely-
  true doneness cue if the ingredient makes one obvious (e.g. chicken:
  "until no longer pink in the center"), never a specific number.

Return JSON: {"steps": [{"step": "<original step, verbatim>",
"expanded": "<the step, possibly rewritten>"}, ...]}, one entry per
step given, SAME ORDER, "step" copied EXACTLY so the caller can match
them back regardless of order."""


async def elaborate(title: str, ingredients: list, steps: list) -> list:
    """steps -> the same list, expanded where it earns it. Never raises
    -- a failed elaboration just means the verbatim steps stand, same
    as every other best-effort model call in this app (resolve_ingredients,
    labels.suggest, extras.suggest)."""
    steps = [s for s in (steps or []) if (s or "").strip()]
    if not steps:
        return steps
    user = (f"Recipe: {title}\n\nIngredients:\n" + "\n".join(ingredients or []) +
           f"\n\nSteps:\n" + "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1)))
    try:
        async with vram.parsing():
            data = await llm.chat_json(SYSTEM, user, model=core.LLM_READ,
                                       temperature=0.2, max_tokens=3000)
    except llm.LLMError:
        return steps
    rows = data.get("steps") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        return steps
    known_numbers = _number_tokens(
        title + " " + " ".join(ingredients or []) + " " + " ".join(steps))
    by_original = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        orig = (row.get("step") or "").strip()
        expanded = (row.get("expanded") or "").strip()
        if orig and expanded:
            by_original[orig.lower()] = expanded
    out = []
    for i, s in enumerate(steps):
        hit = by_original.get(s.strip().lower())
        # Position fallback, same trick tandoor.resolve_ingredients() uses
        # -- a model reliably preserves ORDER even when it paraphrases the
        # echoed text enough that an exact match misses.
        if not hit and i < len(rows) and isinstance(rows[i], dict):
            hit = (rows[i].get("expanded") or "").strip()
        if hit and _introduces_new_number(known_numbers, hit):
            hit = None  # see _introduces_new_number's own docstring
        out.append(hit or s)
    return out
