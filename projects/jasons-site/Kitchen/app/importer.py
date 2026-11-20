"""Turning a link into a recipe, and a recipe into a guide.

This is the orchestration layer. The interesting decisions are in the modules
it calls -- crawl.py decides how to read a page, scale.py decides how to
multiply a quantity, diagram.py decides what to draw. This file decides the
ORDER, and the order is the same argument made three times:

    do it in code if code can be exact, and only then ask the model.

    reading a page      JSON-LD, then the model            (crawl.py)
    scaling portions    arithmetic, never the model        (scale.py)
    substitutions       the table, then the model          (subs.py)
    step pictures       the page's photos, then a drawing  (diagram.py)

WHAT `how` IS FOR
-----------------
Every recipe carries a one-line account of where it came from, and the guide
page prints it. "read straight from the page's own recipe data" and "worked out
by the model from the page text" are very different levels of trust, and you
should not have to guess which one you are looking at while you are shopping.
"""
import re

import httpx

from . import britishisms, core, crawl, detail, diagram, labels, llm, scale, subs, vram, youtube

READ_SYSTEM = """You extract recipes from web pages. The page text you are
given is messy: navigation, adverts, comments, and a long personal story before
the recipe. Find the recipe in it.

Rules:
- Copy ingredients and steps VERBATIM where the page states them. Do not
  rewrite, do not tidy, do not convert units. You are transcribing, not
  improving.
- If the page does not state something, leave that field as an empty string.
  Never invent a cooking time, a temperature, or a serving count.
- If there is genuinely no recipe on this page, return {"found": false} and
  nothing else.
- Steps are whole instructions, one per array entry, in order. Strip any
  leading "1." or "Step 3:" numbering; the page numbers them itself.
- KEEP THE OVEN. If the page says to preheat, or gives a temperature, that is
  a step and it comes first. It is the one instruction people need before they
  start and the easiest one to drop when you are summarising."""

READ_SHAPE = """
Return JSON exactly like this:
{"found": true,
 "title": "",
 "yields": "",
 "prep": "", "cook": "", "total": "",
 "ingredients": ["", ""],
 "steps": ["", ""],
 "notes": ""}
"""

# CAUGHT LIVE (2026-08-25), Jason's own request: "use the internet and,
# YouTuber transcripts and make a helpful walkthrough for my mom." A
# SEPARATE prompt from READ_SYSTEM above, not a reuse of it -- that one's
# central rule is "copy VERBATIM... you are transcribing, not improving,"
# which makes no sense against a spoken transcript: there is no
# formatted ingredient list or numbered step to copy, only someone
# talking through what they're doing, often out of a tidy order, with
# filler, tangents, and no stated amount for things they're eyeballing.
YOUTUBE_SYSTEM = """You extract a recipe from a YouTube cooking video's own
spoken transcript (auto-generated captions -- unpunctuated, occasional
mis-heard words, no formatting at all).

Rules:
- There is no ingredient list or numbered steps to copy -- work them out from
  what the person actually SAYS while cooking, in the order they do it.
- An amount ONLY belongs in your answer if it was actually said out loud
  ("two tablespoons of butter", "a few cloves of garlic"). If they never
  give a quantity for something ("add some salt", "a scallion or two"),
  write the ingredient down WITHOUT inventing a number for it -- never
  guess a standard amount because it sounds normal for that ingredient.
- Turn the spoken narration into clear steps, faithful to what was
  actually said and done, not what a typical recipe for this dish usually
  says -- if they improvise or skip something a normal recipe would
  include, follow THEM, not the genre.
- Skip filler, small talk, sponsor reads, and asides that aren't part of
  actually making the dish.
- If a temperature, time, or oven setting is said out loud, keep it -- it
  is the easiest detail to lose when condensing spoken narration.
- Auto-generated captions sometimes mishear a word -- if something is
  clearly a mis-transcription given the cooking context (e.g. a
  homophone that makes no sense as an ingredient), use your best
  judgment on what was actually said, but do not invent content that
  was not being described at all.
- If this transcript is not really a single recipe being made start to
  finish (a compilation of several dishes, a discussion with no cooking,
  a video where the recipe is only mentioned, not shown), return
  {"found": false} rather than forcing a match to whichever dish sounds
  most complete."""


