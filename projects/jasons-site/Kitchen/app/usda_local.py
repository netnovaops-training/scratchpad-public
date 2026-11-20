"""USDA's own bulk data, kept locally -- Jason's own request (2026-08-24),
handing over the real download links: "can we use this link to grab food
items and nutritional values to compare against my models."

WHY LOCAL DATA BEATS THE LIVE SEARCH API FOR THIS
-----------------------------------------------------
nutrition.py's live search already had to grow a whole relevance-scoring
layer this session because USDA's own remote search ranking is not
trustworthy -- "mint" matched Infant Formula, "ice" matched an ice cream
sandwich, both confirmed live before that guard existed (see nutrition.py's
own comments). A local copy removes the remote ranking from the equation
entirely: every candidate is scored the SAME way (word overlap, see
nutrition.py's _relevance_score, reused here rather than duplicated) and
there is no server-side "best guess" to second-guess -- just every
Foundation/SR Legacy food, searched in full, every time.

TWO DATASETS BULK-IMPORTED, ONE KEPT ON STANDBY
----------------------------------------------------
USDA publishes Foundation Foods (real lab measurements, ~469 entries,
narrow -- mostly raw single ingredients) and SR Legacy (frozen since 2019,
~7,800 entries, broad staple-food coverage -- what nutrition.py's live
`dataType=Foundation,SR Legacy` search already prefers). Both fit easily
in this app's own database, imported once by build_from_csv().

Branded Foods is a THIRD dataset and is NOT bulk-imported: its own CSV
export is ~3GB with 1-2 million branded products, wildly out of
proportion to what a ~1500-ingredient home recipe library needs to have
resident in memory. Jason's own words (2026-08-24): "I don't need that
many branded products at all, however... can we keep them on standby and
then when we find something that fits it we just pull that data set? ...
if something asked for McCormick then we have that already in place."
So the raw CSVs live on disk (RAW_DIR_BRANDED, see below) untouched, and
branded_search() below does a real but ONE-TIME scan across them only
when a specific brand is actually needed -- the result is then cached
into this same usda_local table (usda_local_upsert, a real per-row
cache, unlike the bulk replace the other two datasets use), so the
SECOND time any recipe needs "McCormick" it is already there, instant,
no rescan.

THE JOIN, DONE ONCE AT IMPORT TIME, NOT PER LOOKUP
------------------------------------------------------
USDA's CSV export is relational (food.csv / food_nutrient.csv /
nutrient.csv, matching fdc_id and nutrient_id). build_from_csv() does that
join ONCE, producing exactly the same {value, unit} nutrient shape
nutrition.py's own _extract() produces from the live JSON API -- so
anything already reading a baseline (recipe_nutrition.py, iteminfo.py)
needs no changes at all to use a locally-sourced one.
"""
import csv
import json
import re

from . import core, foodmatch, nutrition, store

RAW_DIR = core.DATA_DIR / "usda_raw"
RAW_DIR_BRANDED = core.DATA_DIR / "usda_raw_branded"

# (source prefix, data_type label) -- matches the file names
# _seed_common_foods-style scripts and this session's own docker cp
# copied into RAW_DIR: foundation_food.csv/foundation_food_nutrient.csv/
# foundation_nutrient.csv, and the same with an sr_ prefix.
_SOURCES = [("foundation", "foundation_food"), ("sr", "sr_legacy_food")]


def _load_nutrient_id_map(path) -> dict:
    """nutrient_id -> our own key, by matching the SAME (name, unit) pairs
    nutrition.py's _extract() already matches against the live API's
    JSON -- one definition of "which nutrient is which" for both paths."""
    id_to_key = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            name = (row.get("name") or "").lower()
            unit = (row.get("unit_name") or "").upper()
            for key, wanted_name, wanted_unit in nutrition.ALL_KEYS:
                if wanted_name.lower() not in name:
                    continue
                if wanted_unit and unit != wanted_unit:
                    continue
                # First match wins, same rule as _extract()'s own
                # first-match-in-list behaviour -- avoids "Energy" also
                # capturing "Energy (Atwater General Factors)".
                id_to_key.setdefault(row["id"], key)
                break
    return id_to_key


