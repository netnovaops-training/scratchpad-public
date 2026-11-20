"""Real nutrition facts per food, from USDA FoodData Central.

WHY A LOOKUP PER FOOD, NOT PER RECIPE
--------------------------------------
"Milk" has one nutrition profile whether it is in a smoothie or a béchamel.
Looking it up once, keeping the baseline, and then doing arithmetic for every
recipe that uses it is both cheaper (one API call per food, not per recipe)
and more honest (the number never disagrees with itself between two recipes
that both use "milk"). This module only ever answers "what is in 100g of
this food" -- turning that into "what is in this recipe" is scale.py's job
(recipe.py's own arithmetic against the baseline stored on each food).

WHY USDA FIRST, AND A MODEL ONLY AS A LAST RESORT
----------------------------------------------------
A model asked "how much iron is in flour" will answer confidently and can be
wrong by a large margin -- it has seen the number in training data, not
measured it. USDA FoodData Central is lab-measured (Foundation / SR Legacy
datasets) or a manufacturer's own label (Branded). Foundation/SR Legacy is
asked for first and preferred; Branded is what is left when a food is more
specific than USDA's generic library covers.

Some foods genuinely have no USDA match -- a typo, something too specific
("Grandma's sauce"), a brand USDA has never catalogued. `llm_guess()` exists
for exactly those, and only those: nutrition_fix.py tries the real lookup
first and falls back to a guess rather than leaving the food with nothing.
Every guess is stamped `"dataType": "LLM estimate"` and a `source` string
that says plainly it is not measured -- the one thing this module must never
do is let a guess look as trustworthy as a lab measurement.

WHY THE FREE KEY, NOT DEMO_KEY
--------------------------------
DEMO_KEY is rate-limited to 10 requests an hour. A library of 689 foods at
10/hour is three days of nothing else happening. A real key
(fdc.nal.usda.gov/api-key-signup.html, instant, free) raises that to 1000/hour.
"""
from __future__ import annotations

import os
import re

import httpx

from . import core, llm

BASE = "https://api.nal.usda.gov/fdc/v1"
API_KEY = os.getenv("KITCHEN_USDA_KEY", "")
TIMEOUT = 20.0

# CAUGHT LIVE (2026-08-24): a real recipe (nonalcoholic mojito) came out at
# 1991.7 calories -- root cause traced to search() blindly trusting
# foods[0] with no relevance check at all. "loosely packed fresh mint
# leaves plus 2 sprigs" (a garbled, verbose "food name" -- itself a
# symptom of a poor upstream ingredient parse) matched "Infant Formula,
# GERBER GOOD START" (492 kcal/100g); a bare "ice" matched "Ice cream
# sandwich" (237 kcal/100g) because USDA's own library has no plain-ice
# entry at all; "nonalcoholic rum" matched real 80-proof rum because
# "nonalcoholic" shares no words with any USDA entry describing an actual
# mocktail spirit. Three different failure shapes, three different fixes
# below -- no single check catches all of them.

# Fix 1: a handful of common ingredients where USDA's own search is
# reliably wrong (their library skews toward prepared/branded foods, not
# raw pantry items) get a hardcoded near-zero baseline instead of ever
# being searched.
_ZERO_CALORIE_OVERRIDES = {
    "ice", "ice cubes", "ice cube", "crushed ice", "water", "tap water",
    "filtered water", "cold water", "hot water", "warm water", "iced water",
    "still water", "sparkling water", "club soda", "seltzer",
    "seltzer water", "soda water",
}

# Fix 2: "nonalcoholic X" / "non-alcoholic X" / "alcohol-free X" -- USDA
# has no mocktail-spirit entries, so a search for these ALWAYS lands on
# the real, alcoholic product (a mojito recipe using "nonalcoholic rum"
# has no alcohol in it at all, but would otherwise be costed as if it
# did). Caught here and routed straight to llm_guess, which can reason
# about "this is a rum-flavored, alcohol-free syrup" instead of matching
# the literal word "rum".
_NONALCOHOLIC_RE = re.compile(r"\b(non[\s-]?alcoholic|alcohol[\s-]?free)\b", re.I)

_STOPWORDS = {
    "a", "an", "and", "or", "of", "the", "to", "for", "in", "on", "with",
    "plus", "into", "about", "extra", "each", "some", "any",
}


def _significant_words(text: str) -> set:
    """Lowercased words worth comparing for relevance -- short/common
    words are dropped since they overlap by accident (e.g. "ice" alone
    would "match" almost anything containing "ice cream")."""
    words = re.findall(r"[a-z']+", (text or "").lower())
    return {w for w in words if len(w) >= 4 and w not in _STOPWORDS}


def _relevance_score(query_words: set, description: str) -> int:
    # Substring containment, not exact word equality -- "mint" needs to
    # score against "Peppermint, fresh" (one compound word in USDA's own
    # naming), which a plain set intersection would never catch.
    desc_words = _significant_words(description)
    return sum(1 for qw in query_words
              for dw in desc_words if qw in dw or dw in qw)


