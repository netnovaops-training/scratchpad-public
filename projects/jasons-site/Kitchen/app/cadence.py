#!/usr/bin/env python3
"""Say a recipe step the way Nellie says things.

Jason, 2026-09-07: "she's 93 and forgetful so it needs to be more since that
she pauses more and forgets something along the way and resumes where she was
talking about."

This is a TEXT transform, not an audio one, and that is the whole trick. Pace
and pitch are already handled in the voice server -- atempo slows her without
making her sound drugged. What no audio setting can produce is the SHAPE of
how somebody talks: stopping mid-thought, going back over a number, picking
the thread up again. That has to be written into the words before the model
ever sees them.

THE ONE RULE THAT CANNOT BEND: A HESITATION MAY REPEAT, NEVER OMIT.

Somebody is cooking from this with their hands in a bowl. If she "forgets" a
quantity and moves on, the recipe is now wrong and sounds charming while being
wrong, which is the worst combination available. So every device here either
restates information or pauses around it. Nothing is ever dropped, reordered,
or replaced with a vaguer word. Rendering her cadence must never cost the
person the recipe.

WHY IT IS DETERMINISTIC, and not a language model. Asking a model to "say this
like a forgetful 93-year-old" would produce something different every render,
would need re-checking every time, and would eventually invent an ingredient.
A fixed set of devices, chosen by position, gives the same output for the same
step forever -- which also means the cached audio stays correct.
"""
import re

# Blank lines are what the voice server turns into real silence, so pauses are
# expressed as structure rather than as punctuation the model may ignore.
BREAK = "\n\n"

# Small spoken hesitations. Deliberately plain: "um" and "er" transcribe badly
# and tend to come out as words, and a caricature of an old woman is not the
# same thing as an old woman.
_PICKUPS = [
    "Now then.",
    "Where was I.",
    "Let me see.",
    "Oh, and.",
    "That's it.",
]

# --- when she wanders --------------------------------------------------------
# Jason: "can Nellie start rambling and talking about random trivia about the
# 30s and facts about how things were ... I want her to go off topic on
# unimportant steps but the important ones I don't want mess the recipe up."
#
# TWO RULES, AND THE SECOND ONE IS ABOUT HER RATHER THAN ABOUT COOKING.
#
# 1. SHE ONLY WANDERS WHERE IT CANNOT COST ANYTHING. See _is_important below:
#    any step carrying a quantity, a temperature, a time or a doneness cue is
#    delivered straight. "Grease the pan" can take a digression; "bake at 350
#    for 25 minutes" cannot, and no amount of charm is worth a ruined dish.
#
# 2. GENERAL PERIOD OBSERVATIONS, NEVER INVENTED MEMORIES. Every line below is
#    something that was true of the time, phrased as an old woman remarking on
#    how things were. None of them is a story about HER life. Writing "I
#    remember when my mother..." would be putting a fabricated biography in a
#    real person's mouth, on a family site, where somebody years from now may
#    not know which parts were real. Era colour is fair; invented memories of
#    a living relative are not, and the line is worth holding even though the
#    result is slightly less vivid.
#
# The facts themselves are ordinary and checkable -- sliced bread, iceboxes,
# margarine sold white with a colour capsule because of the dairy laws.
_TRIVIA = [
    "You know they only started selling bread already sliced around 1930. "
    "Before that everybody cut their own.",

    "Folks kept things in an icebox back then. A man came round with the ice. "
    "Refrigerators were a rich person's thing for a good while.",

    "Margarine came white as lard in those days. They put a little capsule of "
    "colour in the packet and you worked it in yourself. The dairy people saw "
    "to that.",

    "The chocolate chip cookie only came about in the thirties. Somebody at an "
    "inn up in Massachusetts, chopping up a chocolate bar.",

    "Spam turned up in thirty seven. People made jokes, but it fed a lot of "
    "houses that had nothing else.",

    "Macaroni and cheese in a box, that was thirty seven as well. A box fed "
    "four for under a quarter, and that mattered then.",

    "Nobody threw anything out in the Depression. Bones went in the pot, "
    "grease went in a can on the stove and got used again.",

    "Ritz crackers came in thirty four. My word, people put everything on "
    "those.",
]


# Numbers reach this already spoken as words, because tts.py normalises before
# anything gets here -- so "two cups" and "350 degrees" both have to match.
_NUMBER = (r"\d+(?:\.\d+)?|one|two|three|four|five|six|seven|eight|nine|ten|"
           r"eleven|twelve|half a|a half|a quarter of a|three quarters of a|"
           r"a third of a|two thirds of a|an eighth of a")
_UNIT = (r"cups?|teaspoons?|tablespoons?|ounces?|pounds?|grams?|kilograms?|"
         r"millilitres?|minutes?|hours?|seconds?|degrees?|quarts?|pints?")
_QTY = re.compile(rf"\b({_NUMBER})\s+({_UNIT})\b", re.I)


def _restate_quantity(clause: str) -> str | None:
    """Repeat the measurement in a clause, the way somebody checks themselves.

    Returns None when there is no number to repeat -- a hesitation with
    nothing to check is just a stall, and reads as confusion rather than care.
    """
    m = _QTY.search(clause)
    if not m:
        return None
    # Capitalised like the sentence it becomes -- the model reads a lowercase
    # opening flatter, and this line is meant to land as its own thought.
    said = f"{m.group(1)} {m.group(2)}."
    return said[0].upper() + said[1:]