def build_from_csv() -> dict:
    """Reads RAW_DIR's CSVs (see the docstring above) and replaces
    store's usda_local table wholesale. Returns a small report dict.
    Never raises past a missing file -- a partial import (Foundation
    only, say) is still useful, not a reason to give up entirely."""
    report = {"sources": {}, "total": 0}
    all_rows = []
    for prefix, data_type in _SOURCES:
        nutrient_csv = RAW_DIR / f"{prefix}_nutrient.csv"
        food_csv = RAW_DIR / f"{prefix}_food.csv"
        food_nutrient_csv = RAW_DIR / f"{prefix}_food_nutrient.csv"
        if not (nutrient_csv.exists() and food_csv.exists() and food_nutrient_csv.exists()):
            report["sources"][data_type] = "missing files, skipped"
            continue

        id_to_key = _load_nutrient_id_map(nutrient_csv)

        descriptions = {}
        with open(food_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get("data_type") != data_type:
                    continue
                descriptions[row["fdc_id"]] = row.get("description") or ""

        nutrients_by_food: dict = {}
        with open(food_nutrient_csv, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                fdc_id = row.get("fdc_id")
                if fdc_id not in descriptions:
                    continue
                key = id_to_key.get(row.get("nutrient_id"))
                if not key:
                    continue
                amount = row.get("amount")
                if not amount:
                    continue
                unit = next((u for k, _, u in nutrition.ALL_KEYS if k == key), None)
                if unit is None:
                    unit = "KCAL" if key == "calories" else ("MG" if key.endswith("_mg") else "UG" if key.endswith("_ug") else "G")
                nutrients_by_food.setdefault(fdc_id, {})[key] = {
                    "value": round(float(amount), 3), "unit": unit}

        count = 0
        for fdc_id, description in descriptions.items():
            nutrients = nutrients_by_food.get(fdc_id)
            if not nutrients:
                continue
            all_rows.append({"fdc_id": int(fdc_id), "description": description,
                            "data_type": data_type, "nutrients": nutrients})
            count += 1
        report["sources"][data_type] = f"{count} foods"
        report["total"] += count

    if all_rows:
        store.usda_local_replace_all(all_rows)
    return report


_cache: list = []


def _index() -> list:
    global _cache
    if not _cache:
        _cache = store.usda_local_all()
    return _cache


def _weighted_score(query_words: set, description: str) -> tuple:
    """(weighted score, extra words) for one candidate.

    CAUGHT LIVE testing this against a real recipe: nutrition.py's own
    substring-based _relevance_score (query_word in desc_word OR the
    reverse) exists specifically so "mint" can match "Peppermint" --  a
    real compound word, and the right call there. Applied to EVERY word
    without distinction, it also lets "chick" (from "CHICK-FIL-A")
    substring-match "chicken", and "salt" substring-match "saltines" --
    short, common words picking up spurious credit from unrelated
    products that just happen to contain them as a fragment. Confirmed
    live: "chicken breast" matched a Chick-fil-A sandwich, "salt" matched
    saltine crackers, both beating the plain entry on raw overlap count
    alone. Fix: an EXACT word match counts double a substring-only match,
    so two real word matches ("chicken" + "breast") reliably outweigh one
    real match plus one incidental substring fragment.
    """
    desc_words = nutrition._significant_words(description)
    # Singularized too, not just literal -- CAUGHT LIVE: "sugar" (query)
    # vs "sugars" (candidate) only substring-matched, not exact, so
    # "Sweeteners, sugar substitute, granulated, brown" (a literal exact
    # hit on "sugar") outscored the correct "Sugars, brown". Reuses
    # dedupe.py's own singularizer -- same "same word, different form"
    # problem that module already solved for food-name deduplication.
    desc_singular = {foodmatch._singular(w) for w in desc_words}
    score = 0
    matched = 0
    for qw in query_words:
        qw_s = foodmatch._singular(qw)
        exact = qw in desc_words or qw_s in desc_singular
        sub = any(qw in dw or dw in qw for dw in desc_words)
        if exact:
            score += 2
            matched += 1
        elif sub:
            score += 1
            matched += 1
    extra_words = len(desc_words) - matched
    return score, extra_words


def search(name: str) -> "dict | None":
    """Best local match for this food name, or None -- see
    _weighted_score() for how ties like "chicken breast" vs a fast-food
    sandwich get broken correctly."""
    name = (name or "").strip()
    if not name:
        return None
    query_words = nutrition._significant_words(name)
    if not query_words:
        return None
    scored = []
    for entry in _index():
        score, extra_words = _weighted_score(query_words, entry["description"])
        if score == 0:
            continue
        scored.append((score, extra_words, entry))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], t[1]))
    best = scored[0][2]
    return {
        "source": f"USDA FoodData Central ({best['data_type'].replace('_', ' ')}, local)",
        "fdcId": best["fdc_id"], "matchedName": best["description"],
        "dataType": best["data_type"], "per": "100g", "nutrients": best["nutrients"],
    }


# Words that describe HOW a food was cut/cooked, never WHAT it is -- same
# list dedupe.py already built and validated for exactly this distinction
# (identity words vs. everything else).
def _identity_words(text: str) -> set:
    return {w for w in nutrition._significant_words(text) if w not in foodmatch._TIER1_FILLER}


