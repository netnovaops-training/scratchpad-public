"""Finding a real photo of a plain item -- an ingredient or a kitchen tool
-- by name, from the open internet. Shared by mealie.py's food pictures
(foodphotos.py) and toolimages.py's tool pictures: the search and the
download do not care which kind of item they were asked for, only the
prompt-building at the bottom differs by a word.

WHY THIS EXISTS SEPARATELY FROM art.py
art.py draws a step because a real photo of somebody else's chicken in
somebody else's pan would be a wrong, hotlinked, copyrighted claim about THIS
recipe. Neither problem applies to a plain ingredient or a stand mixer:
"a photo of an onion" is not making a claim about anyone's dish, and any
onion (or any Dutch oven) is a fine one to show -- this is closer to
ai-stack/kb's imagesearch.py (a real photo of a physical, identifiable
thing) than to art.py's reasoning for why steps stay drawings.

LISTED, NOT DOWNLOADED. Same rule as kb's imagesearch.py: this returns
candidates only. Nothing is fetched onto disk until main.py's review route
downloads the one a person ticked.

WHY ASYNC HTTPX, UNLIKE kb/imagesearch.py's SYNC VERSION
Same idea, ported rather than shared -- Kitchen and the kb service are
separate containers with no package between them, and Kitchen's own house
style is httpx.AsyncClient throughout (see art.py, mealie.py, crawl.py),
so this matches ITS file's neighbours rather than copying the other
service's sync style verbatim.
"""
from __future__ import annotations

import re
from html import unescape

import httpx

TIMEOUT = 12.0
UA = "Mozilla/5.0 (compatible; JasonsKitchen/1.0; personal recipe box, one search at a time)"

# Same two-call shape as kb's imagesearch.py: a vqd token off the ordinary
# HTML results page, then the JSON image endpoint. No API key, no signup --
# see that file's own docstring for why html.duckduckgo.com specifically
# (the plain /html/ page redirects from the bare domain; this one does not).
_HTML_URL = "https://html.duckduckgo.com/html/"
_IMG_URL = "https://duckduckgo.com/i.js"
_VQD = re.compile(r'name="vqd"\s+value="([^"]+)"')


class ImageSearchError(Exception):
    pass


async def search_images(query: str, limit: int = 8) -> list[dict]:
    """Candidate photos for `query`, best guesses first.

    Each item: {"url", "thumbnail", "title", "source_page"}. Raises
    ImageSearchError with a message fit to show a person, rather than
    returning an empty list that looks the same as "nothing found".
    """
    q = (query or "").strip()
    if not q:
        raise ImageSearchError("Type something to search for first.")

    headers = {"User-Agent": UA}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT, headers=headers,
                                     follow_redirects=True) as c:
            r = await c.get(_HTML_URL, params={"q": q})
            r.raise_for_status()
            m = _VQD.search(r.text)
            if not m:
                raise ImageSearchError("DuckDuckGo did not answer the usual way. Try again shortly.")
            r2 = await c.get(_IMG_URL, params={
                "q": q, "vqd": m.group(1), "o": "json", "f": ",,,", "p": "1",
            }, headers={**headers, "Referer": _HTML_URL})
            r2.raise_for_status()
            data = r2.json()
    except ImageSearchError:
        raise
    except Exception as e:  # noqa: BLE001
        raise ImageSearchError(f"Could not reach the internet ({e.__class__.__name__}).") from e

    out = []
    for item in data.get("results", [])[:limit]:
        url = str(item.get("image") or "").strip()
        if not url:
            continue
        out.append({
            "url": url,
            "thumbnail": str(item.get("thumbnail") or url).strip(),
            "title": unescape(str(item.get("title") or "")).strip(),
            "source_page": str(item.get("url") or "").strip(),
        })
    if not out:
        raise ImageSearchError("Nothing came back for that. Try a plainer name.")
    return out


# For the opt-in AI-drawn fallback, used by both a food's and a tool's
# picture routes. Reuses art.py's room()/draw()/release() directly rather
# than re-implementing the GPU check -- see main.py's picture routes for the
# caller. Only the prompt and negative below are specific to a single-item
# photo rather than a recipe step.
ITEM_NEGATIVE = ("text, words, letters, watermark, signature, hands, fingers, "
                 "people, faces, cartoon, illustration, drawing, blurry, "
                 "deformed, plate, plated meal, table setting, cutlery, "
                 "restaurant presentation, multiple items, collage")


def prompt_for_item(name: str, kind: str = "ingredient") -> str:
    """A picture prompt for one plain item -- an ingredient or a kitchen
    tool -- no model involved. Same reasoning as art.py's prompt_for: the
    item has to lead the prompt or these models default to a finished,
    plated dish (for a food) or a busy lifestyle scene (for a tool)."""
    what = (name or "").strip().lower()
    return f"{what}, a single {kind}, plain background, photograph, natural light"


async def download_image(url: str, timeout: float = 30.0) -> bytes:
    """Fetch ONE picture, because a person ticked its box. Raises
    ImageSearchError on anything that is not plausibly a photo -- same
    "listed, not trusted" reasoning as the search above."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True,
                                     headers={"User-Agent": UA}) as c:
            r = await c.get(url)
    except Exception as e:  # noqa: BLE001
        raise ImageSearchError(f"Could not fetch that picture ({e.__class__.__name__}).") from e
    if r.status_code >= 400:
        raise ImageSearchError(f"That picture answered {r.status_code}.")
    if len(r.content) > 8 * 1024 * 1024:
        raise ImageSearchError("That picture is too big.")
    return r.content
