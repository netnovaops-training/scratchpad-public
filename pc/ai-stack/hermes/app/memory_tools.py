"""
The memory tools, bound to ONE person's store.

WHY THESE ARE NOT IN tools.py. Everything in tools.py is module-level and
identity-free: `read_file` reads the same workspace whoever is asking.
Memory is the opposite -- every call has to land in the store belonging to
the person currently talking, and there may be two people talking at once
(the CLI and the Telegram bridge share this process). So the tools are
built per-Agent around a store, rather than registered globally and handed
an identity they could get wrong.

That is also the security property: the tool closes over an authenticated
store, so there is no `person` argument for a model to hallucinate and no
path for "remember this about Casey instead" to be honoured.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from shared.memory import MemoryStore


def schema() -> list[dict[str, Any]]:
    def _tool(name, desc, props, required):
        return {"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props,
                           "required": required}}}

    return [
        _tool(
            "remember",
            "Save a durable fact about the person you are talking to, so you "
            "still know it in a month. Use for preferences, ongoing projects, "
            "and things that stay true. Do NOT use it for the answer to the "
            "question you were just asked, or for anything that will be "
            "untrue next week.",
            {"summary": {"type": "string",
                         "description": "Short noun phrase, under 70 chars. "
                                        "Becomes the index line."},
             "detail": {"type": "string",
                        "description": "One to three sentences, written to be "
                                       "useful months from now."},
             "kind": {"type": "string",
                      "description": "preference | project | context | skill"}},
            ["summary"],
        ),
        _tool(
            "recall",
            "Search what you already know about this person. The index is "
            "always in your context; use this to pull the DETAIL behind an "
            "index line before answering.",
            {"query": {"type": "string", "description": "What you want to know."}},
            ["query"],
        ),
        _tool(
            "forget",
            "Delete a remembered fact. Use when the person says it is wrong "
            "or asks you to forget it. Takes the slug from the index line.",
            {"slug": {"type": "string", "description": "The fact's slug, e.g. "
                                                       "'prefers-short-answers'"}},
            ["slug"],
        ),
    ]


# `forget` deletes; the CLI should be able to ask first.
MUTATING = {"remember", "forget"}


def build(store: MemoryStore, embed: Callable | None = None) -> dict[str, Callable]:
    """Tool name -> callable, all closed over this person's store."""

    def remember(summary: str, detail: str = "", kind: str = "note") -> str:
        f = store.remember(summary, detail or summary, kind=kind)
        return f"remembered: {f.summary} (facts/{f.slug}.md)"

    def recall(query: str) -> str:
        hits = store.recall(query, embed, k=5) if embed else []
        if not hits:
            # Fall back to everything rather than saying "nothing found" --
            # a small memory is faster to read whole than to search.
            hits = store.facts()[:5]
        if not hits:
            return "nothing remembered about this person yet"
        return "\n\n".join(f"## {f.summary}\n{f.body}" for f in hits)

    def forget(slug: str) -> str:
        return (f"forgotten: {slug}" if store.forget(slug)
                else f"no fact with slug '{slug}' -- check the index")

    return {"remember": remember, "recall": recall, "forget": forget}
