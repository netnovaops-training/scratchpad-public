"""Knowledge-store tools for Hermes: search the documents saved in Paperless.

  docs_search  keyword search, returns short snippets with the document title
               and id. Optional `source` narrows to one tag (for example
               "api-docs" or "homelab-docs").
  docs_read    the text of ONE document by id (capped), for when a snippet is
               not enough.

READ-ONLY. The Paperless token belongs to the least-privilege user
"hermes-docs" (view + add documents; no delete, no admin) and is fetched from
Proton Pass at call time by plant_tools._pass_key. It is never put in a tool
result or an error message.

Why this exists (2026-10-31): Hermes kept answering "how do I call API X" from
memory. The API how-to pages and our own docs now live in Paperless, so the
answer comes from a page with a name Jason can check.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .plant_tools import _pass_key

_BASE = os.environ.get("PAPERLESS_BASE", "http://192.168.1.86:8000")
_ITEM = "Paperless Hermes token (hermes-docs)"
_TIMEOUT = 20
_TAG_RE = re.compile(r"<[^>]+>")


def _get(path: str) -> dict:
    try:
        tok = _pass_key(_ITEM, "password")
    except RuntimeError as e:
        raise RuntimeError(str(e))
    req = urllib.request.Request(
        _BASE + path,
        headers={"Authorization": f"Token {tok}", "Accept": "application/json"}  # a pinned "; version=5" got HTTP 406 here 2026-10-31,
    )
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Paperless answered HTTP {e.code} for {path.split('?')[0]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach Paperless: {e.reason}") from e


def _snippet(content: str, query: str, width: int = 420) -> str:
    """A window of text around the first query word that appears in it."""
    flat = " ".join(content.split())
    low = flat.lower()
    for word in query.lower().split():
        i = low.find(word)
        if i >= 0:
            start = max(0, i - width // 3)
            return ("..." if start else "") + flat[start:start + width] + "..."
    return flat[:width] + "..."


def docs_search(query: str, source: str = "", limit: int = 5) -> str:
    """Search the saved documents. Use distinctive words (an API name, an
    endpoint, an error code) rather than a whole sentence."""
    limit = max(1, min(int(limit or 5), 10))
    params = {"query": query, "page_size": limit}
    if source:
        # Tag filter by name; Paperless matches the name exactly (ignoring case).
        params["tags__name__iexact"] = source
    try:
        data = _get("/api/documents/?" + urllib.parse.urlencode(params))
    except RuntimeError as e:
        return f"Document search failed: {e}"
    results = data.get("results", [])
    if not results:
        return f"No saved document matches '{query}'" + (f" in '{source}'." if source else ".")
    lines = [f"{data.get('count', len(results))} match(es) for '{query}' (showing {len(results)}):"]
    for r in results:
        lines.append(f"\n[{r['id']}] {r.get('title', '')}")
        lines.append("  " + _snippet(_TAG_RE.sub("", r.get("content", "")), query))
    lines.append("\nUse docs_read with an id to read the whole document.")
    return "\n".join(lines)


def docs_read(doc_id: int, max_chars: int = 6000) -> str:
    """The text of one saved document, from the start, capped at max_chars."""
    max_chars = max(500, min(int(max_chars or 6000), 20000))
    try:
        r = _get(f"/api/documents/{int(doc_id)}/")
    except (RuntimeError, ValueError) as e:
        return f"Could not read document {doc_id}: {e}"
    text = r.get("content", "")
    cut = text[:max_chars]
    tail = f"\n[cut: {len(text) - max_chars} more characters]" if len(text) > max_chars else ""
    return f"[{r['id']}] {r.get('title', '')}\n\n{cut}{tail}"


REGISTRY = {"docs_search": docs_search, "docs_read": docs_read}