async def _add_detail(recipe: crawl.Recipe) -> crawl.Recipe:
    """Jason's own request (2026-08-24): "these are two simple to
    follow... make them more verbose... convert all metrics into
    imperial." Runs on EVERY import, both the JSON-LD fast path (a
    source's own thin one-line steps need this most) and the model
    path -- called once here so neither path can drift from the other.
    Unit conversion is pure code (scale.py) and runs SECOND, after
    detail.elaborate()'s own model call, so it also catches any metric
    mention the elaboration step introduced while explaining a method,
    not just what the source itself wrote.

    britishisms.py runs THIRD, same reasoning -- Jason's own follow-up
    request (2026-08-24, re-raised 2026-08-25 about a specific recipe):
    "there are some ingredients my mom would not know" ("rashers",
    "clear honey", "celery stick"). A naming problem, not a unit
    problem, so it is a separate, mechanical pass rather than folded
    into scale.py's own unit conversion.

    CAUGHT LIVE (2026-08-25): scale.imperial_line()'s own output shape,
    "14 oz (400g) flour", reads perfectly to a person but breaks
    tandoor.py's own ingredient parser -- it extracts ONE leading
    amount+unit and treats everything after that as the food name, so it
    has no idea "(400g)" is a second, parenthetical unit rather than
    part of the food's identity. Every converted ingredient line was
    creating a food named "(400g) flour" instead of "flour". Stashing
    the PRE-conversion lines here, before scale.py/britishisms.py touch
    them, so tandoor.py's own _to_tandoor() can parse food/amount/unit
    from the ORIGINAL, parser-friendly text while still showing the
    nicer converted text as each ingredient's own original_text (see
    that function's own comment on how the two get recombined)."""
    recipe["_parse_ingredients"] = list(recipe.get("ingredients") or [])
    recipe["steps"] = await detail.elaborate(
        recipe.get("title") or "", recipe.get("ingredients") or [], recipe.get("steps") or [])
    recipe["ingredients"] = scale.imperial_ingredients(recipe.get("ingredients") or [])
    recipe["steps"] = scale.imperial_steps(recipe.get("steps") or [])
    recipe["ingredients"] = britishisms.americanize_ingredients(recipe.get("ingredients") or [])
    recipe["steps"] = britishisms.americanize_steps(recipe.get("steps") or [])
    # CAUGHT LIVE (2026-08-25): recipe 28 (and, it turns out, every
    # Tandoor-era import) had an empty tools list -- labels.suggest_tools()
    # has existed the whole time, but the ONLY call site left was
    # mealie.py's own old pipeline (`if not recipe.get("tools"):
    # recipe["tools"] = await labels.suggest_tools(recipe)`), never
    # ported over when tandoor.py replaced it. crawl.py's own JSON-LD
    # fast path already fills this in from the source page's own
    # schema.org data when it has any (_tools_from_ld); this is only the
    # FALLBACK for when it doesn't, or for the model path, which never
    # set tools at all. Same "leave it out if unsure" restraint as
    # before -- most recipes come back with zero or one item, not a
    # padded list.
    if not recipe.get("tools"):
        recipe["tools"] = await labels.suggest_tools(recipe)
    return recipe


async def _from_youtube(url: str) -> crawl.Recipe:
    """A recipe worked out from a YouTube video's own spoken transcript
    -- see youtube.py's own module docstring for why yt-dlp, and
    YOUTUBE_SYSTEM above for why this is a separate prompt from
    READ_SYSTEM rather than a reuse of it. Raises core.Blocked/LLMError,
    same contract as from_url() itself."""
    video = await youtube.fetch(url)
    async with vram.parsing():
        data = await llm.chat_json(
            YOUTUBE_SYSTEM + READ_SHAPE, video["transcript"][:24000],
            model=core.LLM_READ, temperature=0.1, max_tokens=3000,
        )
        if not data.get("found", True) or not data.get("ingredients"):
            raise core.Blocked(
                "I read that video's captions and could not find one clear "
                "recipe being made in it.")
        ingredients = [str(i).strip() for i in data.get("ingredients") or [] if str(i).strip()]
        steps = [_denumber(str(s)) for s in data.get("steps") or [] if str(s).strip()]
        ingredients = await crawl.fill_ingredients_from_steps(ingredients, steps)
        return await _add_detail(crawl.Recipe(
            title=str(data.get("title") or "").strip() or video["title"] or "Untitled",
            source=video["source"],
            image=video["thumbnail"],
            images=[],
            yields=str(data.get("yields") or "").strip(),
            prep=str(data.get("prep") or "").strip(),
            cook=str(data.get("cook") or "").strip(),
            total=str(data.get("total") or "").strip(),
            ingredients=ingredients,
            steps=steps,
            notes=str(data.get("notes") or "").strip(),
            how="worked out by the model from the video's own spoken transcript — check it against the video",
        ))


