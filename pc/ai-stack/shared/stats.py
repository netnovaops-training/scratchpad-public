"""
/stats - one command, two depths.

    /stats        the short version: what you'd want at a glance
    /stats full   everything, in sections, paged so nothing is trimmed

WHY TWO DEPTHS RATHER THAN ONE COMPROMISE
=========================================
A single screen that tries to serve both readers serves neither. The glance
version gets padded until you stop reading it, or the detailed version gets
trimmed until the thing you needed is the thing that got cut.

So: `simple` is deliberately short enough to read on a lock screen, and
`full` is deliberately complete -- every container, every drive, every
interface, every model route -- and returns a LIST of pages rather than one
blob, because Telegram hard-rejects anything over 4096 characters and
silently trimming a detail screen defeats its purpose.

Everything is a pure function over one assembled dict, so all of it is
testable with no Docker, no GPU and no printer.
"""

from __future__ import annotations

from typing import Any

from .history import METRICS, bucket, outages, sparkline, trend
from .history import stats as col_stats

# Telegram's hard limit is 4096. 3500 leaves room for the <pre> wrapper and
# the page header without ever getting close.
PAGE_CHARS = 3500


def _bar(pct: float | None, width: int = 12) -> str:
    if pct is None:
        return " " * width
    n = max(0, min(width, round(pct / 100 * width)))
    return "#" * n + "." * (width - n)


def _gb(mb: float | None) -> str:
    if mb is None:
        return "-"
    return f"{mb / 1024:.1f}G" if mb >= 1024 else f"{mb:.0f}M"


def _n(v: Any, unit: str = "", dp: int = 0) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):.{dp}f}{unit}"
    except (TypeError, ValueError):
        return str(v)


# ---------------------------------------------------------------------------
#  simple
# ---------------------------------------------------------------------------

def format_stats_simple(d: dict) -> str:
    """Short enough to read without scrolling. Problems first, always."""
    L: list[str] = []
    probs = d.get("problems") or []
    if probs:
        L.append("PROBLEMS")
        for p in probs[:5]:
            L.append("  ! " + p)
        if len(probs) > 5:
            L.append(f"  ... and {len(probs) - 5} more")
    else:
        L.append("All good.")
    L.append("")

    cur = d.get("current") or {}
    host = d.get("host") or {}
    gpu_pct = cur.get("gpu_vram_pct")

    L.append(f"  cpu   {_bar(cur.get('cpu_pct'))} {_n(cur.get('cpu_pct'), '%')}")
    L.append(f"  ram   {_bar(cur.get('ram_pct'))} {_n(cur.get('ram_pct'), '%')}"
             + (f"   {host.get('ram_used_gb')}/{host.get('ram_total_gb')} GB"
                if host.get("ram_total_gb") else ""))
    L.append(f"  vram  {_bar(gpu_pct)} {_n(gpu_pct, '%')}"
             + (f"   {_gb(cur.get('gpu_vram_mb'))}" if cur.get("gpu_vram_mb") else ""))
    L.append(f"  gpu   {_bar(cur.get('gpu_util'))} {_n(cur.get('gpu_util'), '%')}"
             + (f"   {_n(cur.get('gpu_temp'), 'C')}" if cur.get("gpu_temp") else ""))

    drives = host.get("drives") or []
    if drives:
        worst = max(drives, key=lambda x: x.get("pct_used") or 0)
        L.append(f"  disk  {_bar(worst.get('pct_used'))} "
                 f"{_n(worst.get('pct_used'), '%')}   {worst.get('name')}: "
                 f"{worst.get('free_gb')} GB free")

    L.append("")
    total = d.get("containers_total")
    down = d.get("containers_down") or 0
    if total:
        L.append(f"  {total - down}/{total} containers up"
                 + (f"   DOWN: {d.get('down_names')}" if down else ""))
    calls = sum((v or {}).get("requests") or 0 for v in (d.get("llm") or {}).values())
    if calls:
        L.append(f"  {int(calls)} model calls")
    pr = d.get("printer") or {}
    if pr.get("state"):
        L.append(f"  printer: {pr['state']}"
                 + (f", {len(pr.get('today') or [])} job(s) today"
                    if pr.get("today") is not None else ""))

    L.append("")
    L.append("/stats full   everything")
    L.append("/trends       the last 24h")
    return "\n".join(L)


