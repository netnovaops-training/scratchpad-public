"""
The 8pm report: one message, once a day, that you actually read.

WHY A DAILY DIGEST RATHER THAN MORE ALERTS
==========================================
The heartbeat already texts you the moment something breaks. That is the
right shape for a failure and the wrong shape for everything else -- nobody
wants a message saying the GPU got warm at 2pm.

This is the other half: once a day, the things that are only meaningful as a
SHAPE. Was it busier than usual, is the disk creeping up, did anything go
down while you weren't looking, how much did the models actually get used.
None of those are alerts. All of them are worth thirty seconds at 8pm.

The whole thing is rendered here, as pure functions over data someone else
fetched, so the n8n workflow that sends it is three nodes with no logic in
them -- and so this can be tested without a schedule, a printer, or a GPU.
"""

from __future__ import annotations

from typing import Any

from .history import METRICS, bucket, outages, sparkline, stats, trend


def _hm(ts: int) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _fmt_tokens(n: float | None) -> str:
    if n is None:
        return "-"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    return f"{int(n)}"


def headline(rows: list[dict], llm: dict, printer: dict | None) -> str:
    """The first line: the single most useful sentence in the message.

    Deliberately one line and deliberately first. A digest that opens with a
    table gets skimmed and then ignored; a digest that opens with "all quiet"
    or "3 outages" gets read every day because the first line always earns
    the next thirty seconds.
    """
    problems = []
    runs = outages(rows)
    if runs:
        problems.append(f"{len(runs)} outage{'s' if len(runs) != 1 else ''}")

    disk = trend(rows, "disk_pct")
    if disk["dir"] == "up" and (disk["delta"] or 0) >= 1:
        problems.append(f"disk up {disk['delta']:.0f}%")

    s = stats(rows, "gpu_temp")
    if s.get("n") and s["max"] >= 80:
        problems.append(f"GPU hit {s['max']:.0f}C")

    for p in (printer or {}).get("problems") or []:
        problems.append(p)

    if problems:
        return "Today: " + ", ".join(problems) + "."

    calls = sum(v.get("requests") or 0 for v in llm.values())
    if calls:
        return f"Today: all quiet. {int(calls)} model calls."
    return "Today: all quiet."


def format_daily_report(rows: list[dict], llm: dict | None = None,
                        printer: dict | None = None,
                        hours: int = 24, width: int = 32) -> str:
    """The whole 8pm message."""
    llm = llm or {}
    L: list[str] = []
    L.append(headline(rows, llm, printer))
    L.append("")

    if not rows:
        L.append("No metrics recorded in the last 24h.")
        L.append("  docker compose ps metrics")
        return "\n".join(L)

    # --- the machine ------------------------------------------------------
    L.append(f"MACHINE  (last {hours}h, {len(rows)} samples)")
    for key in ("cpu", "ram", "disk", "gpu", "vram", "temp"):
        col, label, unit, is_pct = METRICS[key]
        s = stats(rows, col)
        if not s.get("n"):
            continue
        vals, _ = bucket(rows, col, width)
        line = sparkline(vals, 0.0 if is_pct else None, 100.0 if is_pct else None)
        t = trend(rows, col)
        arrow = {"up": " ^", "down": " v"}.get(t["dir"], "")
        L.append(f"  {label:<16}{line}  {s['now']:>5.0f}{unit}"
                 f"  peak {s['max']:.0f}{unit}{arrow}")

    # --- network ----------------------------------------------------------
    rx, tx = stats(rows, "net_rx_mbps"), stats(rows, "net_tx_mbps")
    if rx.get("n"):
        L.append("")
        L.append("NETWORK")
        L.append(f"  down  avg {rx['avg']:.1f} Mbps, peak {rx['max']:.1f}")
        if tx.get("n"):
            L.append(f"  up    avg {tx['avg']:.1f} Mbps, peak {tx['max']:.1f}")
        ping = stats(rows, "latency_ms")
        if ping.get("n"):
            L.append(f"  ping  {ping['avg']:.0f}ms avg, worst {ping['max']:.0f}ms")

    # --- models -----------------------------------------------------------
    L.append("")
    L.append("MODELS")
    if not llm:
        L.append("  nothing recorded")
        L.append("  (counters live in LiteLLM; a restart clears them)")
    else:
        # Busiest first: the route you actually rely on should be the one you
        # see, not whichever happens to sort first alphabetically.
        for route, v in sorted(llm.items(),
                               key=lambda kv: -(kv[1].get("requests") or 0)):
            calls = v.get("requests")
            tot = v.get("tokens_total")
            lat = v.get("latency_avg_s")
            bits = [f"{int(calls)} calls" if calls is not None else ""]
            if tot:
                bits.append(f"{_fmt_tokens(tot)} tokens")
            if lat:
                bits.append(f"{lat:.1f}s avg")
            L.append(f"  {route:<14} " + "  ".join(b for b in bits if b))

    # --- printer ----------------------------------------------------------
    if printer:
        L.append("")
        L.append("PRINTER")
        jobs = printer.get("today") or []
        if jobs:
            pages = sum(j.get("pages") or 0 for j in jobs)
            L.append(f"  {len(jobs)} job(s), {pages} page(s)")
            for j in jobs[:4]:
                who = (j.get("user") or "?").split("\\")[-1]
                doc = (j.get("document") or "?")[:30]
                L.append(f"    {_hm_iso(j.get('time'))}  {who}  {doc}")
        else:
            L.append("  nothing printed")
        for s in printer.get("supplies") or []:
            if isinstance(s.get("pct"), (int, float)):
                L.append(f"  {s.get('name', 'toner')}: {int(s['pct'])}%")

    # --- what broke -------------------------------------------------------
    runs = outages(rows)
    L.append("")
    if runs:
        L.append("OUTAGES")
        for run in runs[:6]:
            mins = max(2, (run["end"] - run["start"]) // 60)
            L.append(f"  {_hm(run['start'])}-{_hm(run['end'])}  ({mins} min)  "
                     f"{', '.join(run['names']) or 'unknown'}")
        if len(runs) > 6:
            L.append(f"  ... and {len(runs) - 6} more")
    else:
        L.append("Nothing went down today.")

    return "\n".join(L)


def _hm_iso(ts: str | None) -> str:
    """HH:MM out of an ISO timestamp, without parsing it properly.

    Slicing rather than datetime.fromisoformat because the Windows side
    writes offsets like '-04:00' that older Pythons reject, and a report that
    crashes on a timestamp format is worse than one that shows '??:??'.
    """
    if not ts or len(ts) < 16:
        return "??:??"
    return ts[11:16]
