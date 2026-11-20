"""Reading a recipe page. Structured data first, the model only if we must.

THE ORDER HERE IS THE WHOLE DESIGN
----------------------------------
    1. schema.org JSON-LD    exact, free, instant, no GPU
    2. the model             slow, costs VRAM, and can invent an ingredient

Almost every recipe site on the internet publishes a machine-readable Recipe
block, because Google needs one to show the little picture in search results.
That block IS the recipe: ingredients as a list, steps as a list, yield, times.
When it is there we take it VERBATIM and no model is asked anything -- which
means the import is exact, it happens in under a second, and it works while the
GPU is busy answering the phone line.

The model is the fallback for pages that publish nothing: someone's Wordpress
from 2009, a forum post, a photo caption. There it is genuinely better than
parsing, because it can tell prose apart from a method. But it is second, not
first, and the guide page says which one was used -- see the `how` field. You
should be able to tell at a glance whether the ingredient list in front of you
was copied or inferred.

WHY IMAGES ARE COLLECTED HERE AND NOT GENERATED
-----------------------------------------------
Same argument Mom's guides.py makes about screenshots. A model asked for a
picture of "fold the butter into the flour" produces something plausible and
wrong, and a wrong picture in a method is worse than no picture -- you match
your bowl against it and conclude you have done it badly. So: real photographs
lifted from the page the recipe came from, and where there is no real
photograph, a drawn diagram that is obviously a diagram (see diagram.py).
"""
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urljoin

import httpx

from . import core, ing_clean, llm, vram


class Recipe(dict):
    """A plain dict with the keys the templates expect.

    Kept as a dict rather than a dataclass because it goes straight into JSON
    for the Mealie API and into the SQLite drafts table, and every conversion
    step is a place for a field to get quietly dropped.

    Keys: title, source, image, images[], yields, servings, prep, cook, total,
          ingredients[], steps[], notes, how, source_rating, source_reviews[],
          nutrition_stated, tools[]
    """


# ---------------------------------------------------------------------------
#  Fetching
# ---------------------------------------------------------------------------
# Enough for the usual http->https->www chain and a consent hop, not enough
# for a site to keep this container busy going round a loop.
MAX_REDIRECTS = 6
_REDIRECTS = {301, 302, 303, 307, 308}


async def fetch(url: str) -> tuple[str, str]:
    """Return (final_url, html). Raises core.Blocked or an httpx error."""
    url = core.is_public_url(url)
    headers = {"User-Agent": core.USER_AGENT, **core.BROWSER_HEADERS}
    # EVERY HOP IS CHECKED, NOT JUST THE ONE THAT WAS TYPED.
    #
    # Checking only the typed URL and then following redirects means the guard
    # can be walked straight past: a public site is allowed to answer "302, go
    # to http://ollama:11434/api/delete", and with follow_redirects=True this
    # container obediently goes. The address bar said example.com the whole
    # time. So redirects are followed by hand, one at a time, with the same
    # check applied to each new address.
    async with httpx.AsyncClient(timeout=core.FETCH_TIMEOUT,
                                 follow_redirects=False,
                                 headers=headers) as client:
        for _hop in range(MAX_REDIRECTS):
            async with client.stream("GET", url) as probe:
                if probe.status_code not in _REDIRECTS:
                    break
                where = probe.headers.get("location", "")
            if not where:
                raise core.Blocked("That link redirected to nowhere.")
            url = core.is_public_url(urljoin(url, where))
        else:
            raise core.Blocked("That link redirects round in circles.")

        async with client.stream("GET", url) as r:
            r.raise_for_status()
            ctype = r.headers.get("content-type", "")
            if ctype and "html" not in ctype and "xml" not in ctype:
                raise core.Blocked(
                    f"That link is a {ctype.split(';')[0]}, not a web page.")
            chunks, size = [], 0
            async for chunk in r.aiter_bytes():
                size += len(chunk)
                # Stop READING rather than checking Content-Length: a server
                # that lies about the length, or sends none at all, walks
                # straight past a header check.
                if size > core.MAX_FETCH_BYTES:
                    raise core.Blocked("That page is enormous. I stopped reading it.")
                chunks.append(chunk)
            raw = b"".join(chunks)
            final = str(r.url)
            enc = r.encoding or "utf-8"
    return final, raw.decode(enc, errors="replace")