# ---------------------------------------------------------------------------
#  full - one function per section, so each is testable on its own
# ---------------------------------------------------------------------------

def _sec_machine(d: dict) -> str:
    cur = d.get("current") or {}
    host = d.get("host") or {}
    L = ["MACHINE"]
    if host.get("uptime_hours") is not None:
        h = float(host["uptime_hours"])
        L.append(f"  windows up   {h / 24:.1f} days" if h >= 48 else
                 f"  windows up   {h:.1f} hours")
    L.append(f"  cpu          {_bar(cur.get('cpu_pct'))} {_n(cur.get('cpu_pct'), '%')}"
             + (f"   {_n(host.get('cpu_temp_c'), 'C')}" if host.get("cpu_temp_c") else "   (no temp sensor)"))
    L.append(f"  ram          {_bar(cur.get('ram_pct'))} {_n(cur.get('ram_pct'), '%')}"
             + (f"   {host.get('ram_used_gb')} / {host.get('ram_total_gb')} GB"
                if host.get("ram_total_gb") else ""))
    vm = d.get("vm") or {}
    if vm.get("pct") is not None:
        L.append(f"  docker vm    {_bar(vm['pct'])} {_n(vm['pct'], '%')}"
                 f"   {vm.get('used_mb')} / {vm.get('total_mb')} MB")
    L.append(f"  source       {'Windows' if d.get('src') == 'windows' else 'the Docker VM (run hostmetrics.cmd -Install for real numbers)'}")
    return "\n".join(L)


def _sec_gpu(d: dict) -> str:
    cur = d.get("current") or {}
    L = ["GPU"]
    L.append(f"  vram         {_bar(cur.get('gpu_vram_pct'))} "
             f"{_n(cur.get('gpu_vram_pct'), '%')}   {_gb(cur.get('gpu_vram_mb'))}")
    L.append(f"  load         {_bar(cur.get('gpu_util'))} {_n(cur.get('gpu_util'), '%')}")
    for label, key, unit in (("temperature", "gpu_temp", "C"),
                             ("power", "gpu_power", "W")):
        if cur.get(key) is not None:
            L.append(f"  {label:<12} {_n(cur.get(key), unit, 1)}")
    models = d.get("loaded_models") or []
    if models:
        L.append("  loaded now:")
        for m in models:
            L.append(f"    {m.get('name')}  {_gb(m.get('size_mb'))}")
    elif models is not None:
        L.append("  loaded now:  nothing (models unload after OLLAMA_KEEP_ALIVE)")
    return "\n".join(L)


def _sec_containers(d: dict) -> str:
    L = ["CONTAINERS"]
    per = d.get("per_container") or []
    if not per:
        L.append("  no per-container data (is netdata running?)")
        return "\n".join(L)
    # Busiest first: the one using your machine is the one you want to see.
    for c in sorted(per, key=lambda x: -(x.get("cpu_pct") or 0)):
        L.append(f"  {(c.get('name') or '?'):<18} cpu {_n(c.get('cpu_pct'), '%', 1):>7}"
                 f"   mem {_gb(c.get('mem_mb')):>7}")
    down = d.get("down_names")
    if down:
        L.append("")
        L.append(f"  DOWN: {down}")
    return "\n".join(L)


def _sec_disks(d: dict) -> str:
    host = d.get("host") or {}
    L = ["DISKS"]
    drives = host.get("drives") or []
    if not drives:
        L.append("  not reported - needs hostmetrics.cmd -Install")
    for drv in drives:
        L.append(f"  {(str(drv.get('name')) + ':'):<4} {_bar(drv.get('pct_used'))} "
                 f"{_n(drv.get('pct_used'), '%'):>4}   "
                 f"{drv.get('free_gb')} GB free of {drv.get('total_gb')} GB")
    dk = d.get("docker_disk") or []
    if dk:
        L.append("")
        L.append("  docker")
        for x in dk:
            L.append(f"    {(x.get('type') or '?'):<16} {x.get('size')}"
                     f"   ({x.get('reclaimable')} reclaimable)")
    return "\n".join(L)


