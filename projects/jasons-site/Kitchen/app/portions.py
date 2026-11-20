"""A baseline gram weight for one COUNTED serving of a food -- "1 stalk
celery", "1 clove garlic" -- so it stops being silently dropped from
nutrition totals. Jason's own request (2026-08-25): "I just want the llm
to per recipe be able to determine that one stalk is only a third of
their recommended three stalk[s]... I just want a baseline."

WHY THIS GAP EXISTS AT ALL
------------------------------
recipe_nutrition.py's own grams_for() converts "2 cups flour" or "500g
beef" into grams just fine -- but "1 stalk celery", "2 cloves garlic",
"1 onion" have no conversion factor of their own (a stalk isn't a fixed
weight the way a cup or a gram is), so its own docstring says plainly:
"skipped, not guessed". This module is the missing reference: how much
does ONE of these actually weigh, on average.

WHY USDA'S OWN DATA, NOT AN LLM GUESS
-----------------------------------------
Checked live (2026-08-25): USDA's SR Legacy dataset (unlike the newer,
narrower Foundation Foods set) ships a real food_portion.csv with exactly
this -- "celery, raw" has a real, measured "1 stalk = 51g" row. Reusing
published, measured data is strictly better than asking a model to guess
a plausible-sounding number, same reasoning as nutrition.py's own
"USDA first, a model only when there is truly nothing else" rule.

WHY ONE FLAT JSON FILE, KEYED BY TANDOOR FOOD ID
-----------------------------------------------------
Same convention as iteminfo.py's own cards (Jason's own words, twice:
"if it's safe space we can make them JSON files") -- directly viewable
and hand-editable from /admin/items, and a food he's already corrected
("edited": true) is never silently overwritten by a later re-import
pass, same trust rule as every other card in this app.

WHAT NEVER GETS A USDA MATCH, ON PURPOSE
---------------------------------------------
Jason's own words: "I will hand type anything... that is not
proportional, like a box of mangoes." A "box" isn't a real, standard
unit -- there is no honest USDA lookup for it, and build_baselines()
below simply leaves those foods alone rather than forcing a bad match.
"""
import csv
import re

from . import core, nutrition, usda_local

RAW_DIR = core.DATA_DIR / "usda_raw_portions"
BASELINE_FILE = core.DATA_DIR / "food-portions.json"

# Units scale.py already converts exactly (cups, tbsp, oz, a "serving"...)
# -- CAUGHT LIVE testing on real celery data: "Celery, raw" has both a
# "cup chopped" row AND three sized "stalk" rows, and picking the first
# one found (no filter at all) chose "cup chopped" -- a unit that needed
# no USDA lookup in the first place, scale.py already has it. This whole
# module only exists for the OTHER kind: a unit with no fixed weight of
# its own, which "cup"/"tbsp" are not.
_ALREADY_HANDLED = {
    "cup", "cups", "tbsp", "tablespoon", "tablespoons", "tsp", "teaspoon",
    "teaspoons", "oz", "ounce", "ounces", "lb", "lbs", "pound", "pounds",
    "pint", "pints", "quart", "quarts", "gallon", "gallons", "ml", "l",
    "milliliter", "milliliters", "liter", "liters", "fl", "serving", "nlea",
}
_SIZE_WORDS = {"small", "medium", "large", "extra", "jumbo", "baby"}


def _load() -> dict:
    try:
        import json
        return json.loads(BASELINE_FILE.read_text("utf-8"))
    except (FileNotFoundError, ValueError):
        return {}


def _save(data: dict) -> None:
    import json
    BASELINE_FILE.parent.mkdir(parents=True, exist_ok=True)
    temp = BASELINE_FILE.with_suffix(".part")
    temp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
    temp.replace(BASELINE_FILE)


def get(food_id) -> "dict | None":
    return _load().get(str(food_id))


def set_baseline(food_id, unit: str, grams: float, source: str = "manual", edited: bool = True) -> None:
    # NOT named set() -- CAUGHT LIVE (2026-08-25): this module's own
    # _best_portion() needs the builtin set() a few lines below, and a
    # module-level function named `set` shadows the builtin for every
    # call inside THIS FILE, not just from outside it. foodphotos.py/
    # toolimages.py get away with the same bare `set()` name only because
    # neither of them ever needs the real builtin anywhere in its own code.
    data = _load()
    data[str(food_id)] = {"unit": unit, "grams": grams, "source": source, "edited": edited}
    _save(data)


def all_baselines() -> dict:
    """The whole file, for /admin's own management page."""
    return _load()


