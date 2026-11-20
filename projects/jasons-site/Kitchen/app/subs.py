"""Substitutions: the table first, the model for what the table does not know.

THE SAME SHAPE AS MOM'S KNOWLEDGE BASE
--------------------------------------
kb.py in the Tech Help app makes an argument that applies here word for word:
some questions have one right answer, it does not change, and asking an 8B
model to re-derive it every time is slower, less consistent, and occasionally
wrong. "What can I use instead of buttermilk" is that kind of question. The
answer is a cup of milk with a tablespoon of lemon juice, left ten minutes, and
it has been that answer for a hundred years.

So the table below answers first, instantly, with no GPU involved -- and it
answers with a RATIO, which is the part people actually get wrong. "Use yoghurt
instead" is not useful. "3/4 cup yoghurt thinned with 1/4 cup water, per cup"
is.

The model gets the rest: the unusual ingredient, the one where what you are
making changes the answer, the one nobody has written down. Its answers are
labelled as its answers on the page. You should always be able to see whether
you are reading something written down or something worked out.

WHY THE SAFETY LINE IS ADDED IN CODE AND NOT IN THE PROMPT
----------------------------------------------------------
Allergy substitutions are the one place a wrong answer hurts someone. A model
that is 97% right about swapping butter for oil is fine; a model that is 97%
right about whether something contains nuts is not, because the other 3% is an
ambulance. So anything matching an allergen gets a fixed warning appended in
CODE, after the model has spoken, where no unlucky sample and no instruction
hidden in a scraped page can remove it. Mom's safety.py takes the same
position for the same reason.
"""
import re

from . import core, llm