def _sec_network(d: dict) -> str:
    cur = d.get("current") or {}
    host = d.get("host") or {}
    L = ["NETWORK"]
    L.append(f"  now          {_n(cur.get('net_rx_mbps'), ' Mbps down', 2)}"
             f" / {_n(cur.get('net_tx_mbps'), ' Mbps up', 2)}")
    for n in host.get("net") or []:
        L.append(f"  {(n.get('name') or '?'):<12} link {n.get('link_mbps')} Mbps")
    w = host.get("wifi") or {}
    if w:
        L.append(f"  wifi         {w.get('ssid')}  signal {w.get('signal_pct')}%"
                 f"  {w.get('radio') or ''} ch{w.get('channel') or '?'}")
    lat = host.get("latency_ms") or {}
    for k, v in lat.items():
        L.append(f"  ping {k:<11} {_n(v, ' ms')}")
    if not host:
        L.append("  needs hostmetrics.cmd -Install for real NIC numbers")
    return "\n".join(L)


def _sec_models(d: dict) -> str:
    L = ["MODELS"]
    llm = d.get("llm") or {}
    if not llm:
        L.append("  no usage recorded since LiteLLM last restarted")
    for route, v in sorted(llm.items(), key=lambda kv: -((kv[1] or {}).get("requests") or 0)):
        v = v or {}
        bits = []
        if v.get("requests") is not None:
            bits.append(f"{int(v['requests'])} calls")
        if v.get("tokens_in"):
            bits.append(f"in {int(v['tokens_in']):,}")
        if v.get("tokens_out"):
            bits.append(f"out {int(v['tokens_out']):,}")
        if v.get("latency_avg_s"):
            bits.append(f"{v['latency_avg_s']:.1f}s avg")
        L.append(f"  {route:<14} " + "  ".join(bits))
    routes = d.get("routes") or []
    if routes:
        L.append("")
        L.append("  configured routes")
        for r in routes:
            mark = "ok     " if r.get("installed") else "MISSING"
            L.append(f"    {mark} {r.get('route'):<14} {r.get('model')}")
    return "\n".join(L)


def _sec_printer(d: dict) -> str:
    pr = d.get("printer") or {}
    L = ["PRINTER"]
    if not pr:
        L.append("  no print server (printserver.cmd -Install)")
        return "\n".join(L)

    # Staleness FIRST, because everything below it is only as true as this.
    # A status file written three hours ago describes a printer that has since
    # been switched off, fixed, or refilled -- and reading "Jammed" off a
    # stale file sends you to look at a printer that is perfectly fine.
    age = pr.get("age_min")
    if isinstance(age, (int, float)) and age > 5:
        when = (f"{int(age)} min" if age < 120 else f"{age / 60:.1f} hours")
        L.append(f"  STALE - this is {when} old. The print server is not running.")
        L.append("    printserver.cmd -Install")
        L.append("")
    if pr.get("queue_pending"):
        L.append(f"  {pr['queue_pending']} file(s) WAITING in the approved queue")
        if isinstance(age, (int, float)) and age > 5:
            L.append("    nothing will print until the print server is running")
        L.append("")
    # Only advise the Windows print log if the Windows watcher is what is
    # writing this file. Under CUPS that advice is actively wrong -- the
    # scheduled task is gone, and household prints are detected from the
    # printer's own page counter instead, which sees far more than the
    # Windows log ever could.
    if pr.get("source") != "cups" and pr.get("print_log_enabled") is False:
        L.append("  Windows' print log is OFF - no record of who printed what")
        L.append("    printserver.cmd -EnableLog   (as Administrator, once)")
        L.append("")
    L.append(f"  {pr.get('printer') or '?'}")
    for e in (pr.get("errors") or [])[:2]:
        # CUPS says WHY, unlike the Windows spooler. Surfacing that here is
        # the entire reason for switching -- burying it defeats the point.
        for ln in str(e).split("\n")[:8]:
            L.append(f"    {ln}")
    L.append(f"  state        {pr.get('state')}")
    if pr.get("queued") is not None:
        L.append(f"  queue        {pr['queued']} waiting")
    for s in pr.get("supplies") or []:
        if isinstance(s.get("pct"), (int, float)):
            L.append(f"  {(s.get('name') or 'toner'):<12} {_bar(s['pct'])} {int(s['pct'])}%")
    # In flight, and the last thing we actually know the outcome of.
    #
    # These two lines are the answer to "did it print?", which is a different
    # question from "is the printer OK?" and used to be unanswerable here.
    # `lp` accepting a file proves nothing; a job reaching 'completed' is the
    # first moment anyone knows paper came out.
    if pr.get("jobs_active"):
        L.append(f"  in flight    {pr['jobs_active']} job(s) sent, not finished yet")
    lj = pr.get("last_job") or {}
    if lj.get("name"):
        when = str(lj.get("at") or "")[11:16]
        if lj.get("ok"):
            pg = f"{lj['pages']}p" if isinstance(lj.get("pages"), int) else "printed"
            L.append(f"  last print   {when}  {str(lj['name'])[:28]}  {pg}  CONFIRMED")
        else:
            L.append(f"  last print   {when}  {str(lj['name'])[:28]}  "
                     f"DID NOT PRINT ({lj.get('state')})")
            if lj.get("reason"):
                L.append(f"    {str(lj['reason'])[:60]}")
    jobs = pr.get("today") or []
    if jobs:
        L.append("  today:")
        for j in jobs[:8]:
            who = (j.get("user") or "?").split("\\")[-1]
            L.append(f"    {(j.get('time') or '')[11:16]}  {who}  "
                     f"{(j.get('document') or '?')[:28]}  {j.get('pages') or '?'}p")
    return "\n".join(L)


