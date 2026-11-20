"""Estimating a recipe's nutrition from its ingredients' USDA baselines.

WHY THIS IS ARITHMETIC AND NOT ANOTHER LOOKUP
------------------------------------------------
nutrition.py looks up a food ONCE, per 100g. This turns that baseline into
"how much of X is actually in THIS recipe": multiply the per-100g value by
the ingredient's weight in grams, divide by 100, sum every ingredient. No
model, no second API call, no re-lookup for a food already known.

WHY THIS IS AN ESTIMATE, SAID PLAINLY
----------------------------------------
Converting "2 cups flour" into grams needs a density, and flour, sugar and
oil are not the same weight per cup. Every volume unit here is converted as
if it were water (1 cup ~= 236.6g) -- close for a liquid, off by a real
margin for something like flour (actually ~125g/cup). Good enough for
"roughly how much protein is in this"; not a lab result. The caller is
handed how many ingredients actually contributed so it can say so.

Ingredients with a unit this module cannot convert (a whole egg, a clove of
garlic, "to taste") are skipped, not guessed -- their contribution is simply
absent from the total, which is more honest than inventing a gram weight.
"""
from __future__ import annotations

# Every volume converted to grams AS IF IT WERE WATER (density 1.0). This is
# the one approximation the whole module rests on -- see the docstring.
_GRAMS = {
    "gram": 1.0, "grams": 1.0, "g": 1.0,
    "kilogram": 1000.0, "kilograms": 1000.0, "kg": 1000.0,
    "ounce": 28.35, "ounces": 28.35, "oz": 28.35,
    "pound": 453.6, "pounds": 453.6, "lb": 453.6, "lbs": 453.6,
    "milliliter": 1.0, "milliliters": 1.0, "ml": 1.0,
    "liter": 1000.0, "liters": 1000.0, "l": 1000.0,
    "teaspoon": 4.93, "teaspoons": 4.93, "tsp": 4.93,
    "tablespoon": 14.79, "tablespoons": 14.79, "tbsp": 14.79,
    "cup": 236.6, "cups": 236.6,
    "fluid ounce": 29.57, "fluid ounces": 29.57, "fl oz": 29.57,
    "pint": 473.2, "pints": 473.2,
    "quart": 946.4, "quarts": 946.4,
    "gallon": 3785.4, "gallons": 3785.4,
}

# nutrition.py's keys -> Mealie's own Nutrition field names, checked against
# its schema. Micronutrients have no home here -- Mealie's `nutrition` object
# is macro-only; see nutrition_fix.py for where the micro values actually go
# (each food's own `extras`, not the recipe).
_MEALIE_FIELD = {
    "calories": "calories",
    "carbs_g": "carbohydrateContent",
    "cholesterol_mg": "cholesterolContent",
    "fat_g": "fatContent",
    "fiber_g": "fiberContent",
    "protein_g": "proteinContent",
    "saturated_fat_g": "saturatedFatContent",
    "sodium_mg": "sodiumContent",
    "sugar_g": "sugarContent",
}


def grams_for(quantity: float, unit_name: str) -> float | None:
    """`quantity` of `unit_name` -> grams, or None if this module has no
    conversion for that unit (countable units: egg, clove, pinch, ...)."""
    per = _GRAMS.get((unit_name or "").strip().lower())
    if per is None or not quantity:
        return None
    return quantity * per


def totals_for(ingredients: list, baselines: dict) -> tuple[dict, int, int]:
    """ingredients: Mealie's recipeIngredient list (quantity/unit/food).
    baselines: food name (lowercased) -> nutrition.search()'s return shape.

    Returns (totals, ingredients_used, ingredients_total) where totals is
    keyed by nutrition.py's own names (calories, protein_g, vitamin_c_mg,
    ...) -- EVERY nutrient a baseline had, not just the macro subset Mealie's
    own nutrition field can hold. estimate() below narrows this to that
    subset for Mealie; daily_values() in this module builds Kitchen's own
    chart from the full thing. An empty dict means nothing could be
    estimated -- too few ingredients had both a known baseline and a
    convertible unit to be worth showing.
    """
    totals: dict = {}
    used = 0
    total = len(ingredients)
    for ing in ingredients:
        food_name = (ing.get("food") or {}).get("name", "")
        unit_name = (ing.get("unit") or {}).get("name", "")
        qty = ing.get("quantity") or 0
        base = baselines.get(food_name.strip().lower())
        if not base or not qty:
            continue
        grams = grams_for(qty, unit_name)
        if grams is None:
            continue
        used += 1
        scale = grams / 100.0
        for key, cell in (base.get("nutrients") or {}).items():
            totals[key] = totals.get(key, 0.0) + cell["value"] * scale
    return totals, used, total


def mealie_shape(totals: dict) -> dict:
    """totals_for()'s output, narrowed to the macro-only subset Mealie's own
    `nutrition` field can hold -- see _MEALIE_FIELD."""
    return {mealie_key: str(round(totals[key], 1))
           for key, mealie_key in _MEALIE_FIELD.items() if key in totals}