# ---------------------------------------------------------------------------
#  The written-down ones. Ratio first -- that is the part people get wrong.
# ---------------------------------------------------------------------------
TABLE = {
    "buttermilk": [
        "1 cup milk + 1 tbsp lemon juice or white vinegar, stood for 10 min",
        "3/4 cup plain yoghurt + 1/4 cup water, whisked smooth",
    ],
    "egg": [
        "Baking, per egg: 1 tbsp ground flaxseed + 3 tbsp water, stood 5 min",
        "Binding, per egg: 1/4 cup unsweetened apple sauce or mashed banana",
        "Richness only: 3 tbsp aquafaba (the liquid from a tin of chickpeas)",
    ],
    "butter": [
        "Baking: the same weight of a neutral oil, minus about 20%",
        "Frying: oil, one for one",
        "Dairy-free: a hard block margarine, one for one (tub spreads are wetter)",
    ],
    "heavy cream": [
        "3/4 cup whole milk + 1/4 cup melted butter, per cup (will not whip)",
        "Full-fat coconut milk, one for one, in anything savoury or spiced",
    ],
    "double cream": [
        "3/4 cup whole milk + 1/4 cup melted butter, per cup (will not whip)",
        "Full-fat coconut milk, one for one, in anything savoury or spiced",
    ],
    "sour cream": ["Plain full-fat Greek yoghurt, one for one"],
    "creme fraiche": ["Full-fat Greek yoghurt, one for one, added off the heat"],
    "self-raising flour": [
        "Per cup of plain flour: + 1 1/2 tsp baking powder + 1/4 tsp salt",
    ],
    "baking powder": [
        "Per tsp: 1/4 tsp bicarbonate of soda + 1/2 tsp cream of tartar",
    ],
    "cornflour": ["Per tbsp: 2 tbsp plain flour, for thickening"],
    "cornstarch": ["Per tbsp: 2 tbsp plain flour, for thickening"],
    "brown sugar": ["Per cup: 1 cup white sugar + 1 tbsp molasses or treacle"],
    "caster sugar": ["Granulated sugar blitzed for 20 seconds, or used as it is"],
    "honey": [
        "Maple syrup, one for one",
        "Per cup: 1 1/4 cup sugar + 1/4 cup of the recipe's liquid",
    ],
    "wine": [
        "White: stock + 1 tsp white wine vinegar per 1/2 cup",
        "Red: stock + 1 tsp balsamic or red wine vinegar per 1/2 cup",
    ],
    "shallot": ["Half a small onion + a clove of garlic, per shallot"],
    "fresh herbs": ["Dried, at one third the amount, added earlier in the cooking"],
    "lemon juice": ["Lime juice one for one; white wine vinegar at half"],
    "stock": [
        "Water + half a stock cube per cup, or water and more salt at the end",
    ],
    "breadcrumbs": ["Rolled oats blitzed fine, one for one", "Crushed cornflakes"],
    "parmesan": ["Pecorino or grana padano one for one; nutritional yeast if vegan"],
    "milk": ["Any unsweetened plant milk, one for one (oat browns closest)"],
    "yeast": ["Per 7g sachet: 21g fresh yeast, crumbled into the liquid"],
    "vanilla extract": ["Per tsp: the seeds of half a pod, or 1 tsp vanilla paste"],

    # --- protein --------------------------------------------------------
    "chicken breast": ["Chicken thighs, one for one -- juicier, a few minutes longer"],
    "chicken thighs": ["Chicken breast, one for one -- drier, a few minutes less"],
    "ground beef": ["Ground turkey or chicken, one for one -- add a splash of oil, it's leaner"],
    "ground turkey": ["Ground chicken, one for one", "Ground beef, one for one -- richer, fattier"],
    "italian sausage": ["Ground pork + 1 tsp fennel seed + 1/2 tsp chili flake, per pound"],
    "bacon": ["Pancetta, one for one", "Smoked turkey bacon, one for one -- less fat rendered"],
    "shrimp": ["Cubed firm white fish, same weight, cooked a minute or two less"],

    # --- frozen vs. fresh -------------------------------------------------
    "frozen vegetables": ["Fresh, same weight -- add a few minutes earlier, they take longer to soften"],
    "frozen spinach": ["Fresh spinach, wilted down: use about 3x the weight fresh"],
    "fresh spinach": ["Frozen, thawed and squeezed dry: use about 1/3 the weight"],
    "frozen peas": ["Fresh peas, same amount, blanched 1-2 min first"],

    # --- spices & seasoning -----------------------------------------------
    "cumin": ["Ground coriander, at half the amount -- milder, still earthy"],
    "smoked paprika": ["Sweet paprika + a few drops of liquid smoke, if you have it"],
    "italian seasoning": ["Equal parts dried basil, oregano, thyme -- use what's on hand"],
    "chili powder": ["Per tsp: 1/2 tsp cumin + 1/2 tsp paprika + pinch cayenne"],
    "old bay": ["Per tsp: 1/4 tsp each celery salt, paprika, black pepper, cayenne"],

    # --- sauces & condiments ------------------------------------------------
    "soy sauce": ["Tamari, one for one, if it needs to be gluten-free",
                 "Coconut aminos, one for one -- milder, slightly sweeter"],
    "worcestershire sauce": ["Soy sauce + a dash of vinegar and a pinch of sugar"],
    "fish sauce": ["Soy sauce, one for one -- less funk, similar salt"],
    "marinara sauce": ["A 28oz tin crushed tomatoes + 1 clove garlic + basil + salt, simmered 15 min"],
    "tomato paste": ["Per tbsp: 2 tbsp tomato sauce or ketchup, simmered down a little"],
    "hot sauce": ["A pinch of cayenne or chili flake per dash called for"],

    # --- vegetables ---------------------------------------------------------
    "bell pepper": ["Poblano for less sweetness and more depth, one for one"],
    "celery": ["Celery seed, 1/4 tsp per stalk called for, added with the other aromatics"],
    "leeks": ["Yellow onion, one for one -- sharper, cook a little longer to soften"],
}

# Longest key first, so "self-raising flour" is not claimed by a shorter key
# that happens to appear inside it.
_KEYS = sorted(TABLE, key=len, reverse=True)

# Ingredients where being wrong is not a ruined dinner. Anything matching gets
# the warning below appended IN CODE, after the model has finished.
_ALLERGENS = re.compile(
    r"\b(nut|nuts|peanut|almond|cashew|pistachio|walnut|pecan|hazelnut|"
    r"gluten|wheat|coeliac|celiac|shellfish|prawn|shrimp|crab|lobster|"
    r"sesame|tahini|soy|soya|dairy|lactose|milk|egg|eggs)\b", re.I)

ALLERGY_WARNING = (
    "This is a cooking suggestion, not allergy advice. If someone at the table "
    "has an allergy, read the label on the actual packet you are holding — "
    "recipes change, brands change, and “may contain” is on the box "
    "for a reason."
)


def from_table(ingredient: str):
    """Instant, exact, no GPU. Returns (key, [options]) or None."""
    low = ingredient.lower()
    for key in _KEYS:
        # The trailing "s?" matters more than it looks. Recipes write "3 eggs",
        # not "3 egg", and the table's key is the singular -- so without it the
        # single most-substituted ingredient in cooking never matched, on every
        # recipe, silently.
        if re.search(r"\b" + re.escape(key) + r"s?\b", low):
            return key, TABLE[key]
    return None


