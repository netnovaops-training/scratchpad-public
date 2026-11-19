"""
GPU and LLM usage, in one place, for both Hermes and the Telegram bot.

WHY THIS IS NOT JUST ANOTHER /graph TOPIC
=========================================
/graph already draws these as pictures, but it goes through n8n to do it.
That means when n8n is unhappy -- and n8n has been the flakiest part of this
stack -- you lose the ability to see usage at exactly the moment you most
want to look at it.

So this reads LiteLLM directly. No n8n in the path for the LLM numbers at
all. The GPU half tries n8n first (only n8n can run nvidia-smi, because only
n8n has the Docker socket) and falls back to asking Ollama what it currently
has loaded, which needs nothing but an HTTP call. You get *something* useful
even with n8n stopped.

Everything here is a pure function over text or JSON you already fetched.
Nothing in this module makes a network call, so it's trivially testable and
each caller can use its own sync or async HTTP client.
"""

from __future__ import annotations

import re
from typing import Any

# LiteLLM's real metric names, taken from LiteLLM's source rather than
# guessed. The catch is that WHICH LABELS it attaches is config-dependent,
# so nothing here assumes a `model` label exists -- anything unlabelled is
# bucketed under "all" rather than silently dropped.
M_REQUESTS = "litellm_requests_metric"
M_TOKENS_TOTAL = "litellm_total_tokens_metric"
M_TOKENS_IN = "litellm_input_tokens_metric"
M_TOKENS_OUT = "litellm_output_tokens_metric"
M_LATENCY_SUM = "litellm_request_total_latency_metric_sum"
M_LATENCY_COUNT = "litellm_request_total_latency_metric_count"


def _by_label(text: str, metric: str) -> dict[str, float]:
    """Sum one Prometheus metric, grouped by its model (or job) label.

    Prometheus text is one line per label-combination:
        metric_name{label="v",other="w"} 12.5
    A metric with no labels at all is also legal:
        metric_name 12.5
    Both forms are handled; the unlabelled one lands in "all".
    """
    out: dict[str, float] = {}
    # Escape the name so a metric containing regex characters can't misbehave.
    esc = re.escape(metric)

    for m in re.finditer(rf"^{esc}\{{([^}}]*)\}}\s+([\d.eE+-]+)\s*$", text, re.MULTILINE):
        labels, value = m.group(1), m.group(2)
        lm = re.search(r'\b(?:model|model_group|job)="([^"]*)"', labels)
        key = (lm.group(1) if lm and lm.group(1) else "all")
        try:
            out[key] = out.get(key, 0.0) + float(value)
        except ValueError:
            continue

    for m in re.finditer(rf"^{esc}\s+([\d.eE+-]+)\s*$", text, re.MULTILINE):
        try:
            out["all"] = out.get("all", 0.0) + float(m.group(1))
        except ValueError:
            continue

    return out


def summarise_llm(metrics_text: str) -> dict[str, dict[str, Any]]:
    """Per-model-route usage from LiteLLM's /metrics output.

    Returns {route: {requests, tokens_in, tokens_out, tokens_total,
                     latency_avg_s}}. Missing pieces are None rather than 0,
    so "we didn't measure this" stays distinguishable from "this is zero".
    """
    if not metrics_text:
        return {}

    requests = _by_label(metrics_text, M_REQUESTS)
    tok_total = _by_label(metrics_text, M_TOKENS_TOTAL)
    tok_in = _by_label(metrics_text, M_TOKENS_IN)
    tok_out = _by_label(metrics_text, M_TOKENS_OUT)
    lat_sum = _by_label(metrics_text, M_LATENCY_SUM)
    lat_cnt = _by_label(metrics_text, M_LATENCY_COUNT)

    routes = set()
    for d in (requests, tok_total, tok_in, tok_out, lat_sum):
        routes.update(d)

    result: dict[str, dict[str, Any]] = {}
    for r in sorted(routes):
        total = tok_total.get(r)
        # Some LiteLLM configs export in/out but not the total. Adding them is
        # correct there, and this only fires when the real total is absent.
        if total is None and (tok_in.get(r) is not None or tok_out.get(r) is not None):
            total = (tok_in.get(r) or 0) + (tok_out.get(r) or 0)

        avg = None
        if lat_cnt.get(r):
            avg = lat_sum.get(r, 0.0) / lat_cnt[r]

        result[r] = {
            # False positive: `requests` here is a local dict of counts, not the requests library.
            "requests": requests.get(r),  # nosec B113
            "tokens_in": tok_in.get(r),
            "tokens_out": tok_out.get(r),
            "tokens_total": total,
            "latency_avg_s": avg,
        }
    return result