# Fix 3: "loosely packed fresh mint leaves plus 2 sprigs" -- a query THIS
# verbose returns zero mint-related candidates from USDA at all (checked
# live: top 5 are all unrelated leaf vegetables), so no amount of scoring
# the returned candidates can find the real match; the fix has to be a
# second, simpler query. Cut at the first clause boundary (a comma, or a
# "plus"/"for"/"with" aside -- these consistently introduce a SEPARATE
# garnish/serving note, not more of the food itself) and drop a small set
# of descriptor words that dilute the search without being the food.
_CLAUSE_BREAK_RE = re.compile(r"\s*,|\bplus\b|\bfor\b|\bwith\b", re.I)
_QUERY_FILLER_WORDS = {
    "loosely", "packed", "sprigs", "sprig", "garnish", "whole", "half",
    "quartered", "juiced", "chunks", "pieces", "sliced", "chopped",
    "fresh", "extra",
    # CAUGHT LIVE: "leaves"/"leaf" seemed like the head noun to keep, but
    # USDA's own search weights it so heavily that "mint leaves" returns
    # the same leaf-VEGETABLE results as "leaves" alone (drumstick,
    # amaranth, broccoli...) with no mint entry anywhere in the top 5 --
    # only the bare herb name ("mint") actually surfaces the real herb.
    "leaves", "leaf",
}


def _simplify_food_query(name: str) -> str:
    head = _CLAUSE_BREAK_RE.split(name, maxsplit=1)[0]
    words = [w for w in head.split() if w.lower() not in _QUERY_FILLER_WORDS]
    return " ".join(words).strip()


class NutritionError(Exception):
    """Never actually raised out of search() -- kept for callers that want
    to distinguish "not configured" from "looked, found nothing" if they
    ever need to. search() itself always returns None on any failure; a
    missing nutrition fact is not a reason to fail an import."""


def configured() -> bool:
    return bool(API_KEY)


# FDC's own nutrientName strings, matched by substring (case-insensitive) in
# priority order. `unit` pins which of two same-named entries to take --
# FDC lists Energy in both KCAL and kJ as separate rows.
_MACRO = [
    ("calories", "Energy", "KCAL"),
    ("protein_g", "Protein", None),
    ("fat_g", "Total lipid (fat)", None),
    ("saturated_fat_g", "Fatty acids, total saturated", None),
    ("carbs_g", "Carbohydrate, by difference", None),
    ("fiber_g", "Fiber, total dietary", None),
    ("sugar_g", "Sugars, total", None),
    ("sodium_mg", "Sodium, Na", None),
    ("cholesterol_mg", "Cholesterol", None),
]
_MICRO = [
    ("vitamin_a_ug", "Vitamin A, RAE", None),
    ("vitamin_c_mg", "Vitamin C, total ascorbic acid", None),
    ("vitamin_d_ug", "Vitamin D (D2 + D3)", None),
    ("vitamin_e_mg", "Vitamin E (alpha-tocopherol)", None),
    ("vitamin_k_ug", "Vitamin K (phylloquinone)", None),
    ("vitamin_b6_mg", "Vitamin B-6", None),
    ("vitamin_b12_ug", "Vitamin B-12", None),
    ("folate_ug", "Folate, total", None),
    ("calcium_mg", "Calcium, Ca", None),
    ("iron_mg", "Iron, Fe", None),
    ("magnesium_mg", "Magnesium, Mg", None),
    ("potassium_mg", "Potassium, K", None),
    ("zinc_mg", "Zinc, Zn", None),
]
ALL_KEYS = _MACRO + _MICRO


def _extract(nutrients: list) -> dict:
    out = {}
    for key, wanted_name, wanted_unit in ALL_KEYS:
        for n in nutrients:
            name = n.get("nutrientName") or ""
            if wanted_name.lower() not in name.lower():
                continue
            if wanted_unit and (n.get("unitName") or "").upper() != wanted_unit:
                continue
            value = n.get("value")
            if value is not None:
                out[key] = {"value": round(float(value), 3),
                           "unit": n.get("unitName")}
            break
    return out


_ZERO_NUTRIENTS = {"calories": {"value": 0.0, "unit": "KCAL"}}