def estimate(ingredients: list, baselines: dict) -> tuple[dict, int, int]:
    """The Mealie-shaped subset of totals_for() -- see that function for the
    real arithmetic. Returns (mealie_nutrition_dict, used, total); an empty
    dict means nothing could be estimated at all.
    """
    totals, used, total = totals_for(ingredients, baselines)
    if used == 0:
        return {}, 0, total
    return mealie_shape(totals), used, total


# --- the %DV chart -------------------------------------------------------
#
# FDA's own reference amounts for a 2000-calorie diet (the same numbers
# printed on every Nutrition Facts label in the US, 2020 revision) -- not
# something this app invents or estimates. "Total Sugars" deliberately has
# no entry: the FDA does not publish a %DV for it either (only "Added
# Sugars" has one, and nothing here can tell added from naturally occurring
# sugar), so it is shown as a plain amount, never a bar claiming a
# reference that does not exist.
_DAILY_VALUE = [
    ("calories", "Calories", "kcal", 2000),
    ("protein_g", "Protein", "g", 50),
    ("fat_g", "Fat", "g", 78),
    ("saturated_fat_g", "Saturated fat", "g", 20),
    ("carbs_g", "Carbs", "g", 275),
    ("fiber_g", "Fiber", "g", 28),
    ("sodium_mg", "Sodium", "mg", 2300),
    ("cholesterol_mg", "Cholesterol", "mg", 300),
    ("vitamin_a_ug", "Vitamin A", "mcg", 900),
    ("vitamin_c_mg", "Vitamin C", "mg", 90),
    ("vitamin_d_ug", "Vitamin D", "mcg", 20),
    ("vitamin_e_mg", "Vitamin E", "mg", 15),
    ("vitamin_k_ug", "Vitamin K", "mcg", 120),
    ("vitamin_b6_mg", "Vitamin B6", "mg", 1.7),
    ("vitamin_b12_ug", "Vitamin B12", "mcg", 2.4),
    ("folate_ug", "Folate", "mcg", 400),
    ("calcium_mg", "Calcium", "mg", 1300),
    ("iron_mg", "Iron", "mg", 18),
    ("magnesium_mg", "Magnesium", "mg", 420),
    ("potassium_mg", "Potassium", "mg", 4700),
    ("zinc_mg", "Zinc", "mg", 11),
]
# Not in the FDA's %DV table at all -- shown as a plain amount underneath
# the chart rather than silently dropped.
NO_DAILY_VALUE = [("sugar_g", "Sugar", "g")]


def daily_values(totals: dict, servings: float = 1.0) -> list[dict]:
    """totals_for()'s output -> one row per nutrient that has an FDA %DV,
    scaled to PER SERVING (totals_for() sums the whole recipe) and sorted
    highest-%DV first, which is what makes a bar chart readable -- the
    nutrients this recipe actually delivers a lot of rise to the top instead
    of being buried in a fixed, alphabetical list of twenty rows.

    Each row: {key, label, unit, amount, pct} -- pct is None if this
    nutrient has no baseline at all in `totals` (not "0%", which would
    claim a measurement that was never made).
    """
    servings = servings if servings and servings > 0 else 1.0
    rows = []
    for key, label, unit, dv in _DAILY_VALUE:
        if key not in totals:
            continue
        amount = totals[key] / servings
        pct = round(amount / dv * 100)
        rows.append({"key": key, "label": label, "unit": unit,
                    "amount": round(amount, 1), "pct": pct})
    rows.sort(key=lambda r: r["pct"], reverse=True)
    return rows


# The macros somebody actually checks when they ask "how much am I making",
# not the full twenty-row micronutrient table -- see batch_totals() below.
_BATCH_MACROS = [("calories", "Calories", "kcal"), ("protein_g", "Protein", "g"),
                 ("fat_g", "Fat", "g"), ("carbs_g", "Carbs", "g")]


def batch_totals(totals: dict, native_servings: float, servings_now: float) -> list[dict]:
    """The core macros, added up for the BATCH you are actually making --
    servings_now servings' worth, not one serving.

    daily_values() above is deliberately per serving and does not change when
    the portion buttons are pressed: a serving is a serving whichever size
    batch it came out of, and %DV is a per-day reference that would be
    actively misleading applied to a whole pot (Vitamin A "800% of a day"
    when you have made eight servings does not mean what it looks like it
    means). This answers the different, real question -- if I am making
    eight of these right now, what does that add up to -- by scaling the
    WHOLE-RECIPE totals from however many it was written for to however many
    are actually being made this time.
    """
    if not totals or not native_servings or native_servings <= 0:
        return []
    if not servings_now or servings_now <= 0:
        servings_now = native_servings
    factor = servings_now / native_servings
    out = []
    for key, label, unit in _BATCH_MACROS:
        if key in totals:
            out.append({"key": key, "label": label, "unit": unit,
                        "amount": round(totals[key] * factor, 1)})
    return out


def extra_amounts(totals: dict, servings: float = 1.0) -> list[dict]:
    """Nutrients this app tracks but the FDA publishes no %DV for -- see
    NO_DAILY_VALUE. A plain per-serving amount, no bar."""
    servings = servings if servings and servings > 0 else 1.0
    out = []
    for key, label, unit in NO_DAILY_VALUE:
        if key in totals:
            out.append({"key": key, "label": label, "unit": unit,
                        "amount": round(totals[key] / servings, 1)})
    return out