SYSTEM = """You are a cook helping someone who has just found they are missing
an ingredient, or cannot eat it. You answer with substitutions.

Rules:
- Give at most three options, best first.
- EVERY option must state the AMOUNT relative to the original. "Use yoghurt" is
  useless; "3/4 cup yoghurt thinned with 1/4 cup water, per cup" is the answer.
- Say plainly what changes about the result: texture, browning, rise, flavour.
- If a substitution genuinely will not work in this dish, say so and say why.
  "There is no good substitute here" is a correct and useful answer.
- No preamble, no sign-off, no mention of being an AI."""


def _read_options(data) -> list:
    """Pull the substitutions out of whatever shape the model chose.

    THE ASKED-FOR SHAPE IS A REQUEST, NOT A GUARANTEE. The prompt says
    {"options": [...]} and the model obliges most of the time -- and then
    answers {"substitutes": [{"amount": "1/2 cup", "ingredient": "cooked
    bacon", "notes": "..."}]} instead, which is a perfectly good answer to a
    different question. The old code read `options` and nothing else, so those
    replies produced an empty list, no error, and a heading with nothing under
    it. The same request succeeded or failed depending on which key the model
    felt like using.

    Both the keys and the item shapes are handled. An option may be a plain
    string, or an object with the amount and the ingredient split apart -- the
    latter is reassembled into the sentence the page expects, because an
    amount is the one thing a substitution must state.
    """
    if not isinstance(data, dict):
        return []
    rows = None
    for key in ("options", "substitutes", "substitutions", "alternatives",
                "suggestions", "results"):
        val = data.get(key)
        if isinstance(val, list) and val:
            rows = val
            break
    if rows is None:
        return []

    out = []
    for row in rows:
        if isinstance(row, str):
            text = row.strip()
        elif isinstance(row, dict):
            amount = str(row.get("amount") or row.get("quantity") or "").strip()
            name = str(row.get("ingredient") or row.get("name")
                       or row.get("option") or row.get("substitute") or "").strip()
            notes = str(row.get("notes") or row.get("note")
                        or row.get("why") or "").strip()
            text = " ".join(p for p in (amount, name) if p)
            if notes:
                text = f"{text} — {notes}" if text else notes
        else:
            text = str(row).strip()
        if text:
            out.append(text)
    return out[:3]


async def suggest(ingredient: str, dish: str = "", why: str = "") -> dict:
    """The table AND the model, together -- not one instead of the other.

    Used to skip the model whenever the table had an answer, on the
    reasoning that a written-down ratio is proven and instant. That is
    still true, but it meant the model never got a chance to add a
    dish-specific angle the table cannot have (this dish, this reason) even
    when the table's generic entry existed. Now both run every time; the
    table's answer is still shown first because it is still the more
    trustworthy one, but the model's is right there beside it rather than
    hidden behind whether the table happened to have something.

    Returns {'table': {'options', 'note'} | None,
             'model': {'options', 'note'} | None, 'warning': str}.
    `model` is None only when the model genuinely could not be reached --
    the table's answer (if there is one) is still returned rather than
    losing the whole request over it.
    """
    warning = ALLERGY_WARNING if _ALLERGENS.search(f"{ingredient} {why}") else ""

    hit = from_table(ingredient)
    table = None
    if hit:
        key, options = hit
        table = {"options": options,
                "note": f"From the substitutions table, under “{key}”."}

    ask = f"What can I use instead of: {ingredient}"
    if dish:
        ask += f"\nI am making: {dish}"
    if why:
        ask += f"\nThe reason I need a substitute: {why}"

    model = None
    model_error = ""
    try:
        data = await llm.chat_json(
            SYSTEM + '\n\nReturn JSON: {"options": ["...", "..."], "note": "..."}',
            ask, model=core.LLM_TALK, temperature=0.4, max_tokens=700,
        )
        options = _read_options(data)
        if not options:
            # ONE RETRY, because the failure is a coin toss rather than a
            # refusal. Measured three identical requests: two came back under
            # a key nothing reads, one came back correct. The model is willing
            # and inconsistent about the shape, and _read_options already
            # accepts every shape seen -- so a second ask is the cheapest
            # thing that turns "sometimes nothing" into "almost always
            # something". Not a loop: two failures in a row is a real answer.
            data = await llm.chat_json(
                SYSTEM + '\n\nReturn JSON with a top-level "options" key whose '
                         'value is an array of plain strings, each naming an '
                         'amount and an ingredient. Example: '
                         '{"options": ["1/2 cup diced ham"], "note": "..."}',
                ask, model=core.LLM_TALK, temperature=0.2, max_tokens=700,
            )
            options = _read_options(data)
        model = {"options": options, "note": str(data.get("note") or "")}
        if not options:
            # NEVER A SILENT EMPTY LIST. The page renders a "Worked out"
            # heading from the presence of `model`, so an empty list showed a
            # label with nothing under it and no explanation -- which is what
            # "I click swap and nothing changes" looked like from outside.
            model["note"] = (model["note"] or
                             "The model answered but not in a form this could "
                             "read. Try again -- it usually works second time.")
            # print, not a logger: this module has no logger and core has no
            # `log` attribute -- reaching for one would have raised inside the
            # exact path that was already failing quietly, which is the
            # opposite of the point. Container logs capture stdout.
            print("subs: unreadable model reply, keys="
                  f"{list(data.keys()) if isinstance(data, dict) else type(data)}",
                  flush=True)
    except llm.LLMError as e:
        model_error = str(e)

    if table is None and model is None:
        # Nothing to show at all -- this is the one case worth raising over,
        # same as before: an error page beats a blank one.
        raise llm.LLMError(model_error or "Nothing in the table, and the "
                          "model did not answer either.")

    return {"table": table, "model": model, "model_error": model_error,
           "warning": warning}