# ---------------------------------------------------------------------------
#  A very small HTML reader
# ---------------------------------------------------------------------------
class Reader(HTMLParser):
    """Pulls out the four things we need and ignores everything else.

    Stdlib, deliberately -- see the note in requirements.txt. It does not need
    to build a tree, because nothing here asks a tree-shaped question.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ld = []        # raw JSON-LD script bodies
        self.meta = {}      # og:image and friends
        self.images = []    # (src, alt)
        self.links = []     # (href, words, was it inside nav/footer?)
        self.text = []
        self._in_ld = False
        self._skip = 0      # depth inside script/style/nav/footer
        self._a = None      # the link currently open, and the words seen in it

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld = True
            self.ld.append("")
            return
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "form"):
            self._skip += 1
            return
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content"):
                self.meta.setdefault(key, a["content"])
        elif tag == "a" and a.get("href"):
            # Links are collected even inside nav and footer, because `_skip`
            # is about TEXT and a roundup page's recipe list is sometimes
            # marked up as a nav. find_links() does the filtering instead,
            # where the rules can be read.
            # THE THIRD VALUE IS WHERE IT SITS. A roundup page's nav links to
            # every category the site has -- on food52 those look exactly like
            # recipe links (/recipes/chicken) and there are dozens of them, so
            # a filter that only looks at the URL cannot tell them apart. Where
            # the link is on the page can: the recipes the article is ABOUT are
            # in its body, and the categories are in the chrome around it.
            self._a = [a["href"], "", self._skip > 0]
        elif tag == "img":
            src = (a.get("src") or a.get("data-src")
                   or a.get("data-lazy-src") or "")
            # srcset's last entry is usually the biggest. A thumbnail in a
            # step-by-step guide is unreadable on a phone.
            if not src and a.get("srcset"):
                src = a["srcset"].split(",")[-1].strip().split(" ")[0]
            if src:
                self.images.append((src, a.get("alt", "")))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag == "a" and self._a:
            self.links.append((self._a[0], self._a[1].strip(), self._a[2]))
            self._a = None
            return
        if tag == "script" and self._in_ld:
            self._in_ld = False
            return
        if tag in ("script", "style", "noscript", "svg", "nav", "footer", "form"):
            self._skip = max(0, self._skip - 1)
        elif tag in ("p", "li", "div", "h1", "h2", "h3", "h4", "tr"):
            self.text.append("\n")

    def handle_data(self, data):
        if self._in_ld:
            self.ld[-1] += data
        else:
            s = data.strip()
            if s and self._a is not None:
                # The words inside a link, wherever the link is. Used to label
                # a queued import before it has been read.
                self._a[1] += s + " "
            if s and not self._skip:
                self.text.append(s + " ")


def read(page_html: str) -> Reader:
    r = Reader()
    try:
        r.feed(page_html)
    except Exception:
        # HTMLParser is forgiving but not infinitely so, and a half-read page
        # still has usable JSON-LD and images in it. Losing the tail beats
        # losing the import.
        pass
    return r


# ---------------------------------------------------------------------------
#  schema.org
# ---------------------------------------------------------------------------
def _clean(s) -> str:
    if not isinstance(s, str):
        return ""
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def _as_list(v) -> list:
    if v is None:
        return []
    return v if isinstance(v, list) else [v]


def _iso_duration(v) -> str:
    """PT1H30M -> '1 hr 30 min'. Anything unrecognised comes back as itself.

    CAUGHT LIVE (2026-08-24): the old regex only understood the MINIMAL
    ISO 8601 shape (`P[nD]T[nH][nM][nS]`) -- real sites routinely publish
    the FULL grammar instead, every field present even at zero
    ("P0Y0M0DT0H5M0.000S" for "5 minutes"), with a Y and a SECOND, entirely
    different M (months, before the T) that the old pattern had no group
    for at all. That's not a rare shape: schema.org's own duration spec
    permits it, and at least one real recipe hit it. A duration the regex
    could not match fell through to `_clean(v)`, returning the raw ISO
    string completely unparsed -- which then broke everything downstream
    that expected a human string, notably tandoor.py's `_minutes()`,
    which grabbed the first stray digit of "P0Y..." (a 0) instead of the
    real value.
    """
    if not isinstance(v, str):
        return ""
    m = re.fullmatch(
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
        r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?)?",
        v.strip(), re.I)
    if not m or not any(m.groups()):
        return _clean(v)
    years, months, weeks, days, hours, mins, secs = m.groups()
    out = []
    if years and int(years):
        out.append(f"{int(years)} yr")
    if months and int(months):
        out.append(f"{int(months)} mo")
    if weeks and int(weeks):
        out.append(f"{int(weeks)} wk")
    if days and int(days):
        out.append(f"{int(days)} day{'s' if int(days) != 1 else ''}")
    if hours and int(hours):
        out.append(f"{int(hours)} hr")
    if mins and int(mins):
        out.append(f"{int(mins)} min")
    if secs and float(secs) >= 1:
        out.append(f"{int(float(secs))} sec")
    return " ".join(out)


def _first_image(v) -> str:
    """schema.org `image` is a string, a list, or an ImageObject. All three."""
    for item in _as_list(v):
        if isinstance(item, str) and item:
            return item
        if isinstance(item, dict):
            u = item.get("url") or item.get("contentUrl")
            if u:
                return u
    return ""


def _steps_from_ld(v) -> list:
    """HowToStep, HowToSection, or a plain list of strings."""
    out = []
    for item in _as_list(v):
        if isinstance(item, str):
            out.append(_clean(item))
        elif isinstance(item, dict):
            if "HowToSection" in str(item.get("@type", "")):
                out.extend(_steps_from_ld(item.get("itemListElement")))
            else:
                out.append(_clean(item.get("text") or item.get("name") or ""))
    return [s for s in out if s]


# schema.org's `tool` field is meant for equipment, but some sites' recipe
# plugins stuff a shopping widget's link text in there too -- CAUGHT LIVE, a
# handful of recipes came back needing "Amazon" as a tool. Every one of these
# is a store or an affiliate label, never a thing anyone owns a kitchen for.
_NOT_A_TOOL = {
    "amazon", "amazon.com", "walmart", "target", "instacart", "etsy", "ebay",
    "shop", "shop now", "store", "buy now", "affiliate", "affiliate link",
    "sponsored", "advertisement", "ad",
}


def _looks_like_a_tool(name: str) -> bool:
    n = re.sub(r"[^a-z0-9 ]", "", name.strip().lower())
    return len(n) >= 2 and n not in _NOT_A_TOOL


def _tools_from_ld(v) -> list:
    """schema.org `tool` -- a HowToTool, a plain string, or a list of
    either. Equipment, not ingredients: a stand mixer, a Dutch oven. Not
    every site publishes this, and that's fine -- it just means an empty
    list, the same as a site with no aggregateRating."""
    out = []
    for item in _as_list(v):
        if isinstance(item, str):
            out.append(ing_clean.clean(_clean(item)))
        elif isinstance(item, dict):
            out.append(ing_clean.clean(_clean(item.get("name") or "")))
    return [t for t in out if t and _looks_like_a_tool(t)]


def _rating_from_ld(v) -> dict:
    """schema.org AggregateRating -> {"value": float, "count": int}, or {}.

    THIS IS FEEDBACK FROM THE ORIGINAL SITE, NOT MEALIE'S OWN RATING.
    Mealie's rating (subs.py has nothing to do with this) is this
    household's own opinion, entered here. This is "823 people who cooked
    it on the blog it came from gave it 4.7 stars" -- worth showing, and
    worth keeping clearly labelled as somebody else's number, not this
    kitchen's. Review TEXT is deliberately not scraped: free-text reviews
    are the least structured, most spam-prone field on a recipe page, and a
    star rating plus a count is the part that is actually reliable.
    """
    if not isinstance(v, dict):
        return {}
    try:
        value = float(v.get("ratingValue"))
    except (TypeError, ValueError):
        return {}
    count = 0
    for key in ("ratingCount", "reviewCount"):
        try:
            count = int(v.get(key))
            break
        except (TypeError, ValueError):
            continue
    if not (0 < value <= 5):
        return {}
    return {"value": round(value, 1), "count": count}


# schema.org NutritionInformation's own field names -> the names
# recipe_nutrition.py already uses for its OWN, unrelated, ingredient-level
# USDA estimate (nutrition_full/nutrition_servings elsewhere in this app).
# Sharing the names is deliberate -- one vocabulary for "an amount of
# calories/protein/etc", whichever of the two pipelines produced it -- but
# this dict is NOT that pipeline. Values here are the SOURCE'S OWN, already
# PER SERVING, flat numbers; nutrition_full is a whole-recipe sum this app
# computed itself from ingredient weights. Never mix the two up or scale
# this one the way that one gets scaled -- see nutrition_stated's use in
# main.py/guide.html.
_NUTRITION_FIELD = {
    "calories": "calories",
    "carbohydrateContent": "carbs_g",
    "proteinContent": "protein_g",
    "fatContent": "fat_g",
    "saturatedFatContent": "saturated_fat_g",
    "cholesterolContent": "cholesterol_mg",
    "sodiumContent": "sodium_mg",
    "fiberContent": "fiber_g",
    "sugarContent": "sugar_g",
}


def _num(v):
    """schema.org nutrition values arrive as strings like "205 kcal" or
    "12 g" -- pull the leading number out, or None if there isn't one."""
    if v is None:
        return None
    m = re.search(r"[\d.]+", str(v))
    return float(m.group()) if m else None