def diagnose_metrics(reached: bool, metrics_text: str, llm: dict,
                     status: int | None = None, error: str = "") -> str:
    """Say WHICH of the several 'no numbers' cases this actually is.

    The first version lumped them into one line -- "no LLM usage recorded yet
    (or litellm didn't answer)" -- which is useless, because the causes have
    completely different fixes.

    The SECOND version was still wrong in a way that cost real debugging time:
    it collapsed "the connection was refused" and "it answered 404" into the
    same `reached=False`, and printed "LiteLLM didn't answer on :4000" for
    both. So a perfectly healthy LiteLLM that simply has no /metrics route
    reads as a dead container, and you go and run `docker compose ps litellm`
    -- which says Up, healthy -- and learn nothing.
    
    Those are opposite problems: one means the container is down, the other
    means the container is fine and the exporter never loaded. `status` and
    `error` are what let this tell them apart.
    """
    if not reached:
        # 404 is BY FAR the most common one here, and it is not an outage.
        if status == 404:
            return ("LiteLLM is up, but it has no /metrics route (404).\n"
                    "  The Prometheus exporter didn't load. Almost always this\n"
                    "  is a container that was STARTED BEFORE the config change\n"
                    "  -- litellm reads callbacks only when it boots.\n"
                    "\n"
                    "  docker compose up -d --force-recreate litellm\n"
                    "\n"
                    "  If it's still 404 after that, check litellm/config.yaml has\n"
                    "    litellm_settings:\n"
                    "      callbacks: [\"prometheus\"]\n"
                    "  (callbacks -- NOT success_callback, which silently does\n"
                    "  nothing for metrics), then check: logs.cmd litellm 40")
        if status in (401, 403):
            return (f"LiteLLM refused the request for /metrics ({status}).\n"
                    "  This build gates metrics behind a key. Check that\n"
                    "  LITELLM_MASTER_KEY in .env matches what this container\n"
                    "  was started with:  docker compose up -d --force-recreate litellm hermes telegram")
        if status in (301, 302, 307, 308):
            # This was the real cause of a long hunt. LiteLLM redirects
            # /metrics to /metrics/, and httpx -- unlike requests -- does NOT
            # follow redirects unless you ask it to. So the call "failed"
            # against a completely healthy LiteLLM.
            #
            # Every internal call now passes follow_redirects=True, so seeing
            # this again means a caller was added that forgot it.
            return (f"LiteLLM redirected /metrics (HTTP {status}) and the "
                    "redirect wasn't followed.\n"
                    "  That's a bug in this stack, not in LiteLLM: the HTTP\n"
                    "  client needs follow_redirects=True. Rebuild with the\n"
                    "  current code:\n"
                    "    docker compose build hermes telegram\n"
                    "    docker compose up -d --force-recreate hermes telegram")
        if status is not None:
            return (f"LiteLLM answered /metrics with HTTP {status}.\n"
                    "  logs.cmd litellm 40")
        return ("Can't open a connection to LiteLLM on :4000.\n"
                + (f"  {error}\n" if error else "")
                + "  docker compose ps litellm   will say whether it's up.")

    if llm:
        return ""

    # It answered, so the question becomes whether the exporter is registered
    # at all. Any litellm_* line means yes, and then it's genuinely just idle.
    if "litellm_" not in metrics_text:
        return ("LiteLLM answered but exports no litellm_* metrics.\n"
                "  That means the Prometheus exporter isn't registered:\n"
                "  litellm/config.yaml needs  callbacks: [\"prometheus\"]\n"
                "  under litellm_settings (NOT success_callback), then\n"
                "  docker compose up -d --force-recreate litellm")

    return ("Metrics are on, but nothing has been recorded yet.\n"
            "  Counters reset when LiteLLM restarts. Ask a model something,\n"
            "  then run this again.")


def gpu_from_stats(stats_json: dict | None) -> dict | None:
    """Full GPU picture, from n8n's stats snapshot (nvidia-smi)."""
    if not stats_json:
        return None
    g = stats_json.get("gpu")
    if not g:
        return None
    return {
        "source": "nvidia-smi",
        "util_pct": g.get("util_pct"),
        "mem_used_mb": g.get("mem_used_mb"),
        "mem_total_mb": g.get("mem_total_mb"),
        "temp_c": g.get("temp_c"),
        "power_w": g.get("power_w"),
        "fan_pct": g.get("fan_pct"),
        "models": None,
    }


