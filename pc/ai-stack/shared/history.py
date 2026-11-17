"""
Turning a month of samples into something you can read.

WHY THIS IS A SEPARATE FILE
The recorder (metrics/app.py), the CLI and the Telegram bot all need to agree
on what "vram" means, what units it is in, and what a sane range looks like.
Put that in three places and they drift; the chart says one thing and the text
says another and you stop trusting both. One catalogue, imported everywhere.

Everything here is a pure function over a list of rows, so it can be tested
without a database, a network or a GPU.
"""

from __future__ import annotations

from typing import Any, Iterable

# name -> (column, human label, unit, is_percentage)
#
# The key is what you type: /history vram. The column is what the table calls
# it. Keeping both means the table can be renamed without breaking your
# muscle memory, and vice versa.
METRICS: dict[str, tuple[str, str, str, bool]] = {
    "cpu":    ("cpu_pct",         "CPU",              "%",    True),
    "ram":    ("ram_pct",         "RAM",              "%",    True),
    "disk":   ("disk_pct",        "Disk used",        "%",    True),
    "gpu":    ("gpu_util",        "GPU load",         "%",    True),
    "vram":   ("gpu_vram_pct",    "VRAM",             "%",    True),
    "temp":   ("gpu_temp",        "GPU temperature",  "C",    False),
    "power":  ("gpu_power",       "GPU power",        "W",    False),
    "net":    ("net_rx_mbps",     "Network down",     "Mbps", False),
    "netup":  ("net_tx_mbps",     "Network up",       "Mbps", False),
    "ping":   ("latency_ms",      "Internet latency", "ms",   False),
    "down":   ("containers_down", "Containers down",  "",     False),
}

ALIASES = {
    "memory": "ram", "mem": "ram",
    "gpumem": "vram", "vidmem": "vram",
    "network": "net", "download": "net", "upload": "netup",
    "latency": "ping", "internet": "ping",
    "watts": "power", "heat": "temp", "temperature": "temp",
    "outages": "down", "containers": "down",
}

# Eight levels. Enough shape to see a spike, few enough that it still renders
# in every terminal font and in Telegram's monospace block.
BLOCKS = "▁▂▃▄▅▆▇█"


def resolve(name: str) -> str | None:
    """'memory' -> 'ram'. Returns None for something we don't chart."""
    n = (name or "").strip().lower().lstrip("/")
    if n in METRICS:
        return n
    return ALIASES.get(n)