def _nutrition_from_ld(v) -> dict:
    """schema.org NutritionInformation -> a flat {calories, protein_g, ...}
    dict of whatever the source actually published, or {} if it published
    nothing usable. Never guesses a missing field."""
    if not isinstance(v, dict):
        return {}
    out = {}
    for ld_key, our_key in _NUTRITION_FIELD.items():
        num = _num(v.get(ld_key))
        if num is not None:
            out[our_key] = num
    if out and v.get("servingSize"):
        size = _clean(str(v["servingSize"]))
        if size:
            out["serving_size"] = size
    return out


# Sites rarely publish more than a handful of reviews in their own
# structured data even when the comment section below has hundreds -- this
# is not a cap Kitchen is imposing, it just never asks for more than what
# schema.org already hands over. Kept small anyway as a backstop.
_MAX_REVIEWS = 8
_MAX_REVIEW_CHARS = 2000


def _reviews_from_ld(v) -> list:
    """schema.org `review` -> [{author, rating, date, body}, ...].

    ONLY this structured field, never arbitrary HTML comment sections --
    see the note on _rating_from_ld() above about why review text was
    originally left out. Free-text HTML scraping would mean trusting
    whatever markup a comment widget happens to use on every different
    site, unbounded and unmoderated; this stays inside the same trusted,
    machine-readable contract everything else in this file already reads
    from. Jinja auto-escapes on render, so this is safe to store as plain
    text -- just never mark it `|safe` in a template.
    """
    out = []
    for item in _as_list(v):
        if not isinstance(item, dict):
            continue
        body = _clean(item.get("reviewBody") or "")
        if not body:
            continue
        if len(body) > _MAX_REVIEW_CHARS:
            body = body[:_MAX_REVIEW_CHARS].rstrip() + "…"
        rating = None
        rr = item.get("reviewRating")
        if isinstance(rr, dict):
            try:
                rating = round(float(rr.get("ratingValue")), 1)
            except (TypeError, ValueError):
                rating = None
        author = ""
        a = item.get("author")
        if isinstance(a, dict):
            author = _clean(a.get("name") or "")
        elif isinstance(a, str):
            author = _clean(a)
        out.append({
            "author": author or "someone on the recipe's page",
            "rating": rating,
            "date": _clean(str(item.get("datePublished") or "")),
            "body": body,
        })
        if len(out) >= _MAX_REVIEWS:
            break
    return out