def gpu_from_ollama(ps_json: dict | None) -> dict | None:
    """Fallback: what Ollama currently holds in VRAM.

    Less than nvidia-smi gives -- no utilisation, temperature or power, since
    Ollama doesn't know those. But it needs no Docker socket, so it still
    answers when n8n is stopped, and "which model is loaded and how big is it"
    is often the actual question anyway.
    """
    if not ps_json:
        return None
    models = ps_json.get("models") or []
    if not models:
        # Reached Ollama fine, nothing loaded. That's a real answer, not a
        # failure -- say so rather than returning None and printing "no GPU".
        return {"source": "ollama", "util_pct": None, "mem_used_mb": 0,
                "mem_total_mb": None, "temp_c": None, "power_w": None,
                "fan_pct": None, "models": []}

    used = sum((m.get("size_vram") or 0) for m in models)
    return {
        "source": "ollama",
        "util_pct": None,
        "mem_used_mb": round(used / (1024 * 1024)),
        "mem_total_mb": None,
        "temp_c": None,
        "power_w": None,
        "fan_pct": None,
        "models": [
            {
                "name": m.get("name") or m.get("model") or "?",
                "vram_mb": round((m.get("size_vram") or 0) / (1024 * 1024)),
            }
            for m in models
        ],
    }


def _fmt_int(n: Any) -> str:
    if n is None:
        return "-"
    try:
        return f"{round(float(n)):,}"
    except (TypeError, ValueError):
        return "-"


def format_text(llm: dict, gpu: dict | None, note: str = "") -> str:
    """Plain text, sized for a phone screen. No markdown, no tables.

    Telegram renders monospace poorly on narrow screens and markdown tables
    not at all, so this is deliberately line-per-fact rather than columnar.
    """
    lines: list[str] = []

    lines.append("GPU")
    if not gpu:
        lines.append("  not available (n8n stopped, and Ollama didn't answer)")
    elif gpu["source"] == "nvidia-smi":
        used, total = gpu.get("mem_used_mb"), gpu.get("mem_total_mb")
        if used is not None and total:
            pct = round(100 * used / total)
            lines.append(f"  vram   {_fmt_int(used)} / {_fmt_int(total)} MB  ({pct}%)")
        if gpu.get("util_pct") is not None:
            lines.append(f"  util   {gpu['util_pct']}%")
        bits = []
        if gpu.get("temp_c") is not None:
            bits.append(f"{gpu['temp_c']}C")
        if gpu.get("power_w") is not None:
            bits.append(f"{gpu['power_w']}W")
        if gpu.get("fan_pct") is not None:
            bits.append(f"fan {gpu['fan_pct']}%")
        if bits:
            lines.append("  " + "  ".join(bits))
    else:
        models = gpu.get("models") or []
        if not models:
            lines.append("  no model loaded in VRAM right now")
        else:
            lines.append(f"  vram   {_fmt_int(gpu.get('mem_used_mb'))} MB in use")
            for m in models:
                lines.append(f"    {m['name']}  {_fmt_int(m['vram_mb'])} MB")
        lines.append("  (from Ollama - start n8n for utilisation/temp/power)")

    lines.append("")
    lines.append("LLM usage, since LiteLLM last restarted")
    if not llm:
        # The caller passes the specific reason in `note`; don't guess here.
        lines.append("  (none yet)")
    else:
        for route, d in llm.items():
            lines.append(f"  {route}")
            parts = []
            if d.get("requests") is not None:
                parts.append(f"{_fmt_int(d['requests'])} req")
            if d.get("tokens_total") is not None:
                parts.append(f"{_fmt_int(d['tokens_total'])} tok")
            if d.get("latency_avg_s") is not None:
                parts.append(f"{d['latency_avg_s']:.1f}s avg")
            if parts:
                lines.append("    " + "  ".join(parts))
            if d.get("tokens_in") is not None or d.get("tokens_out") is not None:
                lines.append(
                    f"    in {_fmt_int(d.get('tokens_in'))}"
                    f"  out {_fmt_int(d.get('tokens_out'))}"
                )

    if note:
        lines.append("")
        lines.append(note)
    return "\n".join(lines)
