"""Pictures for the steps that have none, drawn on the card's spare time.

WHAT THIS IS FOR
----------------
A method step with no photograph gets a diagram from diagram.py -- a line
drawing of a pan and a number. It is honest and it is instant, but it does not
tell you what "season it good on both sides and let it sit" is supposed to look
like. This fills that gap for the steps worth filling it for.

THREE THINGS IT IS DELIBERATELY NOT
-----------------------------------
NOT a photograph, and never presented as one. Every drawn picture is labelled
on the page as a drawing. Somebody who cooks from a picture of a browned chop
and gets a raw one has been lied to by software, and no amount of quality
makes that all right.

NOT a web search. "Find a picture that fits this step" sounds cheaper than
drawing one and is worse in every way that matters: the picture is of somebody
else's chicken in somebody else's pan, it is usually a different recipe
entirely, and it is somebody's copyrighted photograph being hotlinked onto a
page that implies it is this recipe's. A drawing is obviously a drawing.

NOT allowed to slow anything down. The whole job runs behind imagine's "have
you got room" check, one picture at a time, and hands the card back the moment
it is finished -- see release().

THE PROMPT IS BUILT IN CODE
---------------------------
The step text already says what is happening. Handing it to a language model
to rewrite as an image prompt would spend the GPU we are trying to stay out of
the way of, add a second failure mode, and produce roughly what a few lines of
string handling produce -- because the useful transformation is "strip the
timings and the temperatures, keep the food and the vessel, say it is a step in
progress", and none of that needs a model.

WHICH STEPS GET ONE
-------------------
Steps with a real photo do not. Steps that are pure waiting ("cover and leave
overnight") do not -- there is nothing to see, and a picture of nothing is
worse than the diagram. Steps under a handful of words do not, because there is
not enough in them to draw from.
"""
import re

import httpx

from . import core, diagram, importer, llm

BASE = core.os.getenv("KITCHEN_IMAGINE_URL", "http://imagine:8020").rstrip("/")
TIMEOUT = float(core.os.getenv("KITCHEN_IMAGINE_TIMEOUT", "120"))

# Everything the picture should not contain. Hands and text are the two things
# these models get visibly wrong, and a step picture with six-fingered hands
# holding a garbled label is what people point at and stop trusting.
#
# The plating words are the other half, and they were learned the hard way. A
# prompt that says "food photograph" gets a beauty shot -- the first draw of
# "season the chicken good on both sides" came back a golden roast chicken
# plated with parsley and a lemon wedge, which is a lovely picture of the wrong
# end of the recipe. Every step but the last is work in progress, so everything
# that says "finished and served" has to be pushed away.
NEGATIVE = ("text, words, letters, watermark, signature, hands, fingers, "
            "people, faces, cartoon, illustration, drawing, blurry, deformed, "
            "plated meal, serving plate, restaurant presentation, table "
            "setting, cutlery, garnish, finished dish")

# ...except at the end, where a finished dish is exactly what the step is.
SERVED_NEGATIVE = ("text, words, letters, watermark, signature, hands, "
                   "fingers, people, faces, cartoon, illustration, drawing, "
                   "blurry, deformed")

# Steps that ARE the finished dish. Only these get to look like one.
_SERVING = re.compile(
    r"\b(serve|serving|plate up|dish up|garnish|enjoy|spoon onto|"
    r"ladle into)\b", re.I)

# Words that mean nothing is happening that could be photographed.
_NOTHING = re.compile(
    r"^\s*(cover|refrigerate|chill|leave|let|allow|rest|set aside|wait|"
    r"marinate|soak|freeze|thaw|defrost|store|keep)\b", re.I)

# diagram.py's scenes that are equipment or plain waiting, not food itself.
# "Preheat the oven to 350" clears the word count above with room to spare,
# but there is nothing in it a photograph shows that a schematic oven with
# 350 on the door does not already say, more plainly and without a chance of
# getting the wrong oven. These keep their diagram regardless of length --
# only a scene where food is actually being combined, cooked, or served
# (bowl, pan, boil, board, plate, or nothing recognised at all) earns a
# drawing.
_EQUIPMENT_SCENES = {"oven", "fridge", "tray", "timer"}