# A step is IMPORTANT if getting it wrong changes the food. That is a wider
# net than just numbers: "until it thickens" carries no measurement and is
# still the whole instruction, and a tangent dropped in front of "do not let
# it boil" is how somebody ends up with a split sauce.
#
# Deliberately generous -- when in doubt a step counts as important and she
# simply says it plainly. The cost of being wrong in that direction is a
# slightly duller sentence; the cost the other way is the recipe.
_DONENESS = re.compile(
    r"\b(until|do not|don't|never|careful|watch|burn|scorch|overcook|"
    r"boil|simmer|bake|roast|fry|broil|whisk|fold|knead|proof|chill|"
    r"freeze|thicken\w*|golden|tender|smooth|curdle|split)\b"
    # "set" only where it means done -- "until set", "has set". Bare "set"
    # caught "set it aside", which is precisely the harmless kind of step she
    # is supposed to be free to wander on.
    r"|\b(?:until|is|has|have|had)\s+set\b"
    # "rest" the same way: "let it rest" matters, "rest of the flour" does not.
    r"|\b(?:let\s+(?:\w+\s+){1,3}rest|resting)\b", re.I)


def _is_important(clause: str) -> bool:
    return bool(_QTY.search(clause) or _DONENESS.search(clause))


def spoken_like_nellie(step_text: str, intensity: str = "medium",
                       ramble: bool = False, seed: int = 0) -> str:
    """One recipe step, in her cadence. Blank lines become spoken pauses.

    intensity:
      "light"  -- pauses only, no interruptions
      "medium" -- pauses, and she checks a measurement back to herself
      "heavy"  -- also loses the thread once and picks it up again
    """
    text = (step_text or "").strip()
    if not text:
        return ""

    # Break at sentence ends AND at every comma. A person of ninety-three
    # telling you how to cook stops at both; the commas are most of it. Very
    # short fragments are re-attached rather than left stranded on a line,
    # because a two-word line becomes an odd little stab of speech.
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|,\s+", text) if p.strip()]
    merged: list[str] = []
    for p in parts:
        if merged and len(p.split()) < 3:
            merged[-1] = f"{merged[-1]}, {p}"
        else:
            merged.append(p)
    parts = merged or [text]

    out: list[str] = []
    restated = False
    lose_it_at = len(parts) // 2 if len(parts) > 2 else -1

    for i, clause in enumerate(parts):
        clause = clause.rstrip(",").strip()
        if not clause.endswith((".", "!", "?")):
            clause += "."
        # Capitalise, since splitting mid-sentence leaves clauses starting
        # lowercase and the model reads a lowercase opening more flatly.
        clause = clause[0].upper() + clause[1:] if clause else clause
        out.append(clause)

        if intensity == "light":
            continue

        # She checks a measurement back to herself, ONCE, the first time one
        # appears. Restating is safe; it is also exactly what somebody does
        # when they are not certain they said it right.
        if not restated:
            again = _restate_quantity(clause)
            if again:
                out.append(again)
                restated = True

        # Heavy: once, in the middle, she loses the thread and picks it up --
        # and picks it up by REPEATING the clause she was on, never by moving
        # to the next. That is the whole difference between forgetful and
        # unreliable, and it is why nothing is ever skipped.
        if intensity == "heavy" and i == lose_it_at:
            out.append(_PICKUPS[i % len(_PICKUPS)])
            out.append(clause)

    # --- the wandering ------------------------------------------------------
    # SHE WANDERS ON EVERY STEP NOW. WHERE she wanders is what changed.
    #
    # Jason first asked for tangents only on unimportant steps, so a measured
    # one could not be ruined. Then, hearing the version he liked -- "stir in
    # two cups of milk ... until it thickens", which is measured and therefore
    # got no tangent at all -- he asked for her to ramble there too.
    #
    # Both asks are satisfied by moving the tangent rather than by dropping
    # the rule. On a step that carries a measurement, a temperature, a time or
    # a doneness cue, the instruction is delivered WHOLE first, and only then
    # does she wander -- and then says it again. Nothing is ever interrupted
    # between a number and its unit, and the last thing heard is always the
    # instruction.
    #
    # On a step with nothing to get wrong -- "grease a baking dish" -- she can
    # go off wherever, because there is nothing there to protect.
    if ramble and parts:
        important = any(_is_important(p) for p in parts)
        aside = _TRIVIA[seed % len(_TRIVIA)]
        out.append(aside)
        out.append(_PICKUPS[(seed + 1) % len(_PICKUPS)])
        # Coming back to the WHOLE step when it mattered, to the opening
        # clause when it did not. Repeating four clauses of small talk is
        # tiresome; repeating four clauses of a measured instruction is the
        # entire safety of doing this at all.
        #
        # The ORIGINAL sentence, not the clauses joined back up: splitting
        # stripped the commas that separate them, so rejoining produced "stir
        # in two cups of milk bring it up to a simmer and let it cook" -- one
        # breathless run-on, read as one. The text as written is right here
        # and already punctuated.
        out.append(text if important else parts[0])

    return BREAK.join(out)
