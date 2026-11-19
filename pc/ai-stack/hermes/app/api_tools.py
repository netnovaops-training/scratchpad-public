"""Free plant and web-search API tools for Hermes (all READ-ONLY).

  perenual_search   care data (watering, sun, edible, poisonous)  -- Perenual
  trefle_search     taxonomy and names                            -- Trefle
  plantbook_search  ideal sensor ranges per species               -- Open Plantbook
  permapeople_search permaculture facts, companions               -- Permapeople
  inat_taxa         taxa, common names, observation counts        -- iNaturalist (no key)
  web_search        Google results (Serper) or LLM-ready web search (Tavily)

Keys come from the Proton Pass "API keys" vault at call time through
plant_tools._pass_key and are never placed in a result or an error message.
Perenual and Trefle put the key in the URL, so every error passes through
_safe() which strips key=/token= values (the 2026-10-28 USDA leak happened
exactly this way).

How each call was written (2026-10-31): from the stored pages in Paperless
(tag api-docs). Those pages are model-written summaries, and three details are
UNCONFIRMED until a live call proves them: Serper's X-API-KEY header and "q"
body, Trefle's /plants/search path, and Open Plantbook's Token header on
plant/search. No live call was made when this file was written; a failure
message names the HTTP code so the page can be corrected.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from .plant_tools import _pass_key


def _http_only(target):
    """Return target (a URL string or urllib Request) if its scheme is http or https, else raise ValueError.

    urlopen also follows file:// and custom schemes (Bandit B310). Every caller here talks to an
    http(s) service, so anything else is a mistake or a planted URL. Added 2026-11-19.
    """
    url = str(getattr(target, "full_url", target))
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url[:80]}")
    return target


_TIMEOUT = 20
_UA = {"User-Agent": "Hermes/1.0 (household assistant)"}
_SECRET_QS = re.compile(r"(?i)([?&](?:key|token|api[-_]?key)=)[^&\s]+")


def _safe(text: str) -> str:
    return _SECRET_QS.sub(r"\1<redacted>", text)


def _call(url: str, headers: dict | None = None, body: dict | None = None) -> dict | list:
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {**_UA, **(headers or {})}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=hdrs)
    try:
        with urllib.request.urlopen(_http_only(req), timeout=_TIMEOUT) as r:  # nosec B310
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(_safe(f"HTTP {e.code} from {url.split('?')[0]}")) from e
    except urllib.error.URLError as e:
        raise RuntimeError(_safe(f"could not reach {url.split('?')[0]}: {e.reason}")) from e


def _lim(n: int, hi: int = 10) -> int:
    return max(1, min(int(n or 5), hi))


def perenual_search(query: str, limit: int = 5) -> str:
    """Care data for plants matching a name (Perenual free tier)."""
    key = _pass_key("Perenual Api", "API Key")
    url = "https://perenual.com/api/v2/species-list?" + urllib.parse.urlencode({"key": key, "q": query})
    try:
        d = _call(url)
    except RuntimeError as e:
        return f"Perenual lookup failed: {e}"
    rows = d.get("data", [])[:_lim(limit)]
    if not rows:
        return f"Perenual has nothing for '{query}'."
    out = [f"Perenual results for '{query}' ({d.get('total', len(rows))} total):"]
    for r in rows:
        sci = ", ".join(r.get("scientific_name") or [])
        out.append(f"  [{r.get('id')}] {r.get('common_name')} ({sci}) | cycle {r.get('cycle')} | "
                   f"water {r.get('watering')} | sun {', '.join(r.get('sunlight') or [])}")
    return "\n".join(out)


def trefle_search(query: str, limit: int = 5) -> str:
    """Plant names and taxonomy from Trefle (60 requests/minute)."""
    key = _pass_key("https://trefle.io/", "API Key")
    url = "https://trefle.io/api/v1/plants/search?" + urllib.parse.urlencode({"token": key, "q": query})
    try:
        d = _call(url)
    except RuntimeError as e:
        return f"Trefle lookup failed: {e}"
    rows = d.get("data", [])[:_lim(limit)]
    if not rows:
        return f"Trefle has nothing for '{query}'."
    out = [f"Trefle results for '{query}':"]
    for r in rows:
        out.append(f"  {r.get('scientific_name')} | common {r.get('common_name')} | family {r.get('family')} "
                   f"| slug {r.get('slug')}")
    return "\n".join(out)


def plantbook_search(query: str, limit: int = 5) -> str:
    """Ideal light, temperature, humidity and soil ranges (Open Plantbook)."""
    key = _pass_key("https://open.plantbook.io/", "API Key")
    url = "https://open.plantbook.io/api/v1/plant/search?" + urllib.parse.urlencode(
        {"alias": query, "limit": _lim(limit)})
    try:
        d = _call(url, {"Authorization": f"Token {key}"})
    except RuntimeError as e:
        return f"Open Plantbook lookup failed: {e}"
    rows = d.get("results", d) if isinstance(d, dict) else d
    if not rows:
        return f"Open Plantbook has nothing for '{query}'."
    return "Open Plantbook results:\n" + "\n".join(
        f"  {r.get('pid')} | {r.get('display_pid')} | alias {r.get('alias')}" for r in rows[:_lim(limit)])


def permapeople_search(query: str, limit: int = 5) -> str:
    """Permaculture plant facts (Permapeople; data is CC BY-SA, credit Permapeople.org)."""
    kid = _pass_key("https://permapeople.org", "API Key")
    sec = _pass_key("https://permapeople.org", "Secret")
    url = "https://permapeople.org/api/search?" + urllib.parse.urlencode({"q": query})
    try:
        d = _call(url, {"x-permapeople-key-id": kid, "x-permapeople-key-secret": sec})
    except RuntimeError as e:
        return f"Permapeople lookup failed: {e}"
    rows = (d.get("plants") if isinstance(d, dict) else d) or []
    if not rows:
        return f"Permapeople has nothing for '{query}'."
    out = ["Permapeople results (credit Permapeople.org, CC BY-SA 4.0):"]
    for r in rows[:_lim(limit)]:
        out.append(f"  [{r.get('id')}] {r.get('name')} ({r.get('scientific_name')}) {str(r.get('description') or '')[:140]}")
    return "\n".join(out)


def inat_taxa(query: str, limit: int = 5) -> str:
    """Taxa from iNaturalist. No key; keep to a few calls a minute (about 60/min is the polite limit)."""
    url = "https://api.inaturalist.org/v1/taxa?" + urllib.parse.urlencode({"q": query, "per_page": _lim(limit)})
    try:
        d = _call(url)
    except RuntimeError as e:
        return f"iNaturalist lookup failed: {e}"
    rows = d.get("results", [])
    if not rows:
        return f"iNaturalist has nothing for '{query}'."
    out = [f"iNaturalist taxa for '{query}' ({d.get('total_results')} total):"]
    for r in rows:
        out.append(f"  [{r.get('id')}] {r.get('name')} | {r.get('preferred_common_name')} | rank {r.get('rank')} "
                   f"| observations {r.get('observations_count')}")
    return "\n".join(out)


def web_search(query: str, engine: str = "serper", limit: int = 5) -> str:
    """Web search. engine 'serper' (Google results, 2,500 free queries) or 'tavily' (1,000 credits/month).
    Each successful call spends credits, so search only when the saved docs do not answer."""
    n = _lim(limit)
    try:
        if engine == "tavily":
            key = _pass_key("https://app.tavily.com", "API Key")
            d = _call("https://api.tavily.com/search", {"Authorization": f"Bearer {key}"},
                      {"query": query, "max_results": n, "search_depth": "basic"})
            rows = [(r.get("title"), r.get("url"), (r.get("content") or "")[:220]) for r in d.get("results", [])]
        else:
            key = _pass_key("Serper.dev", "API Key")
            d = _call("https://google.serper.dev/search", {"X-API-KEY": key}, {"q": query, "num": n})
            rows = [(r.get("title"), r.get("link"), (r.get("snippet") or "")[:220]) for r in d.get("organic", [])]
    except RuntimeError as e:
        return f"Web search ({engine}) failed: {e}"
    if not rows:
        return f"No web results for '{query}'."
    return f"Web results ({engine}) for '{query}':\n" + "\n".join(f"  {t}\n    {u}\n    {s}" for t, u, s in rows[:n])


REGISTRY = {
    "perenual_search": perenual_search,
    "trefle_search": trefle_search,
    "plantbook_search": plantbook_search,
    "permapeople_search": permapeople_search,
    "inat_taxa": inat_taxa,
    "web_search": web_search,
}