def _find_recipe_node(node):
    """Walk whatever the page published, looking for @type Recipe.

    Sites nest this every way imaginable: a bare object, an array, an @graph,
    a WebPage with the recipe hanging off mainEntity. This is a search, not a
    path -- a fixed path works on one site and breaks on the next.
    """
    if isinstance(node, list):
        for item in node:
            found = _find_recipe_node(item)
            if found:
                return found
    elif isinstance(node, dict):
        types = [str(t).lower() for t in _as_list(node.get("@type"))]
        if any("recipe" in t for t in types):
            return node
        for key in ("@graph", "mainEntity", "itemListElement"):
            if key in node:
                found = _find_recipe_node(node[key])
                if found:
                    return found
    return None


def from_jsonld(reader: Reader, url: str):
    """The fast path. Returns a Recipe, or None if the page has no usable one."""
    for blob in reader.ld:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        node = _find_recipe_node(data)
        if not node:
            continue

        ingredients = [ing_clean.clean(_clean(i)) for i in
                       _as_list(node.get("recipeIngredient") or node.get("ingredients"))]
        ingredients = [i for i in ingredients if i]
        steps = _steps_from_ld(node.get("recipeInstructions"))
        if not ingredients or not steps:
            # A Recipe block with no method is a stub -- a category page, or a
            # site that only publishes the photo for the search result. Fall
            # through to the model rather than importing an empty recipe.
            continue

        yields = _as_list(node.get("recipeYield"))
        return Recipe(
            title=(_clean(node.get("name"))
                   or _clean(reader.meta.get("og:title"))
                   or "Untitled"),
            source=url,
            image=urljoin(url, _first_image(node.get("image"))
                          or reader.meta.get("og:image", "")),
            images=[],
            yields=_clean(str(yields[0])) if yields else "",
            prep=_iso_duration(node.get("prepTime")),
            cook=_iso_duration(node.get("cookTime")),
            total=_iso_duration(node.get("totalTime")),
            ingredients=ingredients,
            steps=steps,
            notes=_clean(node.get("description")),
            how="read straight from the page's own recipe data",
            source_rating=_rating_from_ld(node.get("aggregateRating")),
            source_reviews=_reviews_from_ld(node.get("review")),
            nutrition_stated=_nutrition_from_ld(node.get("nutrition")),
            tools=_tools_from_ld(node.get("tool")),
        )
    return None


