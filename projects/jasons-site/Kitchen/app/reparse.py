"""Re-parsing one recipe from its original source, with Phi-4 instead of
whatever the original import used. Started as a one-time bulk script
(2026-08-24, Jason's own request: "take from the sources from all the
links i have then have phi parse the inforamtion"), then Jason asked to
take it over by hand instead: "I'll parse the rest manually just give me
the ability to do so in /admin with a done and undone." Promoted from a
throwaway script to a real module for that reason -- see main.py's
/internal/reparse-* routes and the admin panel's own page for the
done/undone list and the per-recipe trigger.

WHY THIS ALWAYS TAKES THE MODEL PATH, NEVER JSON-LD
-------------------------------------------------------
Several already-migrated recipes (the nonalcoholic mojito being the one
actually found and checked) came from a source page whose JSON-LD recipe
data was thin: one combined instruction block instead of real steps, no
banner image, etc. importer.from_url() prefers that same JSON-LD data
when a page has it and never falls through to the model -- so simply
calling from_url() again on these recipes would reproduce the exact same
thin result. This module instead ALWAYS takes the model path (the same
code importer.from_url() uses when there is no JSON-LD, just never
short-circuited by it).

STEP PICTURES ARE DELIBERATELY LEFT ALONE -- NOT REATTACHED. main.py's
own _reattach_step_files() keys strictly by step INDEX ("step-3"), not
content; a re-parse very likely changes how many steps there are and
what each one says, so index-based reattachment here would silently put
the WRONG photo on the wrong new step rather than no photo at all -- a
worse failure than a blank step, and quieter. Jason's own words going
into this: "if i have to redo any images or tools let me... we can talk
about this further if needed" -- so any step missing a picture after
this is expected, and the existing idle-artist job (main.py's
_draw_missing/_draw_each) already redraws steps that have none on its
own regular pass; nothing new needs building for that. The recipe's own
HERO image (not step-indexed) is still refreshed here, since that one
isn't affected by this failure mode.
"""
import asyncio

import httpx

from . import core, crawl, detail, importer, llm, scale, store, tandoor, vram

DONE_MARKER = "re-parsed from the source page with a bigger model"
# CAUGHT LIVE (2026-08-24), Jason's own example recipe (a hand-typed one,
# "Nanny's cheese scones"): it has no source link at all, so there is
# nothing for _reparse_one() above to re-fetch -- status() used to skip
# these entirely, and this whole module could never reach them. See
# _detail_only() below.
DETAIL_ONLY_MARKER = "details added -- no source page to re-read"


async def _reparse_one(url: str) -> crawl.Recipe:
    """Same as importer.from_url()'s own model path, but ALWAYS taken --
    see the module docstring for why."""
    final_url, page_html = await crawl.fetch(url)
    reader = crawl.read(page_html)
    images = crawl.harvest_images(reader, final_url)
    text = crawl.page_text(reader)
    if len(text) < 200:
        raise core.Blocked("Almost no text on that page now.")

    data = await llm.chat_json(
        importer.READ_SYSTEM + importer.READ_SHAPE, text,
        model=core.LLM_READ, temperature=0.1, max_tokens=3000)
    if not data.get("found", True) or not data.get("ingredients"):
        raise core.Blocked("No recipe found on the page this time.")
    hero = reader.meta.get("og:image", "") or (images[0]["src"] if images else "")
    ingredients = [str(i).strip() for i in data.get("ingredients") or [] if str(i).strip()]
    steps = [importer._denumber(str(s)) for s in data.get("steps") or [] if str(s).strip()]
    ingredients = await crawl.fill_ingredients_from_steps(ingredients, steps)

    # importer._add_detail() -- the same "make it more verbose, convert
    # to imperial" pass every fresh import now gets (Jason's own request,
    # 2026-08-24), applied here too so re-parsing an old recipe actually
    # upgrades it rather than just re-fetching the same thin steps with a
    # bigger model. Runs under the CALLER's vram.parsing() span (see
    # reparse_one_recipe below), not a new one of its own.
    return await importer._add_detail(crawl.Recipe(
        title=str(data.get("title") or "").strip() or "Untitled",
        source=final_url,
        image=hero,
        images=importer.drop_hero(images, hero),
        yields=str(data.get("yields") or "").strip(),
        prep=str(data.get("prep") or "").strip(),
        cook=str(data.get("cook") or "").strip(),
        total=str(data.get("total") or "").strip(),
        ingredients=ingredients,
        steps=steps,
        notes=str(data.get("notes") or "").strip(),
        how=DONE_MARKER,
    ))