async def from_url(url: str) -> crawl.Recipe:
    """Fetch a page and get a recipe out of it. Raises core.Blocked / LLMError."""
    # Jason's own request (2026-08-25): "can we wire in on the same way
    # /recipe and kitchen is" -- invisibly, off the SAME paste-a-link box
    # everything else already uses, not a separate button or screen.
    # Checked first, before any HTML fetch is attempted at all: a
    # YouTube watch page's own HTML has no recipe in it for crawl.fetch()
    # to find anyway (JSON-LD or otherwise), so there's no fast path
    # being skipped here, only a slow, guaranteed-to-fail one.
    if youtube.is_youtube_url(url):
        return await _from_youtube(url)
    try:
        final_url, page_html = await crawl.fetch(url)
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        # SAY WHICH PROBLEM IT IS. "Some sites block anything that is not a
        # browser" was one message for three different situations, and it sent
        # you off to paste text by hand when waiting sixty seconds would have
        # worked.
        if code == 429:
            raise core.Blocked(
                "That site is rate-limiting us — it answered 429, which means "
                "'not so fast' rather than 'no'. Wait a minute and try again; "
                "it usually works second time.")
        if code in (401, 403):
            raise core.Blocked(
                f"That site refused us ({code}). Some sites only serve pages "
                "to a signed-in browser. Open it yourself and paste the text "
                "into the box below instead.")
        if code == 404:
            raise core.Blocked("There is no page at that address (404). Worth "
                               "checking the link.")
        raise core.Blocked(
            f"That site answered {code}, which is a problem at their end "
            "rather than ours. Trying again later is usually the answer.")
    except httpx.RequestError as e:
        raise core.Blocked(f"I could not open that page ({e.__class__.__name__}).")

    reader = crawl.read(page_html)
    images = crawl.harvest_images(reader, final_url)

    # --- the fast path ------------------------------------------------------
    recipe = crawl.from_jsonld(reader, final_url)
    if recipe:
        recipe["images"] = drop_hero(images, recipe["image"])
        recipe["ingredients"] = await crawl.fill_ingredients_from_steps(
            recipe["ingredients"], recipe["steps"])
        return await _add_detail(recipe)

    # --- the model path -----------------------------------------------------
    text = crawl.page_text(reader)
    if len(text) < 200:
        raise core.Blocked(
            "There was almost no text on that page. It is probably built by "
            "JavaScript, which I cannot run — paste the recipe text in instead."
        )

    # ONE vram.parsing() SPANNING BOTH MODEL CALLS, not one each -- this
    # function's own extraction call below AND fill_ingredients_from_steps()
    # a few lines later both use the same job/model. Wrapping them
    # separately would evict-and-restore in between two calls that are
    # really one logical import, paying the ~14B reload twice for no
    # reason. vram.parsing() is safe to nest (see its own docstring), and
    # a raise partway through still restores correctly on the way out --
    # `async with` runs the context manager's cleanup regardless.
    async with vram.parsing():
        data = await llm.chat_json(
            READ_SYSTEM + READ_SHAPE, text, model=core.LLM_READ,
            temperature=0.1, max_tokens=3000,
        )
        if not data.get("found", True) or not data.get("ingredients"):
            raise core.Blocked("I read that page and could not find a recipe on it.")

        hero = reader.meta.get("og:image", "") or (images[0]["src"] if images else "")
        ingredients = [str(i).strip() for i in data.get("ingredients") or [] if str(i).strip()]
        steps = [_denumber(str(s)) for s in data.get("steps") or [] if str(s).strip()]
        ingredients = await crawl.fill_ingredients_from_steps(ingredients, steps)
        # STILL INSIDE vram.parsing() -- same reasoning as the comment
        # above this block: _add_detail()'s own elaborate() call uses the
        # same model as the extraction just above it, so keeping it under
        # the same span avoids evicting and reloading in between.
        return await _add_detail(crawl.Recipe(
            title=str(data.get("title") or "").strip() or "Untitled",
            source=final_url,
            image=hero,
            images=drop_hero(images, hero),
            yields=str(data.get("yields") or "").strip(),
            prep=str(data.get("prep") or "").strip(),
            cook=str(data.get("cook") or "").strip(),
            total=str(data.get("total") or "").strip(),
            ingredients=ingredients,
            steps=steps,
            notes=str(data.get("notes") or "").strip(),
            how="worked out by the model from the page text — check it against the source",
        ))