# ---------------------------------------------------------------------------
#  Ingredients the steps mention but the ingredient list left vague or out
# ---------------------------------------------------------------------------
# "Kosher salt and freshly ground black pepper" with no quantity anywhere,
# and step 1 says "generously season the chicken with the salt and pepper" --
# the amount IS on the page, just not next to the ingredient. Cheap enough to
# check with a regex before ever waking the model: a line starting with a
# digit or a unicode fraction, or one of the few phrases that already carry
# an amount ("a pinch of...", "...to taste"), is not bare. THE FAST PATH
# STAYS FAST FOR MOST RECIPES -- most ingredient lists already state a real
# amount for every line, and this only calls the model when at least one
# line does not.
#
# THE PHRASES ARE SEARCHED ANYWHERE IN THE LINE, NOT JUST THE START.
# CAUGHT LIVE: this function's own output puts "to taste" at the END of a
# line ("Kosher salt and freshly ground black pepper, to taste"), so an
# anchored match never recognised its own completed work as done -- running
# this function a second time treated an already-fixed line as still bare,
# asked the model again, and that second pass glued a fragment of step text
# onto the line instead of leaving it alone. The leading digit/fraction
# check stays anchored at the start on purpose: a number appearing
# somewhere in the middle of a line is not the same strong signal a number
# leading it is.
_QTY_HINT = re.compile(
    r"^\s*(\d|[¼½¾⅓⅔⅐⅑⅒⅛⅜⅝⅞])|\b(a pinch|a dash|a splash|a drizzle|"
    r"a handful|a knob|to taste|as needed)\b", re.I)


