"""Little cards about a single food or tool -- Jason's own request
(2026-08-24): "opening up a new page about something... where to find
coriander in your supermarket... a shank... nutritional information...
maybe include some history." Tapping a food/tool name that already has a
picture opens a small card, not a link to a real site -- see guide.html's
own JS for the tap handling.

WHY THIS IS THE MODEL FOR EVERYTHING EXCEPT NUTRITION
--------------------------------------------------------
"Where in the supermarket", "how to trim this cut", "a little history" are
all the same shape of question extras.py already answers for a whole
recipe: no table of every food/tool in the world to look this up against,
so it is worked out once and kept, not fetched live. Nutrition is
different -- Kitchen already has a REAL, lab-measured number for it
(store.food_baseline(), the same USDA FoodData Central lookup
nutrition.py/tandoor.py use to cost out a recipe), so that one section is
built from data already on hand, in code, never asked of the model at all.
Two different kinds of question, two different sources of truth, same as
nutrition.py's own "USDA first, a model only when there is truly nothing
else" rule.

NEVER FETCHED FROM A REAL SITE, ON PURPOSE
---------------------------------------------
Jason's own words: "these will just be pop-ups not direct links to actual
sites... if it's safe space we can make them JSON files." So this only
ever calls the self-hosted model (same as every other suggestion in this
app) and stores the result as JSON -- nothing here makes an outbound
request to a real supermarket site, Wikipedia, or anywhere else.

WHY FLAT JSON FILES, NOT A DATABASE TABLE
--------------------------------------------
Jason's own words, twice (2026-08-24): "if it's safe space we can make
them JSON files" and then again, explicitly, "I would like to manage all
the tools and food items there with .json files." Same convention
toolimages.py already uses for a tool's picture reference (tool-images.json)
-- one flat file per kind, read fresh every call so two processes never
need to coordinate over it, and directly editable by hand or by the
/admin panel without going through a migration.

GENERATED ONCE, HAND-EDITS NEVER OVERWRITTEN
------------------------------------------------
Jason's own words: "that I'll fill in." Same trust rule as
taxonomy.py's `locked`: a card marked `edited` is his, and a later
suggest() call must never silently replace it -- see main.py's item_info
route.
"""
import json

from . import core, llm, nutrition, store

_FILES = {
    "food": core.DATA_DIR / "food-info.json",
    "tool": core.DATA_DIR / "tool-info.json",
}


def _load(kind: str) -> dict:
    try:
        return json.loads(_FILES[kind].read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_all(kind: str, data: dict) -> None:
    path = _FILES[kind]
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".part")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(path)


def get(kind: str, item_id) -> dict | None:
    return _load(kind).get(str(item_id))


def save(kind: str, item_id, data: dict, edited: bool = False) -> None:
    all_items = _load(kind)
    all_items[str(item_id)] = {**data, "edited": edited}
    _save_all(kind, all_items)


def all_cards(kind: str) -> dict:
    """The whole file, for /admin's own management page -- one read
    instead of one per item."""
    return _load(kind)


def carry_over(kind: str, old_id, new_id) -> bool:
    """An info card written for `old_id` survives a merge into `new_id`
    -- same bug and same fix as foodphotos.py/toolimages.py's own
    carry_over(), for the where-to-find/how-to-use/history/fun-fact card
    instead of the picture. Never overwrites a card already on `new_id`,
    and never carries over an UN-edited (model-generated) card onto a
    survivor that already has any card at all -- Jason's own hand-written
    notes always win over a merge."""
    old_id, new_id = str(old_id), str(new_id)
    if get(kind, new_id):
        return False
    entry = get(kind, old_id)
    if not entry:
        return False
    save(kind, new_id, {k: v for k, v in entry.items() if k != "edited"},
        edited=bool(entry.get("edited")))
    return True


SYSTEM = """You write ONE short reference card about a single kitchen
food or kitchen tool, for a home cook glancing at it mid-recipe -- not an
encyclopedia entry. Keep every field to 1-3 plain sentences. If you are
not genuinely confident about a field, leave it as an empty string rather
than guessing or padding -- an empty section is honest, a vague one is not.
Never invent a specific brand, price, or store name. History means a
short, well-known fact (where a dish/ingredient/tool style originated, or
a notable role it's played), not a full timeline.

Return JSON exactly:
{"where_to_find": "", "how_to_use": "", "history": "", "fun_fact": ""}

For a FOOD: where_to_find is which aisle/section of a typical US
supermarket; how_to_use covers prep basics relevant to it (how it's
usually cut, trimmed, or handled).
For a TOOL: where_to_find is what kind of store carries it; how_to_use
covers what it's actually for and a tip on using it well."""


async def suggest(kind: str, name: str) -> dict:
    """One card, worked out once. Never raises -- a card that fails to
    generate is just an empty one, same as extras.py's own [] on failure;
    nothing downstream needs this to succeed."""
    user = f"{'Food' if kind == 'food' else 'Tool'}: {name}"
    try:
        data = await llm.chat_json(SYSTEM, user, model=core.LLM_LABEL,
                                   temperature=0.3, max_tokens=400)
    except llm.LLMError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: str(data.get(k) or "").strip()
           for k in ("where_to_find", "how_to_use", "history", "fun_fact")}


_NUTRIENT_LABELS = [
    ("calories", "Calories", "kcal"),
    ("protein_g", "Protein", "g"),
    ("fat_g", "Fat", "g"),
    ("carbs_g", "Carbs", "g"),
    ("fiber_g", "Fiber", "g"),
    ("sodium_mg", "Sodium", "mg"),
]


def nutrition_card(name: str) -> dict | None:
    """A short, human read of store.food_baseline()'s already-fetched USDA
    data -- Jason's own example, "what the USDA says about salt" --
    without ever calling out anywhere new. None if this food has no
    baseline cached yet (nobody has imported a recipe using it, or
    nutrition.py could not find a confident match -- see its own
    relevance guard)."""
    base = store.food_baseline(name)
    if not base:
        return None
    nutrients = base.get("nutrients") or {}
    lines = []
    for key, label, unit in _NUTRIENT_LABELS:
        v = nutrients.get(key)
        if v and v.get("value") is not None:
            lines.append(f"{label}: {v['value']:g} {unit}")
    if not lines:
        return None
    return {"per": base.get("per") or "100g", "lines": lines,
           "source": base.get("source") or "USDA FoodData Central",
           "matched_name": base.get("matchedName") or name,
           "is_estimate": (base.get("dataType") or "") == "LLM estimate"}