async def from_text(text: str, title_hint: str = "") -> crawl.Recipe:
    """The paste box. For sites that block us, and for photos of a card typed up."""
    # See from_url()'s own comment on why this spans both model calls.
    async with vram.parsing():
        data = await llm.chat_json(
            READ_SYSTEM + READ_SHAPE, text[:24000], model=core.LLM_READ,
            temperature=0.1, max_tokens=3000,
        )
        if not data.get("found", True) or not data.get("ingredients"):
            raise core.Blocked("I could not find a recipe in that text.")
        ingredients = [str(i).strip() for i in data.get("ingredients") or [] if str(i).strip()]
        steps = [_denumber(str(s)) for s in data.get("steps") or [] if str(s).strip()]
        ingredients = await crawl.fill_ingredients_from_steps(ingredients, steps)
        return await _add_detail(crawl.Recipe(
            title=str(data.get("title") or "").strip() or title_hint or "Untitled",
            source="", image="", images=[],
            yields=str(data.get("yields") or "").strip(),
            prep=str(data.get("prep") or "").strip(),
            cook=str(data.get("cook") or "").strip(),
            total=str(data.get("total") or "").strip(),
            ingredients=ingredients,
            steps=steps,
            notes=str(data.get("notes") or "").strip(),
            how="worked out by the model from text you pasted",
        ))


def drop_hero(images: list, hero: str) -> list:
    """Take the recipe's own headline photo out of the step candidates.

    It is already at the top of the guide, so pinning it to a step shows the
    same picture twice -- and it is the picture of the FINISHED dish, which
    makes it the single most misleading thing to put next to step four.

    This is not hypothetical. On BBC Good Food's lasagne the hero's alt text is
    "Classic lasagne in an oven dish topped with basil leaves", and the
    assembly step mentions the oven, the dish and the lasagne: three words
    overlap, the bar is three, and the finished bake got captioned as the step
    where you are still layering pasta.

    Compared WITHOUT the query string, because the same file is served as
    ...jpg?resize=440,400 in one place and ...jpg?quality=90&resize=440,400 in
    another, and a string comparison sees two different images.
    """
    if not hero:
        return images
    base = hero.split("?")[0]
    return [i for i in images if i["src"].split("?")[0] != base]


_NUMBERED = re.compile(r"^\s*(?:step\s*)?\d{1,2}\s*[.):\-]\s*", re.I)


def _denumber(step: str) -> str:
    """Drop a leading "1." or "Step 3:".

    The template numbers the steps itself. Without this you get "1. 1. Preheat
    the oven", which looks like a bug because it is one.
    """
    return _NUMBERED.sub("", step).strip()


# ---------------------------------------------------------------------------
#  Matching photographs to steps
# ---------------------------------------------------------------------------
# Words that appear in every alt text on a food blog and therefore distinguish
# nothing. Without this list, "recipe" in an alt text matches every step.
_NOISE = {"the", "a", "an", "and", "to", "of", "in", "on", "with", "for", "it",
          "is", "at", "into", "until", "then", "your", "you", "this", "that",
          "recipe", "photo", "image", "step", "food", "picture", "how", "make"}


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower()) if w not in _NOISE}