# ---------------------------------------------------------------------------
#  Importing SR Legacy's own portion table -- run once, or re-run any
#  time to fill in newly-added foods (never touches an "edited": true one).
# ---------------------------------------------------------------------------
def _read_raw() -> tuple:
    """(fdc_id -> description, fdc_id -> [{"modifier", "grams"} per ONE
    unit]). Read fresh from RAW_DIR's CSVs every call -- this only ever
    runs from an explicit admin action, not a hot path."""
    foods = {}
    with (RAW_DIR / "food.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            foods[row["fdc_id"]] = row.get("description") or ""

    portions: dict = {}
    with (RAW_DIR / "food_portion.csv").open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            modifier = (row.get("modifier") or "").strip()
            if not modifier or modifier.lower() == "undetermined":
                continue
            try:
                grams = float(row.get("gram_weight") or 0)
                amount = float(row.get("amount") or 1) or 1
            except ValueError:
                continue
            if grams <= 0:
                continue
            # Per ONE unit, not per whatever amount USDA happened to
            # measure ("2 stalks = 75g" -> "1 stalk = 37.5g") -- a
            # recipe's own ingredient line already carries its own count.
            portions.setdefault(row["fdc_id"], []).append(
                {"modifier": modifier, "grams": grams / amount})
    return foods, portions


def _best_portion(rows: list) -> "dict | None":
    """One representative entry per food -- a real COUNT unit ("stalk",
    "clove", "slice") over anything scale.py already converts on its own
    ("cup chopped", "tbsp"), and within that, "medium" over a small/large
    variant when a food has several sizes, else the plain unqualified
    noun, else whatever's left. None if every row is something scale.py
    already handles -- correctly no baseline, not a wrong one."""
    countable = [r for r in rows
                if not (set(r["modifier"].lower().replace(",", " ").split()) & _ALREADY_HANDLED)]
    if not countable:
        return None
    medium = [r for r in countable if "medium" in r["modifier"].lower()]
    if medium:
        return medium[0]
    plain = [r for r in countable if not (set(r["modifier"].lower().split()) & _SIZE_WORDS)]
    return (plain or countable)[0]


def build_baselines(tandoor_foods: list) -> dict:
    """Match every current Tandoor food against SR Legacy's portion table
    by name, keep the single best count-unit candidate for each. Returns
    a tally -- written / left alone because already hand-edited / no
    USDA match found (a "box of mangoes" style food, correctly left for
    Jason to fill in himself)."""
    foods, portions = _read_raw()
    existing = _load()
    written, skipped_edited, no_match = 0, 0, 0
    for tf in tandoor_foods:
        fid = str(tf.get("id"))
        if existing.get(fid, {}).get("edited"):
            skipped_edited += 1
            continue
        name = (tf.get("name") or "").strip()
        query_words = nutrition._significant_words(name)
        if not query_words:
            no_match += 1
            continue
        # (score, extra_words) -- SAME tie-break as usda_local.search()'s
        # own sort: highest score first, then FEWEST extra words. Without
        # the tie-break, "celery" matched "Celery, cooked, boiled,
        # drained, with salt" over the plain "Celery, raw" (both score
        # the same on word overlap alone; iteration order picked the
        # first one found, which happened to be the wrong one).
        best_fdc, best_key = None, None
        for fdc_id, rows in portions.items():
            score, extra = usda_local._weighted_score(query_words, foods.get(fdc_id, ""))
            if score == 0:
                continue
            key = (-score, extra)
            if best_key is None or key < best_key:
                best_fdc, best_key = fdc_id, key
        chosen = _best_portion(portions.get(best_fdc, [])) if best_fdc else None
        if not chosen:
            no_match += 1
            continue
        existing[fid] = {
            "unit": chosen["modifier"], "grams": round(chosen["grams"], 1),
            "source": f"USDA SR Legacy ({foods.get(best_fdc)})", "edited": False,
        }
        written += 1
    _save(existing)
    return {"written": written, "skipped_edited": skipped_edited, "no_match": no_match}


_SERVINGS_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:-|to|–)?\s*(\d+(?:\.\d+)?)?\s*"
    r"(?:serving|portion|person|people|serves)", re.I)
_LEADING_NUM = re.compile(r"^\s*(\d+(?:\.\d+)?)")
_SERVES_FIRST_RE = re.compile(
    r"\b(?:serves|feeds|yields?|makes)\s+(?:about\s+|approx\.?\s+)?"
    r"(\d+(?:\.\d+)?)\s*(?:-|to|–)?\s*\d*\s*"
    r"(?:serving|portion|person|people)?", re.I)


def servings_in(text: str) -> int:
    """How many servings a yields line means. 0 when it does not say.

    WHY THIS EXISTS. Kitchen read the serving count only from Tandoor,
    whose library is empty, so every recipe reported 0 servings while its
    own yields field said "4 serving(s)" -- and the portion buttons could
    not scale anything. Caught on recipe 111.

    A RANGE TAKES THE LOWER NUMBER. "4-6 servings" becomes 4, because the
    number is divided INTO the nutrition totals: taking 6 would quietly
    understate what is on the plate, and understating sodium in a dish
    already at 139% of a day's worth is the wrong way to be wrong.

    Returns 0 rather than guessing 1. A wrong divisor is worse than a
    missing one -- 1 would silently present the whole pot as a portion.
    """
    t = (text or "").strip()
    if not t:
        return 0
    # "Serves 6" / "Feeds 4" -- the word comes FIRST in these, so the
    # number-then-word pattern below misses them entirely. Checked before
    # it, because "Serves 4-6" would otherwise fall through to the range
    # branch and read the 4 by luck rather than by rule.
    m = _SERVES_FIRST_RE.search(t)
    if m:
        try:
            return max(0, int(float(m.group(1))))
        except (TypeError, ValueError):
            return 0
    m = _SERVINGS_RE.search(t)
    if m:
        try:
            return max(0, int(float(m.group(1))))
        except (TypeError, ValueError):
            return 0
    # "4" or "4 " on its own is a serving count often enough to accept;
    # anything with a unit attached ("500 g", "2 loaves") is not, and is
    # left at 0 rather than divided by a weight.
    m = _LEADING_NUM.match(t)
    if m and len(t) <= 3:
        try:
            return max(0, int(float(m.group(1))))
        except (TypeError, ValueError):
            return 0
    return 0