async def _detail_only(old_recipe: dict) -> dict:
    """Same detail-adding pass as _reparse_one()/importer._add_detail(),
    for a recipe with no source link to re-fetch -- nothing to re-read,
    so everything except ingredients/steps/how just stays what it
    already was."""
    ingredients = list(old_recipe.get("ingredients") or [])
    steps = await detail.elaborate(old_recipe.get("title") or "", ingredients,
                                   list(old_recipe.get("steps") or []))
    fresh = dict(old_recipe)
    fresh["ingredients"] = scale.imperial_ingredients(ingredients)
    fresh["steps"] = scale.imperial_steps(steps)
    fresh["how"] = DETAIL_ONLY_MARKER
    return fresh


async def _process_one(r: dict) -> "str | None":
    """One recipe, start to finish. Returns an error message on failure,
    None on success."""
    draft_id = r["id"]
    tandoor_id = r["tandoor_id"]
    old_recipe = r["recipe"]
    url = old_recipe.get("source")

    if not url:
        fresh = await _detail_only(old_recipe)
    else:
        fresh = None
        last_error = None
        for attempt in (1, 2):
            try:
                fresh = await _reparse_one(url)
                break
            except (llm.LLMError, httpx.RequestError) as e:
                last_error = str(e)
                if attempt == 2:
                    return f"Could not read it: {last_error}"
                await asyncio.sleep(5)
            except (core.Blocked, httpx.HTTPStatusError) as e:
                return f"Could not read it: {e}"
        if fresh is None:
            return last_error or "Could not read it."

    new_recipe = dict(old_recipe)
    # CAUGHT LIVE (2026-08-25): "tools" was never in this list -- Jason's
    # own report that recipe 28 still had none after a reparse, even
    # though labels.suggest_tools() (wired into _add_detail() the same
    # night) worked correctly when called directly. importer._add_detail
    # computed it fine; this loop just never carried it from `fresh`
    # onto what actually gets saved. Same real gap for
    # "_parse_ingredients" -- without it, tandoor._to_tandoor()'s own
    # parse/display split (see that function's own comment) silently
    # falls back to parsing the DISPLAY text, the exact corruption that
    # fix exists to prevent, on every reparse specifically (fresh
    # imports were never affected -- they hand crawl.Recipe straight
    # through, not through this copy loop).
    for field in ("title", "source", "image", "images", "yields",
                 "prep", "cook", "total", "ingredients", "steps",
                 "notes", "how", "tools", "_parse_ingredients"):
        new_recipe[field] = fresh.get(field)
    for photo_dict_key in ("mine", "drawn"):
        d = new_recipe.get(photo_dict_key)
        if isinstance(d, dict):
            new_recipe[photo_dict_key] = {
                k: v for k, v in d.items() if not k.startswith("step-")}
    new_recipe.pop("nutrition_full", None)
    new_recipe.pop("nutrition_servings", None)

    store.update_recipe(draft_id, new_recipe)
    try:
        await tandoor.update_recipe(tandoor_id, new_recipe, draft_id)
    except tandoor.TandoorError as e:
        return f"Read it fine, but the library would not take it: {e}"
    if new_recipe.get("image"):
        try:
            await tandoor.push_image_from_url(tandoor_id, new_recipe["image"])
        except tandoor.TandoorError:
            pass
    await tandoor.push_recipe_nutrition(tandoor_id)
    return None


async def reparse_one_recipe(tandoor_id) -> "str | None":
    """The single-recipe entry point /internal/reparse/{id} calls. Wraps
    its own vram.parsing() -- unlike the old batch script, there is no
    outer loop sharing one eviction any more, so each manual click pays
    its own model-load cost. That is the right trade now: Jason asked
    for this one recipe at a time, not a batch, so there is no "whole
    run" left to amortize the load across."""
    row = next((r for r in store.kept_with_ids() if str(r.get("tandoor_id")) == str(tandoor_id)), None)
    if not row:
        return "That recipe is not in the library."
    async with vram.parsing():
        return await _process_one(row)


def status() -> list:
    """Every kept recipe, and whether it has been re-parsed/detailed yet
    -- the done/undone list for /admin. Used to skip anything with no
    source link entirely; now includes those too (see DETAIL_ONLY_MARKER)
    with `has_source: false` so /admin can show a different button for
    them -- there is no page to re-read, only detail to add to what's
    already there."""
    rows = store.kept_with_ids()
    out = []
    for r in rows:
        recipe = r.get("recipe") or {}
        if not r.get("tandoor_id"):
            continue
        out.append({
            "tandoor_id": r["tandoor_id"], "title": recipe.get("title") or "Untitled",
            "done": recipe.get("how") in (DONE_MARKER, DETAIL_ONLY_MARKER),
            "has_source": bool(recipe.get("source")),
        })
    out.sort(key=lambda x: (x["done"], x["title"].lower()))
    return out