def attach_images(steps: list, images: list) -> list:
    """Give each step a photograph if the page has one that is plainly about it.

    TWO WAYS A PHOTO EARNS A STEP, both deliberately conservative:

      1. ALT TEXT OVERLAP. A blog that writes "creaming the butter and sugar"
         under a photo has told us what the photo is of. Three shared words is
         the bar -- two matches far too much ("add the flour" matches half a
         baking post).

      2. POSITION, only when the counts line up exactly. A post with seven
         step photos and seven steps is a step-by-step post, and the nth photo
         is the nth step. If the counts differ at all this is skipped entirely,
         because being off by one puts the picture of the finished cake next to
         "grease the tin" -- confidently, on every step, which is worse than
         having no pictures at all.

    Everything unmatched gets a drawing. A step is never left blank.
    """
    used = set()
    out = []

    positional = len(images) == len(steps) and len(steps) >= 3

    for i, text in enumerate(steps):
        photo = None
        best, best_score = None, 0
        for j, img in enumerate(images):
            if j in used or not img.get("alt"):
                continue
            score = len(_words(text) & _words(img["alt"]))
            if score > best_score:
                best, best_score = j, score
        if best is not None and best_score >= 3:
            photo = images[best]
            used.add(best)
        elif positional and i not in used:
            photo = images[i]
            used.add(i)

        out.append({
            "n": i + 1,
            "text": text,
            "photo": photo["src"] if photo else "",
            "alt": (photo.get("alt") if photo else "") or "",
            "svg": "" if photo else diagram.svg_for(text, i + 1),
            "scene": diagram.scene_for(text),
        })
    return out


def build_guide(recipe: dict, want_servings: int | None = None,
                draft_id: str = "", draft_url: str = "") -> dict:
    """Everything the guide template needs, with no model call anywhere in it.

    Worth saying plainly: once a recipe is imported, viewing it, scaling it and
    seeing which ingredients have a written-down substitute all happen without
    touching the GPU. Reload the page at any portion size while Ollama is busy
    on the phone line and it still answers instantly.
    """
    factor, serves_now = scale.factor_for(recipe.get("yields", ""), want_servings or 0)
    lines = scale.ingredients(recipe.get("ingredients", []), factor)

    # YOUR photographs win. The order everywhere in this app is: what you
    # supplied, then what the page had, then a drawing -- most specific first,
    # invented last. `mine` is filled by the upload route in main.py.
    mine = recipe.get("mine") or {}
    drawn = recipe.get("drawn") or {}
    steps = attach_images(recipe.get("steps", []), recipe.get("images", []))
    for step in steps:
        slot = f"step-{step['n']}"
        own = mine.get(slot)
        step["own"] = bool(own)
        step["drawn"] = False
        if own:
            step["photo"] = f"{draft_url or ''}/photo/{draft_id}/{own}" if draft_id else ""
            step["alt"] = "your photo"
            step["svg"] = ""
        elif not step["photo"] and drawn.get(slot):
            # Third, not second. A photograph from the source page is of the
            # actual dish; a drawing is of the idea of it. The drawing only
            # ever fills a gap, and it fills it in front of the diagram rather
            # than in front of anything real.
            step["photo"] = (f"{draft_url or ''}/photo/{draft_id}/{drawn[slot]}"
                             if draft_id else "")
            step["alt"] = "a drawing of this step"
            step["svg"] = ""
            step["drawn"] = True

    return {
        "recipe": recipe,
        "steps": steps,
        "hero_own": mine.get("hero", ""),
        "ingredients": lines,
        "factor": factor,
        "factor_label": ("unchanged" if factor == 1
                         else f"×{scale.render(factor)}"),
        "serves_now": serves_now,
        "want": want_servings or serves_now,
        "flags": scale.flags(lines),
        "swaps": subs.scan(recipe.get("ingredients", [])),
        # Times are NOT scaled -- see the note at the top of scale.py. Said out
        # loud on the page whenever the portions have changed, because "I
        # halved it so I halve the time" is the obvious wrong assumption.
        "times_note": (factor != 1),
    }