# ---------------------------------------------------------------------------
#  Scanning a whole recipe for what the table already covers
# ---------------------------------------------------------------------------
def scan(ingredients: list) -> list:
    """Which lines of this recipe have a written-down substitute?

    Runs on every guide page. It is a dictionary lookup per line, so it costs
    nothing and needs no model -- which is why the guide can show a "swap this"
    link next to an ingredient without having asked anything first.
    """
    out = []
    for text in ingredients:
        hit = from_table(text)
        if hit:
            out.append({"line": text, "key": hit[0], "options": hit[1]})
    return out


# ---------------------------------------------------------------------------
#  Equipment. Same shape as ingredients above, on purpose -- table first,
# model for anything not written down, safety warnings do not apply here
# (nobody has an allergy to a Dutch oven).
# ---------------------------------------------------------------------------
EQUIPMENT = {
    "stand mixer": ["A hand mixer, same times", "A whisk and a strong arm -- add a few minutes"],
    "hand mixer": ["A whisk, worked harder -- add a few minutes to any 'beat' or 'whip' step"],
    "dutch oven": ["Any heavy, lidded, oven-safe pot -- a stockpot with foil under the lid works"],
    "cast iron skillet": ["Any heavy-bottomed oven-safe pan; results brown a little less evenly"],
    "food processor": ["A blender for wet mixes; a knife and patience for chopping"],
    "blender": ["A food processor", "An immersion (stick) blender, run right in the pot"],
    "immersion blender": ["A regular blender, in batches, cooled slightly first for hot liquid"],
    "stand mixer with dough hook": ["Knead by hand, 8-10 minutes instead of 5-6 machine minutes"],
    "candy thermometer": ["The cold-water test: drop a bit of syrup in ice water and check the stage"],
    "meat thermometer": ["Cut into the thickest part and check the color/juices -- less precise"],
    "springform pan": ["A regular cake pan lined with parchment, with overhang, to lift it out"],
    "rolling pin": ["A clean wine bottle, or a straight-sided glass"],
    "pastry cutter": ["Two knives, worked in a scissor motion", "Your fingertips, worked quickly"],
    "kitchen scale": ["Volume measures (cups/tbsp) -- less precise, fine for most cooking"],
    "wok": ["The largest, heaviest skillet you have, over the highest heat it takes"],
    "grill": ["A grill pan or cast iron skillet, indoors, well preheated"],
    "slow cooker": ["A low oven (250-275F) in a covered Dutch oven, same total time"],
    "instant pot": ["Stovetop, same recipe, roughly 3x the pressure-cook time at a simmer"],
    "mandoline": ["A sharp knife and a steady hand -- go slower, watch your fingers either way"],
}

_EQUIPMENT_KEYS = sorted(EQUIPMENT, key=len, reverse=True)