def _looks_bare(line: str) -> bool:
    return not _QTY_HINT.search(line)


FILL_SYSTEM = """You are checking a recipe's ingredient list against its own
written steps, for a household cook who wants every ingredient's amount
filled in rather than left blank.

Some ingredients in the list have no amount at all -- often a seasoning like
"salt and pepper" -- because the amount actually appears later, in how a
step describes using it ("season generously", "a pinch", "to taste"). Your
job is ONLY to move that ACTUAL wording from the steps into the ingredient
line. Never invent a quantity the steps do not support.

Rules:
- Only touch a line with NO real quantity in it already (no number, no
  fraction, no "a pinch of", "a dash of", "a splash of", and so on). Leave
  every other line exactly as it is -- do not repeat it in your answer.
- For a bare line, search the steps for how much of it is actually used. If
  the steps say how ("generously", "to taste", "a pinch", "a drizzle"), work
  that in naturally -- either appended ("Kosher salt and freshly ground
  black pepper, to taste") or as a lead-in amount ("A drizzle of olive
  oil"), whichever reads like something a person actually wrote. If the
  steps say nothing about amount either, leave that line out of your answer
  entirely -- do not guess a quantity that appears nowhere on the page.
- If a step uses an ingredient that never appears in the list at all, add
  it, using only the amount language the step actually gives (or none, if
  the step gives none either).
- No preamble, no sign-off, no mention of being an AI."""

FILL_MAX_TOKENS = 1500


async def fill_ingredients_from_steps(ingredients: list, steps: list) -> list:
    """Bare ingredient lines get an amount backfilled FROM WHAT THE STEPS
    ACTUALLY SAY -- never a guessed quantity -- and a step-only ingredient
    (a last-minute garnish, a splash of something) gets added. See
    FILL_SYSTEM for the exact rules the model is held to.

    Conservative twice over: a cheap regex skips the model call entirely
    when every line already states a real amount, and the model is asked to
    return ONLY what changed rather than the whole list rewritten -- one bad
    generation cannot silently drop or reorder anything it was not asked to
    touch.

    core.LLM_READ, NOT LLM_TALK -- CAUGHT LIVE. LLM_TALK's job (`chat`,
    qwen3:8b) is a THINKING model, and asked to reason through every
    ingredient against every step before answering, it reliably spent its
    whole token budget thinking and returned nothing parseable (see the
    warning in core.py's own LLM_LABEL comment about the exact same
    failure). LLM_READ's job (`long-context`, qwen2.5:7b) is a plain model
    with the extra context room this actually needs, and it is already
    proven on a near-identical job -- importer.py's own from_url() asks it
    for a whole recipe as JSON from raw page text.

    Never raises. On any model trouble, or when there's nothing to look at,
    the original list comes back unchanged -- this is a nice-to-have polish
    pass, not something an import should ever fail over.
    """
    if not ingredients or not steps:
        return ingredients
    if not any(_looks_bare(i) for i in ingredients):
        return ingredients

    ask = ("Ingredients:\n" + "\n".join(f"- {i}" for i in ingredients) +
           "\n\nSteps:\n" + "\n".join(f"{n}. {s}" for n, s in enumerate(steps, 1)))
    try:
        async with vram.parsing():
            data = await llm.chat_json(
                FILL_SYSTEM + '\n\nReturn JSON: {"changed": [{"was": "...", '
                '"now": "..."}], "added": ["...", "..."]}',
                ask, model=core.LLM_READ, temperature=0.1, max_tokens=FILL_MAX_TOKENS,
            )
    except llm.LLMError:
        return ingredients

    out = list(ingredients)
    for pair in data.get("changed") or []:
        if not isinstance(pair, dict):
            continue
        was = str(pair.get("was") or "").strip()
        now = str(pair.get("now") or "").strip()
        if not was or not now:
            continue
        try:
            idx = out.index(was)
        except ValueError:
            # The model paraphrased "was" instead of quoting the line
            # exactly -- safer to skip than guess which line it meant.
            continue
        out[idx] = ing_clean.clean(now)
    for extra in data.get("added") or []:
        text = ing_clean.clean(str(extra or "").strip())
        if not text:
            continue
        dup = _matches_existing_line(text, out)
        if dup is not None:
            out[dup] = text
        elif text not in out:
            out.append(text)
    return out