def _sec_history(d: dict) -> str:
    rows = d.get("rows") or []
    L = ["LAST 24 HOURS"]
    if not rows:
        L.append("  nothing recorded yet")
        return "\n".join(L)
    for key in ("cpu", "ram", "disk", "gpu", "vram", "temp", "net", "ping"):
        col, label, unit, is_pct = METRICS[key]
        s = col_stats(rows, col)
        if not s.get("n"):
            continue
        vals, _ = bucket(rows, col, 28)
        line = sparkline(vals, 0.0 if is_pct else None, 100.0 if is_pct else None)
        t = trend(rows, col)
        arrow = {"up": " ^", "down": " v"}.get(t["dir"], "")
        L.append(f"  {label:<16}{line} {s['now']:>5.0f}{unit} peak {s['max']:.0f}{unit}{arrow}")
    runs = outages(rows)
    L.append("")
    if runs:
        L.append(f"  {len(runs)} outage(s):")
        for r in runs[:6]:
            mins = max(2, (r["end"] - r["start"]) // 60)
            from datetime import datetime
            hm = lambda t: datetime.fromtimestamp(t).strftime("%H:%M")
            L.append(f"    {hm(r['start'])}-{hm(r['end'])} ({mins}m) "
                     f"{', '.join(r['names']) or 'unknown'}")
    else:
        L.append("  nothing went down")
    return "\n".join(L)


SECTIONS: dict[str, tuple[str, Any]] = {
    "machine":    ("Machine",    _sec_machine),
    "gpu":        ("GPU",        _sec_gpu),
    "containers": ("Containers", _sec_containers),
    "disks":      ("Disks",      _sec_disks),
    "network":    ("Network",    _sec_network),
    "models":     ("Models",     _sec_models),
    "printer":    ("Printer",    _sec_printer),
    "history":    ("Last 24h",   _sec_history),
}


def format_section(d: dict, name: str) -> str:
    entry = SECTIONS.get(name)
    if not entry:
        return ("Don't know that section. Try: " + ", ".join(SECTIONS))
    return entry[1](d)


def format_stats_full(d: dict) -> list[str]:
    """Every section, packed into pages that fit Telegram's 4096 limit.

    Returns a LIST. Sections are never split across pages -- a container
    table cut in half mid-row is worse than one extra message -- so pages
    are packed greedily and a single oversized section gets its own page.
    """
    probs = d.get("problems") or []
    head = ["PROBLEMS"] + [f"  ! {p}" for p in probs] if probs else ["All good."]
    blocks = ["\n".join(head)] + [fn(d) for _, fn in SECTIONS.values()]

    pages: list[str] = []
    cur = ""
    for b in blocks:
        if not b.strip():
            continue
        if cur and len(cur) + len(b) + 2 > PAGE_CHARS:
            pages.append(cur)
            cur = b
        else:
            cur = f"{cur}\n\n{b}" if cur else b
    if cur:
        pages.append(cur)

    if len(pages) > 1:
        pages = [f"{p}\n\n[{i + 1}/{len(pages)}]" for i, p in enumerate(pages)]
    return pages