async def search(food_name: str) -> dict | None:
    """Nutrition per 100g for the best match to this food name.

    Never raises. Returns None if not configured, nothing matched, or the
    request failed for any reason -- this is always a best-effort
    enrichment, never something an import should fail over.
    """
    name = (food_name or "").strip()
    if not configured() or not name:
        return None

    lowered = name.lower()
    if lowered in _ZERO_CALORIE_OVERRIDES:
        return {"source": "Known near-zero-calorie ingredient (not searched)",
               "fdcId": None, "matchedName": name, "dataType": "override",
               "per": "100g", "nutrients": dict(_ZERO_NUTRIENTS)}
    if _NONALCOHOLIC_RE.search(name):
        # USDA's library has no mocktail-spirit entries -- every candidate
        # it would return is the REAL, alcoholic product. Rather than cost
        # a mojito as if it had actual rum in it, skip straight to a
        # model's estimate, which can reason about "alcohol-free" instead
        # of matching on the word "rum" alone.
        return None

    query_words = _significant_words(name)
    queries = [name]
    simplified = _simplify_food_query(name)
    if simplified and simplified.lower() != lowered:
        queries.append(simplified)

    try:
        foods_by_id: dict = {}
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            for q in queries:
                r = await client.get(f"{BASE}/foods/search", params={
                    "query": q, "api_key": API_KEY, "pageSize": 5,
                    "dataType": "Foundation,SR Legacy"})
                found = (r.json().get("foods") or []) if r.status_code == 200 else []
                if not found:
                    # Nothing in USDA's generic library -- try Branded, the
                    # catch-all for anything more specific ("kosher salt"
                    # over plain "salt", say).
                    r = await client.get(f"{BASE}/foods/search", params={
                        "query": q, "api_key": API_KEY, "pageSize": 5,
                        "dataType": "Branded"})
                    found = (r.json().get("foods") or []) if r.status_code == 200 else []
                for f in found:
                    foods_by_id.setdefault(f.get("fdcId"), f)
        foods = list(foods_by_id.values())
        if not foods:
            return None

        # CAUGHT LIVE: taking foods[0] unconditionally is how "loosely
        # packed fresh mint leaves plus 2 sprigs" ended up matched to
        # "Infant Formula, GERBER GOOD START" -- USDA's own relevance
        # ranking is not trustworthy for a query this far from a clean
        # food name, and its own search sometimes returns zero
        # mint-related candidates for a query this verbose no matter how
        # they're ranked, which is why a SECOND, simplified query
        # (_simplify_food_query) is folded into the same candidate pool
        # above rather than only re-scoring the first query's results.
        # Score every candidate by real word overlap against the ORIGINAL
        # name, and only trust one that shares an actual word with it --
        # if query_words itself is empty (a very short/common name, under
        # the 4-letter significance floor), there is nothing to score
        # against, so fall back to trusting USDA's own top result rather
        # than discarding every short ingredient name outright.
        if query_words:
            scored = sorted(foods, key=lambda f: _relevance_score(query_words, f.get("description") or ""), reverse=True)
            best = scored[0]
            if _relevance_score(query_words, best.get("description") or "") == 0:
                return None
        else:
            best = foods[0]

        nutrients = _extract(best.get("foodNutrients") or [])
        if not nutrients:
            return None
        return {
            "source": "USDA FoodData Central",
            "fdcId": best.get("fdcId"),
            "matchedName": best.get("description"),
            "dataType": best.get("dataType"),
            "per": "100g",
            "nutrients": nutrients,
        }
    except httpx.RequestError:
        return None


_GUESS_KEYS = [k for k, _, _ in _MACRO]  # micronutrients are not asked for --
# a model's confidence drops fast past the everyday macros, and a wrong
# vitamin number is worse than no vitamin number.


async def llm_guess(food_name: str) -> dict | None:
    """A model's best estimate of per-100g macros -- used only when USDA has
    no match at all. See the module docstring for why this exists and why it
    is limited to this one case. Never raises: a bad or unparseable guess
    returns None, the same as "nothing found", rather than something wrong
    dressed up as an answer.
    """
    name = (food_name or "").strip()
    if not name:
        return None
    system = (
        "You estimate typical nutrition facts per 100g of a food, for a "
        "home cook's reference -- not medical or lab-grade use. Give "
        "realistic numbers close to what published nutrition data would "
        "show. If this is not a real, edible food (a kitchen tool, a "
        "brand name with no food attached, gibberish), say so honestly "
        "rather than inventing numbers."
    )
    user = (
        f"Food: {name}\n\n"
        'Return JSON: {"is_food": true or false, "calories": number, '
        '"protein_g": number, "fat_g": number, "carbs_g": number, '
        '"fiber_g": number, "sugar_g": number, "sodium_mg": number} -- '
        "all per 100g. Omit any field you cannot reasonably estimate."
    )
    try:
        data = await llm.chat_json(system, user, model=core.LLM_LABEL, max_tokens=300)
    except llm.LLMError:
        return None
    if not isinstance(data, dict) or data.get("is_food") is False:
        return None

    nutrients = {}
    for key in _GUESS_KEYS:
        value = data.get(key)
        if not isinstance(value, (int, float)):
            continue
        unit = "MG" if key.endswith("_mg") else ("KCAL" if key == "calories" else "G")
        nutrients[key] = {"value": round(float(value), 2), "unit": unit}
    if not nutrients:
        return None

    return {
        "source": "LLM estimate (not measured -- USDA had no match)",
        "matchedName": name,
        "dataType": "LLM estimate",
        "per": "100g",
        "nutrients": nutrients,
    }


async def health() -> tuple[bool, str]:
    """Used by the status line. Never raises."""
    if not configured():
        return False, "no KITCHEN_USDA_KEY set"
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{BASE}/foods/search", params={
                "query": "water", "api_key": API_KEY, "pageSize": 1})
        if r.status_code == 403:
            return False, "USDA rejected the key"
        if r.status_code == 429:
            return False, "rate-limited"
        if r.status_code >= 400:
            return False, f"USDA said {r.status_code}"
        return True, "reachable"
    except Exception as e:  # noqa: BLE001
        return False, e.__class__.__name__