# NO AUTO-FIX HERE, ON PURPOSE -- history, not caution for its own sake.
# The first version of this function auto-applied anything where the
# cached match shared zero identity words with the query and the local
# match shared at least one. First run: 20 fixes, 5 of them wrong
# ("ginger, peeled, quartered" -> "Cucumber, peeled, raw" on "peeled"
# alone; "captain morgan spiced rum" -> "Peaches, spiced..." on "spiced"
# alone). Filtered prep words out and tried again: STILL wrong a second
# way ("salmon fillet" -> "Emu, fan fillet, raw" on "fillet"; "frozen ...
# shrimp" -> "Durian, raw or frozen" on "frozen"). Two refinement passes,
# two new failure shapes -- the pattern itself is the finding: exact-word
# identity matching on this kind of garbled, real-world ingredient text
# is not a problem a slightly better word list solves. Everything below
# goes to review now, full stop, same as dedupe.py's own tier-2 already
# does for food/tool grouping -- a model or a person judges identity, a
# word list only ever screens candidates.
def audit() -> dict:
    """Compares every cached food_baseline against this local dataset.
    Jason's own request (2026-08-24): "grab food items and nutritional
    values to compare against my models," then, once real discrepancies
    turned up, "both" -- auto-fix the obvious ones, queue the rest for
    review. The "obvious" tier was tried and removed -- see the comment
    above; every discrepancy now goes to `review`, for Jason's own call
    in /admin. Only ever flags a calorie count that already differs by
    more than 2x, so a cosmetically different but nutritionally similar
    match is left alone either way.
    """
    with store._db() as conn:
        rows = conn.execute("SELECT name, data FROM food_baselines").fetchall()

    review = []
    for row in rows:
        name = row["name"]
        try:
            cached = json.loads(row["data"])
        except (TypeError, ValueError):
            continue
        cached_cal = (cached.get("nutrients") or {}).get("calories", {}).get("value")
        if cached_cal is None or cached_cal == 0:
            continue
        local = search(name)
        if not local:
            continue
        local_cal = (local.get("nutrients") or {}).get("calories", {}).get("value")
        if local_cal is None:
            continue
        ratio = local_cal / cached_cal
        if not (ratio > 2 or ratio < 0.5):
            continue
        review.append({"name": name, "cached": cached, "local": local})
    store.nutrition_review_replace_all(review)
    return {"review": review}


def branded_search(query: str, brand_hint: str = "") -> "dict | None":
    """On-demand only -- see the module docstring's "kept on standby"
    section for why this never runs automatically for every ingredient,
    only when a name genuinely looks brand-specific (main.py/nutrition.py
    decide that, not this function). Two streamed passes over the raw
    CSVs, neither ever loaded whole into memory: first food.csv +
    branded_food.csv to find the best-matching product's fdc_id (scored
    the same weighted way search() already does), then a targeted scan
    of food_nutrient.csv for just that one id. Slow -- a real pass over
    ~2.8GB -- which is the correct trade for something used rarely rather
    than keeping 1-2 million products resident for everyone's sake.
    Caches the result via store.usda_local_upsert() on success, so this
    full scan never has to repeat for the same brand twice.
    """
    text = f"{brand_hint} {query}".strip()
    query_words = nutrition._significant_words(text)
    if not query_words:
        return None
    food_csv = RAW_DIR_BRANDED / "food.csv"
    branded_csv = RAW_DIR_BRANDED / "branded_food.csv"
    nutrient_csv = RAW_DIR_BRANDED / "nutrient.csv"
    food_nutrient_csv = RAW_DIR_BRANDED / "food_nutrient.csv"
    if not all(p.exists() for p in (food_csv, branded_csv, nutrient_csv, food_nutrient_csv)):
        return None

    brand_by_id = {}
    with open(branded_csv, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            brand_by_id[row["fdc_id"]] = f"{row.get('brand_owner') or ''} {row.get('brand_name') or ''}".strip()

    best = None
    best_key = (0, 10**9)
    with open(food_csv, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if row.get("data_type") != "branded_food":
                continue
            fdc_id = row["fdc_id"]
            full_text = f"{brand_by_id.get(fdc_id, '')} {row.get('description') or ''}"
            score, extra = _weighted_score(query_words, full_text)
            if score == 0:
                continue
            key = (score, -extra)
            if key > best_key:
                best_key = key
                best = {"fdc_id": fdc_id, "description": row.get("description") or "",
                       "brand": brand_by_id.get(fdc_id, "")}
    if not best:
        return None

    id_to_key = _load_nutrient_id_map(nutrient_csv)
    nutrients = {}
    with open(food_nutrient_csv, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if row.get("fdc_id") != best["fdc_id"]:
                continue
            key = id_to_key.get(row.get("nutrient_id"))
            amount = row.get("amount")
            if not key or not amount:
                continue
            unit = next((u for k, _, u in nutrition.ALL_KEYS if k == key), None) or \
                ("KCAL" if key == "calories" else "MG" if key.endswith("_mg") else
                 "UG" if key.endswith("_ug") else "G")
            nutrients[key] = {"value": round(float(amount), 3), "unit": unit}
    if not nutrients:
        return None

    full_name = f"{best['brand']}, {best['description']}".strip(", ")
    store.usda_local_upsert(int(best["fdc_id"]), full_name, "branded_food", nutrients)
    _cache.clear()  # next search()/audit() call re-reads, including this new row
    return {
        "source": "USDA FoodData Central (branded, pulled on demand)",
        "fdcId": best["fdc_id"], "matchedName": full_name,
        "dataType": "branded_food", "per": "100g", "nutrients": nutrients,
    }