# Times, temperatures and quantities. All correct, none of them visible in a
# picture, and all of them things the model tries to render as text somewhere
# in the frame.
#
# EACH PATTERN EATS ITS OWN PREPOSITION. Stripping "8 minutes" out of "fry the
# onions for 8 minutes at 180C" leaves "fry the onions for at", a dangling pair
# of prepositions the model then tries to make sense of. The "for" and the "at"
# belong to the number, so they go with it.
_STRIP = [
    # "for 25-30 minutes"
    (re.compile(r"\b(?:for\s+)?\d+\s*(?:-|to|–)\s*\d+\s*"
                r"(?:minutes?|mins?|hours?|hrs?|seconds?|secs?)\b", re.I), ""),
    # "for 8 minutes", "for 1 hour"
    (re.compile(r"\b(?:for\s+)?\d+\s*"
                r"(?:minutes?|mins?|hours?|hrs?|seconds?|secs?)\b", re.I), ""),
    # "at 180C", "at 350 F"
    (re.compile(r"\b(?:at\s+|to\s+)?\d+\s*(?:°|degrees?)?\s*[cf]\b",
                re.I), ""),
    # "to 200 degrees"
    (re.compile(r"\b(?:at\s+|to\s+)?\d+\s*(?:°|degrees?)", re.I), ""),
    (re.compile(r"\b(?:to\s+)?gas mark \d+\b", re.I), ""),
    # "250ml stock" -> "stock"
    (re.compile(r"\b\d+(?:\.\d+)?\s*"
                r"(?:g|kg|ml|l|oz|lb|lbs|cups?|tbsp|tsp)\b", re.I), ""),
    (re.compile(r"^\s*(?:step\s*)?\d+[.)]\s*", re.I), ""),
    # Whatever the strips left dangling: a preposition with nothing after it.
    (re.compile(r"\b(?:for|at|to|about|around|approximately)\s*"
                r"(?=[,.;]|\s*$|\s+(?:and|or|then|until)\b)", re.I), ""),
    (re.compile(r"\s*,\s*(?=,)"), ""),
    (re.compile(r"\s{2,}"), " "),
]


def missing_steps(recipe: dict) -> list:
    """Steps in this recipe with no picture of any kind that are worth
    drawing one for -- the exact skip logic main.py's _draw_each applies,
    factored out so a caller can count or list them without touching the
    graphics card. Used by draw_fix.py's --show, so a library-wide count
    never has to guess at what --backfill would actually do.
    """
    steps = importer.attach_images(recipe.get("steps", []), recipe.get("images", []))
    drawn = recipe.get("drawn") or {}
    mine = recipe.get("mine") or {}
    out = []
    for step in steps:
        slot = f"step-{step['n']}"
        if mine.get(slot) or step.get("photo") or drawn.get(slot):
            continue
        if not worth_drawing(step["text"]):
            continue
        out.append(step)
    return out


def worth_drawing(text: str) -> bool:
    """Is there anything in this step a picture would show?"""
    t = (text or "").strip()
    if len(t.split()) < 5:
        return False
    if _NOTHING.match(t):
        return False
    return diagram.scene_for(t) not in _EQUIPMENT_SCENES


def prompt_for(step_text: str, dish: str = "", cuisine: str = "",
               reference_detail: str = "", items: list | None = None,
               tools: list | None = None) -> str:
    """A picture prompt, from the step, with no model involved.

    Verbose, but every extra word is a NAME, not an adjective -- these
    distilled models follow a plain description better than a long one,
    and a prompt padded with adjectives pulls the picture away from what
    the step actually said. The lengthening here is specifically the
    fix for Jason's own report (2026-08-25): "the image generator was
    supposed to be more specific using the items and more verbose
    details along with the tools" -- the generic "cooking in progress"
    picture a bare step sentence produces, made specific by naming the
    real ingredients and tools involved, is the actual complaint, not
    the prompt's length for its own sake.

    `items`/`tools` are this step's own ingredients/tools (see
    main.py's _draw_each, which matches them off icon_map the same way
    the step's own small call-out icons are matched) -- passed in
    rather than parsed here because a step's sentence often leans on a
    pronoun ("mix them together") that names nothing a picture can be
    specific about, so the caller's independent match against the real
    ingredient list is often the ONLY source for the real names.

    `reference_detail` is the ONE piece of this that IS model-derived --
    a vision model's own description of a picture Jason found online (see
    llm.describe_image, main.py's _draw_each), when one exists for this
    step. Optional and appended last, on purpose: everything above it is
    the mechanical, always-reliable baseline, so a reference description
    that turns out vague or off-topic degrades the prompt at the margin
    rather than replacing the part that always works.
    """
    what = (step_text or "").strip()
    for pattern, to in _STRIP:
        what = pattern.sub(to, what)
    what = what.strip(" ,.;:").strip()
    # Up to two sentences now, not one -- a single short clause ("mix
    # well") was often all a step contributed, and "more verbose" is
    # exactly the ask; the second sentence is usually still the same
    # piece of action, not yet the tidying-up that comes later in a long
    # step. Still capped, so a ten-sentence step cannot balloon the
    # prompt into the noise this function's whole design avoids.
    sentences = re.split(r"(?<=[.;])\s+", what)
    what = " ".join(sentences[:2])[:260].strip(" ,.;:")

    # THE ACTION LEADS. Not decoration: these models weight the front of the
    # prompt hardest, so a prompt that opens with "food photograph" gets a
    # photograph of food -- plated, garnished, finished -- whatever the rest of
    # it says. Opening with what is being done is what makes the picture be of
    # the step rather than of the dinner.
    served = bool(_SERVING.search(step_text or ""))
    bits = []
    if what:
        bits.append(what.lower())
    # NAMED, not just implied. If the step's own sentence already said
    # "butter" this repeats it -- redundancy costs nothing here and a
    # distilled model reinforced on a word is more likely to draw it than
    # one it saw once in passing.
    if items:
        bits.append("with " + ", ".join(i.lower() for i in items))
    if tools:
        bits.append("using a " + " and a ".join(t.lower() for t in tools))
    if dish:
        bits.append(f"making {dish.strip()[:60].lower()}")
    if cuisine:
        bits.append(f"{cuisine.strip()[:30].lower()} cooking")
    bits.append("the finished dish, plated" if served
                else "cooking in progress, mid preparation")
    bits += ["photograph, home kitchen", "natural light"]
    if reference_detail:
        bits.append(reference_detail.strip()[:200])
    return ", ".join(bits)