def _f(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def bucket(rows: Iterable[dict], col: str, width: int = 48
           ) -> tuple[list[float | None], list[int]]:
    """Average the samples down to `width` columns, evenly spaced in TIME.

    Spacing by time rather than by row matters: if the recorder was stopped
    for six hours, a row-spaced chart quietly closes the gap and the outage
    disappears. Time-spaced, it stays visible as an empty column.

    Returns (values, timestamps) where a value may be None for "no data in
    this slice" -- the caller decides how to draw a hole.
    """
    rows = [r for r in rows if r.get("ts") is not None]
    if not rows:
        return [], []
    t0, t1 = rows[0]["ts"], rows[-1]["ts"]
    if t1 <= t0:
        v = _f(rows[-1].get(col))
        return [v], [t1]

    # Never ask for more columns than the data can actually fill, or a young
    # database draws every other column as a hole and looks like it has been
    # dropping samples all day. The typical gap between samples is the honest
    # resolution -- and because it's the MEDIAN, one real six-hour outage
    # doesn't inflate it and hide itself.
    gaps = sorted(rows[i]["ts"] - rows[i - 1]["ts"] for i in range(1, len(rows)))
    typical = gaps[len(gaps) // 2] or 1
    width = max(1, min(width, int((t1 - t0) / typical) + 1))

    span = (t1 - t0) / width
    sums: list[float] = [0.0] * width
    counts: list[int] = [0] * width
    for r in rows:
        v = _f(r.get(col))
        if v is None:
            continue
        i = min(width - 1, int((r["ts"] - t0) / span))
        sums[i] += v
        counts[i] += 1

    vals = [(sums[i] / counts[i]) if counts[i] else None for i in range(width)]
    stamps = [int(t0 + span * (i + 0.5)) for i in range(width)]
    return vals, stamps


def sparkline(values: list[float | None], lo: float | None = None,
              hi: float | None = None) -> str:
    """Braille-free block chart. A gap in the data is a space, not a zero.

    Drawing missing data as zero is the classic monitoring lie -- "the CPU
    was idle all night" and "the recorder was dead all night" look identical
    and mean opposite things.
    """
    real = [v for v in values if v is not None]
    if not real:
        return ""
    lo = min(real) if lo is None else lo
    hi = max(real) if hi is None else hi
    if hi <= lo:
        hi = lo + 1.0
    out = []
    for v in values:
        if v is None:
            out.append(" ")
            continue
        idx = int((v - lo) / (hi - lo) * (len(BLOCKS) - 1) + 0.5)
        out.append(BLOCKS[max(0, min(len(BLOCKS) - 1, idx))])
    return "".join(out)


def stats(rows: Iterable[dict], col: str) -> dict:
    vals = [v for v in (_f(r.get(col)) for r in rows) if v is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "min": min(vals),
        "max": max(vals),
        "avg": sum(vals) / len(vals),
        "now": vals[-1],
    }


def trend(rows: list[dict], col: str, recent_frac: float = 0.15) -> dict:
    """Is this going up or down? The last slice compared with everything before.

    A sparkline shows you the shape but makes you eyeball the direction, and
    the direction is usually the question -- "is the disk filling up" and "is
    the disk full" are different worries with different deadlines.

    Compares the most recent 15% of the window against the earlier 85%,
    rather than first-sample-vs-last, so one spike at either end can't invent
    a trend that isn't there.
    """
    vals = [(r["ts"], _f(r.get(col))) for r in rows if r.get("ts") is not None]
    vals = [(t, v) for t, v in vals if v is not None]
    if len(vals) < 4:
        return {"dir": "?", "delta": None, "recent": None, "before": None}

    cut = int(len(vals) * (1 - recent_frac))
    before = [v for _, v in vals[:cut]]
    recent = [v for _, v in vals[cut:]]
    if not before or not recent:
        return {"dir": "?", "delta": None, "recent": None, "before": None}

    b, r = sum(before) / len(before), sum(recent) / len(recent)
    delta = r - b
    # A threshold relative to the earlier average, so 2% of a busy CPU and 2%
    # of an idle one are both treated as noise, not news.
    noise = max(0.5, abs(b) * 0.05)
    return {
        "dir": "up" if delta > noise else "down" if delta < -noise else "flat",
        "delta": delta,
        "recent": r,
        "before": b,
    }


ARROW = {"up": "^", "down": "v", "flat": "-", "?": " "}


def outages(rows: Iterable[dict]) -> list[dict]:
    """Contiguous runs where at least one container was down.

    Collapsed into runs rather than listed per sample, because "unhealthy at
    02:04, 02:06, 02:08, 02:10..." is the same fact ninety times.
    """
    runs: list[dict] = []
    cur: dict | None = None
    for r in rows:
        n = r.get("containers_down") or 0
        if n > 0:
            names = [x for x in (r.get("down_names") or "").split(",") if x]
            if cur is None:
                cur = {"start": r["ts"], "end": r["ts"], "names": set(names)}
            else:
                cur["end"] = r["ts"]
                cur["names"].update(names)
        elif cur is not None:
            runs.append(cur)
            cur = None
    if cur is not None:
        runs.append(cur)
    for run in runs:
        run["names"] = sorted(run["names"])
    return runs


def _clock(ts: int) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%H:%M")


def _daystamp(ts: int) -> str:
    from datetime import datetime
    return datetime.fromtimestamp(ts).strftime("%d %b %H:%M")


def format_history_text(rows: list[dict], metric: str, hours: int,
                        width: int = 48) -> str:
    """One metric as text: a sparkline, the numbers, and what broke.

    This is what the CLI prints and what Telegram falls back to when the
    image can't be fetched -- so it has to stand on its own, not read like a
    caption for a chart that isn't there.
    """
    key = resolve(metric) or "cpu"
    col, label, unit, is_pct = METRICS[key]

    if not rows:
        return (f"No history yet for {label}.\n"
                "The recorder samples every 2 minutes; give it a few minutes "
                "after a restart.\n  docker compose ps metrics")

    s = stats(rows, col)
    if not s["n"]:
        return (f"{label}: nothing recorded in the last {hours}h.\n"
                + ("The GPU columns need ollama running.\n" if key in
                   ("gpu", "vram", "temp", "power") else "")
                + ("Disk, RAM %, network and latency come from the Windows "
                   "collector - run hostmetrics.cmd.\n" if key in
                   ("disk", "net", "netup", "ping") else ""))

    lo = 0.0 if is_pct else None
    hi = 100.0 if is_pct else None
    vals, _ = bucket(rows, col, width)
    line = sparkline(vals, lo, hi)

    u = unit if unit else ""
    span_lo = _clock(rows[0]["ts"])
    span_hi = _clock(rows[-1]["ts"])
    if hours > 36:
        span_lo, span_hi = _daystamp(rows[0]["ts"]), _daystamp(rows[-1]["ts"])

    L = [f"{label} - last {hours}h   ({len(rows)} samples)"]
    L.append(line)
    L.append(f"{span_lo}{' ' * max(1, width - len(span_lo) - len(span_hi))}{span_hi}")
    L.append("")
    L.append(f"  now {s['now']:.1f}{u}    avg {s['avg']:.1f}{u}"
             f"    min {s['min']:.1f}{u}    max {s['max']:.1f}{u}")
    t = trend(rows, col)
    if t["dir"] == "up":
        L.append(f"  trending UP   - lately {t['recent']:.1f}{u}, "
                 f"earlier {t['before']:.1f}{u}")
    elif t["dir"] == "down":
        L.append(f"  trending DOWN - lately {t['recent']:.1f}{u}, "
                 f"earlier {t['before']:.1f}{u}")
    elif t["dir"] == "flat":
        L.append("  steady")
    if is_pct:
        L.append("  scale 0-100%")
    else:
        L.append(f"  scale {min(v for v in vals if v is not None):.1f}"
                 f"-{max(v for v in vals if v is not None):.1f}{u}")

    runs = outages(rows)
    if runs:
        L.append("")
        L.append(f"  {len(runs)} outage{'s' if len(runs) != 1 else ''} in this window:")
        for run in runs[:5]:
            mins = max(2, (run["end"] - run["start"]) // 60)
            L.append(f"    {_clock(run['start'])}-{_clock(run['end'])}"
                     f"  ({mins} min)  {', '.join(run['names']) or 'unknown'}")
        if len(runs) > 5:
            L.append(f"    ... and {len(runs) - 5} more")

    return "\n".join(L)


def format_overview_text(rows: list[dict], hours: int, width: int = 40) -> str:
    """Every metric, one sparkline each. The default `/history` with no args."""
    if not rows:
        return ("No history recorded yet.\n"
                "  docker compose ps metrics")

    L = [f"Last {hours}h  ({len(rows)} samples, one every 2 min)", ""]
    drawn = 0
    for key, (col, label, unit, is_pct) in METRICS.items():
        if key == "down":
            continue
        s = stats(rows, col)
        if not s["n"]:
            continue
        drawn += 1
        vals, _ = bucket(rows, col, width)
        line = sparkline(vals, 0.0 if is_pct else None,
                         100.0 if is_pct else None)
        t = trend(rows, col)
        move = ""
        if t["dir"] in ("up", "down") and t["delta"] is not None:
            move = f"  {ARROW[t['dir']]}{abs(t['delta']):.0f}{unit}"
        L.append(f"{label:<17} {line}  {s['now']:>6.1f}{unit}"
                 f"  (avg {s['avg']:.0f}, max {s['max']:.0f}){move}")

    if not drawn:
        # Rows exist but every column is empty: the recorder is running and
        # its sources are not. Say so, rather than printing a blank page that
        # reads as "everything is fine".
        L.append("Every column is empty - the recorder is running but has")
        L.append("nothing to read. Its sources, in the order to check them:")
        L.append("  docker compose ps netdata     <- CPU, RAM, GPU, containers")
        L.append("  docker compose logs metrics   <- says which one it can't reach")
        L.append("  hostmetrics.cmd               <- C: space, real network, latency")
        return "\n".join(L)

    # The Windows collector is the one part of this that lives outside Docker
    # and so is the one part that can stop without a container going red.
    # Say so here rather than letting four columns quietly go blank.
    last = rows[-1]
    age = last.get("host_age_min")
    if age is None and last.get("src") != "windows":
        L.append("")
        L.append("Windows metrics missing - no C:/D:/G:/M: space, no real")
        L.append("network throughput, no internet latency, no Windows uptime.")
        L.append("  hostmetrics.cmd -Install     (runs every 2 min, survives reboot)")
    elif isinstance(age, (int, float)) and age > 15:
        L.append("")
        L.append(f"Windows metrics are {int(age)} min old - the collector has stopped.")
        L.append("  hostmetrics.cmd -Status      (is the scheduled task still there?)")

    L.append("")
    runs = outages(rows)
    if runs:
        L.append(f"{len(runs)} outage{'s' if len(runs) != 1 else ''}:")
        for run in runs[:5]:
            mins = max(2, (run["end"] - run["start"]) // 60)
            L.append(f"  {_clock(run['start'])}-{_clock(run['end'])}"
                     f"  ({mins} min)  {', '.join(run['names']) or 'unknown'}")
    elif any(r.get("containers_total") for r in rows):
        L.append("Nothing went down in this window.")
    else:
        # No container counts at all means the Docker socket wasn't readable,
        # NOT that everything stayed up. Claiming the latter is the one lie a
        # monitoring tool must never tell.
        L.append("Container states weren't recorded - can't say what went down.")
        L.append("  docker compose logs metrics")
    return "\n".join(L)