EQUIPMENT_SYSTEM = """You are a cook helping someone who does not have a piece
of equipment a recipe calls for. You answer with what to use instead and how
the method changes.

Rules:
- Give at most three options, best first.
- Say what actually changes: timing, texture, how closely to watch it.
- If there genuinely is no good workaround, say so plainly and say why.
- No preamble, no sign-off, no mention of being an AI."""


def from_equipment_table(item: str):
    """Instant, exact, no GPU. Returns (key, [options]) or None."""
    low = item.lower()
    for key in _EQUIPMENT_KEYS:
        if re.search(r"\b" + re.escape(key) + r"s?\b", low):
            return key, EQUIPMENT[key]
    return None


async def suggest_equipment(item: str, dish: str = "") -> dict:
    """Same shape as suggest() above: table first, model only if the table
    has nothing. No `why`/allergy warning -- neither applies to equipment."""
    hit = from_equipment_table(item)
    if hit:
        key, options = hit
        return {"source": "written down", "options": options,
                "note": f"From the substitutions table, under “{key}”.",
                "warning": ""}

    ask = f"What can I use instead of: {item}"
    if dish:
        ask += f"\nI am making: {dish}"

    data = await llm.chat_json(
        EQUIPMENT_SYSTEM + '\n\nReturn JSON: {"options": ["...", "..."], "note": "..."}',
        ask, model=core.LLM_TALK, temperature=0.4, max_tokens=700,
    )
    options = [str(o) for o in (data.get("options") or []) if str(o).strip()][:3]
    return {"source": "worked out", "options": options,
           "note": str(data.get("note") or ""), "warning": ""}


# ---------------------------------------------------------------------------
#  A SPECIFIC substitute you already have, converted to a SPECIFIC amount
# ---------------------------------------------------------------------------
# Different question from suggest() above. suggest() is "what could I use
# instead of X" -- several options, best first, for someone with an empty
# spot in the kitchen. This is "I already have Y, the recipe wants this much
# X, how much Y do I actually need" -- one answer, for someone standing at
# the counter holding the substitute in their hand.
#
# ALWAYS THE MODEL, NO TABLE. A fixed ratio table works for "X instead of Y"
# in general, but the right AMOUNT here depends on the specific product as
# much as the ingredient -- "stevia" alone covers baking blends that measure
# roughly cup-for-cup and pure extract at a small fraction of that, and a
# table cannot know which one is in the cupboard. Labelled the same
# "worked out" way every other model answer in this file is -- never
# presented as a certainty a table would be.
CONVERT_SYSTEM = """You are a cook working out an exact substitute amount.
Someone has one specific ingredient in their kitchen and wants to know how
much of exactly THAT to use in place of what a recipe calls for.

Rules:
- The "amount" field is REQUIRED and must be a real measurement someone
  could put in a measuring spoon or cup -- "1 tsp", never "a small amount",
  "to taste", or "adjust as needed". Give your best specific number even
  when the exact product varies -- put the caveat in "note" instead, do not
  leave "amount" vague or blank.
- If this substitute genuinely will not work at all for this ingredient,
  put "not a good substitute here" as the amount and say why in "note" --
  that is still an amount field filled in, just not a measurement.
- One short note on what changes about the result -- sweetness, texture,
  browning, rise. Optional: skip it if there is nothing worth saying.
- No preamble, no sign-off, no mention of being an AI."""


async def convert(recipe_line: str, have: str, dish: str = "") -> dict:
    """recipe_line is the recipe's own ingredient text, WITH its amount
    ("1 cup sugar") -- not just the ingredient name. `have` is the specific
    thing they actually have ("stevia", "monk fruit sweetener").

    Returns {'amount': str, 'note': str, 'warning': str}. Raises
    llm.LLMError on failure rather than falling back to anything -- there is
    no table entry to fall back to here, so the caller has to say the model
    was unavailable rather than show a wrong or empty amount.
    """
    warning = ALLERGY_WARNING if _ALLERGENS.search(f"{recipe_line} {have}") else ""
    ask = f"The recipe calls for: {recipe_line}\nI have: {have}"
    if dish:
        ask += f"\nI am making: {dish}"
    ask += f"\nHow much {have} do I need instead?"

    data = await llm.chat_json(
        CONVERT_SYSTEM + '\n\nReturn JSON: {"amount": "...", "note": "..."}',
        ask, model=core.LLM_TALK, temperature=0.3, max_tokens=400,
    )
    return {"amount": str(data.get("amount") or "").strip(),
           "note": str(data.get("note") or "").strip(), "warning": warning}