def negative_for(step_text: str) -> str:
    """The serving step is allowed to look finished. Nothing before it is."""
    return SERVED_NEGATIVE if _SERVING.search(step_text or "") else NEGATIVE


async def room() -> tuple:
    """(is there room, why not). Never raises -- a drawing is never urgent."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{BASE}/health")
            if r.status_code != 200:
                return False, f"the drawing service answered {r.status_code}"
            body = r.json()
            if body.get("room"):
                return True, ""
            return False, (f"the graphics card is busy "
                           f"({body.get('free_mb', 0)}MB free, "
                           f"{body.get('need_mb', 0)}MB needed)")
    except Exception as exc:
        # THE SERVICE WAS RETIRED, NOT BROKEN (found 2026-09-06). `imagine`
        # was deleted in the 2026-08-31 section sweep -- container, image,
        # compose entry and volume -- and this caller was never told, so
        # it has been reporting a connection error for a thing that no
        # longer exists. A ConnectError to a host that is not in compose
        # is not a fault to investigate; it is a message that should say
        # what happened and where the replacement is.
        if type(exc).__name__ in ("ConnectError", "ConnectTimeout"):
            return False, (
                "RETIRED: the local drawing service (imagine) was removed on "
                "2026-08-31 and no longer exists. Step pictures now come from "
                "Gemini instead -- open the same recipe at /recipe-gemini. "
                "Set KITCHEN_IMAGINE_URL only if you stand a local drawer "
                "back up.")
        return False, f"the drawing service is not answering ({type(exc).__name__})"


async def release():
    """Tell the drawing service we are finished, so it hands the card back.

    Always called after a run of drawings, in a finally. Loaded, imagine holds
    about 7GB and draws in half a second -- so a whole recipe is done in
    seconds and the memory would otherwise sit taken until its idle timer
    fires, which on a one-GPU box is a stretch where the phone line cannot load
    a model. Not urgent, and never worth raising over.
    """
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(f"{BASE}/release")
    except Exception:
        pass


async def draw(prompt: str, seed: int = 0, negative: str = "") -> bytes:
    """PNG bytes, or b"" if it could not be drawn right now."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{BASE}/draw", json={
                "prompt": prompt, "negative": negative or NEGATIVE,
                "seed": seed})
            if r.status_code == 200 and r.content[:8] == b"\x89PNG\r\n\x1a\n":
                return r.content
    except Exception:
        pass
    return b""


async def caption_for(step_text: str, dish: str = "") -> str:
    """A short caption for a drawn illustration -- the one piece of this
    module that DOES use a model. prompt_for() is deliberately mechanical
    (see the module docstring for why); a caption is prose meant to be read,
    and prose is what a model is for. Always says plainly that it's a
    drawing, matching the rule that nothing here is ever presented as a
    photo. Never raises -- a missing caption is not a reason to lose the
    picture.
    """
    system = ("You caption AI-drawn illustrations of cooking steps, for "
              "someone following a recipe. One short sentence, under 20 "
              "words. Say plainly that it is a drawing, not a photo. Plain "
              "and warm, no hype, no exclamation points.")
    user = f"Step: {(step_text or '').strip()[:300]}\nDish: {dish or 'this recipe'}"
    try:
        # LLM_LABEL (llama3.2:3b), NOT LLM_TALK -- CAUGHT LIVE: LLM_TALK's
        # qwen3:8b is a thinking model and a 60-token budget just returns a
        # chunk of its own reasoning trace instead of an answer. Same
        # gotcha core.py already documents for LLM_LABEL's own reasoning.
        text = await llm.chat(system, user, model=core.LLM_LABEL, max_tokens=60)
        return text.strip().strip('"')
    except llm.LLMError:
        return "A drawing of this step -- not a photo."