# A handful of words that are the AMOUNT, not the ingredient, so they should
# not count toward whether an "added" line is actually the same ingredient
# as one already in the list.
_AMOUNT_WORDS = {"a", "an", "the", "of", "or", "and", "to", "taste",
                 "generously", "generous", "amount", "drizzle", "splash",
                 "dash", "pinch", "handful", "knob"}


def _core_words(line: str) -> set:
    return {w for w in re.findall(r"[a-z]+", line.lower())
           if w not in _AMOUNT_WORDS and len(w) > 2}


def _matches_existing_line(added: str, existing: list) -> int | None:
    """CAUGHT LIVE: the model sometimes puts an already-bare existing line
    here instead of in "changed" -- it should have said this line CHANGED,
    not that a new one was ADDED -- e.g. "Cooking spray or extra virgin
    olive oil" turning into a second, separate "A drizzle of olive oil"
    line rather than replacing the first. FULL CONTAINMENT, not a fuzzy
    overlap score: once amount words are stripped, one line's remaining
    words have to be entirely inside the other's ("olive", "oil" inside
    "cooking", "spray", "extra", "virgin", "olive", "oil") -- a single
    shared word is not enough, or "garlic salt" would wrongly get folded
    into an unrelated "garlic cloves" line over the one word they share.
    Returns the matching index in `existing`, or None for a genuinely new
    ingredient.
    """
    added_words = _core_words(added)
    if not added_words:
        return None
    for i, line in enumerate(existing):
        line_words = _core_words(line)
        if not line_words:
            continue
        if added_words <= line_words or line_words <= added_words:
            return i
    return None


# ---------------------------------------------------------------------------
#  What the model gets when there is no JSON-LD
# ---------------------------------------------------------------------------
def page_text(reader: Reader, limit: int = 24000) -> str:
    """The page as rough text.

    Squashed hard. A food blog is mostly navigation, comments and adverts, and
    a 200KB prompt on a 7B model at 32k context is both slow and likely to push
    the actual method out of the window before it is read.
    """
    text = re.sub(r"[ \t]+", " ", "".join(reader.text))
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()[:limit]


def harvest_images(reader: Reader, url: str, limit: int = 40) -> list:
    """Candidate step photographs, in page order.

    No model involved: these are real files the page already serves. Matching
    them to steps happens in guide.py, and a step that matches nothing simply
    gets a drawn diagram instead.
    """
    seen, out = set(), []
    for src, alt in reader.images:
        full = urljoin(url, src)
        if full in seen or not full.startswith(("http://", "https://")):
            continue
        low = full.lower()
        # Icons, avatars, share buttons, tracking pixels, the site logo. All of
        # them are images; none of them is a step in a recipe.
        if any(bad in low for bad in
               ("logo", "avatar", "icon", "sprite", "pixel", "badge", "gravatar",
                "author", "spacer", "1x1", "emoji", "/ads/", "doubleclick",
                "facebook", "pinterest", "instagram")):
            continue
        if low.split("?")[0].endswith((".svg", ".gif")):
            continue
        seen.add(full)
        out.append({"src": full, "alt": _clean(alt)})
        if len(out) >= limit:
            break
    return out
