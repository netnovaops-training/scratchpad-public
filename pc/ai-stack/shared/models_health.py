"""
Which job routes actually work, and which are quietly broken.

THE PROBLEM THIS SOLVES
=======================
litellm/config.yaml maps a JOB ("coding") to a MODEL
("ollama_chat/qwen2.5-coder:7b"). Nothing checks that the model on the
right-hand side was ever actually pulled. If it wasn't -- a typo, a rename
upstream, a `PULL_MODELS` line that was edited after the fact -- you find
out when you ask a question and get an error, which might be weeks later
and probably at the worst moment.

TWO CHECKS, DELIBERATELY DIFFERENT IN COST
==========================================
1. INVENTORY (free). Compare what LiteLLM has configured against what
   Ollama actually has on disk. No model is loaded, no GPU work happens,
   it takes milliseconds. This catches the common failure -- "configured
   but never pulled" -- and is safe to run any time, on a schedule, from
   a chart, from anywhere.

2. LIVE (expensive). LiteLLM's /health actually sends a request to a model.
   That forces Ollama to LOAD it into VRAM. On a 12GB card, checking every
   route means loading several models, and LiteLLM fires those in parallel
   (https://github.com/BerriAI/litellm/issues/5816) -- which on this machine
   would evict whatever you were using and could take minutes.

   So live checks here are ONE ROUTE AT A TIME, on explicit request only,
   and never on a timer. That restriction is the whole reason this module
   separates the two rather than offering a single "check everything".

Pure functions over JSON you already fetched -- no network calls here.
"""

from __future__ import annotations

from typing import Any


def parse_routes(model_info_json: dict | None) -> list[dict[str, str]]:
    """Job routes from LiteLLM's /model/info.

    Returns [{route, backing, provider}], where `backing` is the bare model
    name Ollama would know it by -- 'ollama_chat/qwen3:8b' becomes 'qwen3:8b',
    because that prefix is LiteLLM's routing syntax, not part of the name.
    """
    if not model_info_json:
        return []
    rows = model_info_json.get("data")
    if rows is None and isinstance(model_info_json, list):
        rows = model_info_json
    if not rows:
        return []

    out: list[dict[str, str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        route = r.get("model_name") or ""
        raw = ((r.get("litellm_params") or {}).get("model")) or ""
        provider, backing = "", raw
        if "/" in raw:
            provider, backing = raw.split("/", 1)
        if route:
            out.append({"route": route, "backing": backing, "provider": provider})
    return out


def parse_installed(tags_json: dict | None) -> set[str]:
    """Model names Ollama actually has on disk, from /api/tags."""
    if not tags_json:
        return set()
    names: set[str] = set()
    for m in tags_json.get("models") or []:
        for key in ("name", "model"):
            v = m.get(key)
            if v:
                names.add(v)
    return names


def _matches(backing: str, installed: set[str]) -> bool:
    """Is this model present, allowing for the implicit ':latest' tag?

    Ollama treats 'qwen3' and 'qwen3:latest' as the same thing, and which
    form appears depends on how it was pulled. Comparing raw strings would
    report a perfectly working model as missing.
    """
    if not backing:
        return False
    if backing in installed:
        return True
    if ":" not in backing and f"{backing}:latest" in installed:
        return True
    return bool(backing.endswith(":latest") and backing[:-len(":latest")] in installed)


def reconcile(routes: list[dict[str, str]], installed: set[str]) -> list[dict[str, Any]]:
    """Join configured routes against installed models. Free; loads nothing."""
    out = []
    for r in routes:
        # Only Ollama-backed routes can be checked this way. A cloud route
        # (openai/..., anthropic/...) isn't "missing" just because it isn't
        # on this disk -- saying so would be actively wrong.
        local = r["provider"].startswith("ollama") or r["provider"] == ""
        out.append({
            **r,
            "local": local,
            "installed": _matches(r["backing"], installed) if local else None,
        })
    return out


def parse_health(health_json: dict | None, route: str) -> dict[str, Any]:
    """Result of a LIVE /health?model=<route> check for one route."""
    if not health_json:
        return {"route": route, "live": None, "error": "no response"}

    healthy = health_json.get("healthy_endpoints") or []
    unhealthy = health_json.get("unhealthy_endpoints") or []

    if healthy:
        return {"route": route, "live": True, "error": ""}
    if unhealthy:
        err = ""
        first = unhealthy[0]
        if isinstance(first, dict):
            err = str(first.get("error") or first.get("api_base") or "")
        return {"route": route, "live": False, "error": err[:300]}
    return {"route": route, "live": None, "error": "no endpoints reported"}


def format_inventory_text(rows: list[dict[str, Any]], installed_count: int) -> str:
    """Plain text for Telegram. One line per route, verdict first."""
    if not rows:
        return ("No model routes configured, or LiteLLM didn't answer.\n"
                "  docker compose ps litellm   will say which.")

    lines = []
    missing = []
    for r in rows:
        if r["installed"] is True:
            mark = "ok"
        elif r["installed"] is False:
            mark = "MISSING"
            missing.append(r)
        else:
            mark = "cloud"
        lines.append(f"  {mark:8} {r['route']} -> {r['backing'] or '?'}")

    if missing:
        lines.append("")
        lines.append("These will fail. Pull them:")
        for r in missing:
            lines.append(f"  docker exec ollama ollama pull {r['backing']}")
    else:
        lines.append("")
        lines.append("All routes good.")
    return "\n".join(lines)
