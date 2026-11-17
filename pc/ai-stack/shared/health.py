"""
One screen that answers "is anything wrong right now".

Everything here is a pure function over the JSON that n8n's `stats` webhook
already returns -- no network calls, so it is trivially testable and each
caller (Telegram, Hermes) does its own fetching.

The ordering is deliberate: PROBLEMS FIRST. A health screen that opens with
a tidy table of normal readings buries the one line that matters. Anything
wrong appears at the top with a marker; if nothing is wrong, that is stated
in one line and the detail follows for the curious.
"""

from __future__ import annotations

from typing import Any

# Thresholds. Deliberately conservative -- a warning you see every day stops
# being a warning, so these fire only when something is genuinely worth a
# look on a home machine that runs a phone line.
DISK_WARN_PCT = 85
RAM_WARN_PCT = 90
VRAM_WARN_PCT = 92
GPU_TEMP_WARN_C = 83      # 4070 throttles around 83-87
CPU_TEMP_WARN_C = 90
LATENCY_WARN_MS = 120
WIFI_WARN_PCT = 40        # below this, Wi-Fi renegotiates down and packets retry
RESTART_WARN = 10
RESTART_SETTLED_MIN = 60   # up this long = whatever happened is over
HOST_STALE_MIN = 30       # host-metrics.json older than this is not current


def _pct(v: Any) -> float | None:
    try:
        f = float(str(v).replace("%", "").strip())
        return f
    except (TypeError, ValueError):
        return None


def problems(d: dict) -> list[str]:
    """Everything currently wrong, worst first. Empty list means healthy."""
    out: list[str] = []
    if not d:
        return ["no data at all - could not reach the stats endpoint"]

    if d.get("docker_ok") is False:
        out.append("CANNOT READ DOCKER - monitoring itself is broken")
        for e in (d.get("errors") or [])[:2]:
            out.append("   " + str(e)[:120])

    down = d.get("down_names") or []
    if down:
        out.append(f"DOWN: {', '.join(down)}")

    # A restart LOOP is "restarting repeatedly AND not staying up".
    #
    # Docker's RestartCount never resets -- it accumulates for the entire life
    # of the container -- so a count alone marks anything that has been through
    # ten PC reboots as permanently broken. Caddy showed "restart loop: 22
    # times" while having been up continuously for 34 hours, which is the exact
    # shape of a warning you learn to scroll past. Requiring a short CURRENT
    # uptime as well is what keeps this meaningful.
    for r in d.get("looping") or []:
        count = r.get("count") or 0
        if count < RESTART_WARN:
            continue
        up_min = r.get("up_min")
        if isinstance(up_min, (int, float)) and up_min >= RESTART_SETTLED_MIN:
            continue
        age = (f" (up only {int(up_min)} min)"
               if isinstance(up_min, (int, float)) else "")
        out.append(f"restart loop: {r.get('name')} has restarted "
                   f"{count} times{age}")

    host = d.get("host") or {}
    for drv in host.get("drives") or []:
        used = _pct(drv.get("pct_used"))
        if used is not None and used >= DISK_WARN_PCT:
            out.append(f"disk {drv.get('name')}: {used:.0f}% full "
                       f"({drv.get('free_gb')} GB free)")

    ram = _pct(host.get("ram_pct"))
    if ram is not None and ram >= RAM_WARN_PCT:
        out.append(f"RAM {ram:.0f}% used ({host.get('ram_used_gb')}/{host.get('ram_total_gb')} GB)")

    vm = d.get("vm") or {}
    vmp = _pct(vm.get("pct"))
    if vmp is not None and vmp >= RAM_WARN_PCT:
        out.append(f"Docker VM memory {vmp:.0f}% used - containers may get killed")

    gpu = d.get("gpu") or {}
    vram = _pct(gpu.get("mem_pct"))
    if vram is not None and vram >= VRAM_WARN_PCT:
        out.append(f"VRAM {vram:.0f}% full - the next model load may fail")
    gt = _pct(gpu.get("temp_c"))
    if gt is not None and gt >= GPU_TEMP_WARN_C:
        out.append(f"GPU {gt:.0f}C - at or near throttling")

    ct = _pct(host.get("cpu_temp_c"))
    if ct is not None and ct >= CPU_TEMP_WARN_C:
        out.append(f"CPU {ct:.0f}C")

    wifi = host.get("wifi") or {}
    sig = _pct(wifi.get("signal_pct"))
    if sig is not None and sig < WIFI_WARN_PCT:
        out.append(f"weak Wi-Fi: {sig:.0f}% signal"
                   + (f" on {wifi.get('ssid')}" if wifi.get("ssid") else "")
                   + " - expect choppy calls before you see an outage")

    if host:
        if host.get("online") is False:
            out.append("INTERNET UNREACHABLE - the phone line and websites are down to the outside world")
        else:
            for target, ms in (host.get("latency_ms") or {}).items():
                if isinstance(ms, (int, float)) and ms >= LATENCY_WARN_MS:
                    out.append(f"slow internet: {ms}ms to {target}")

    age = d.get("host_age_min")
    if isinstance(age, (int, float)) and age > HOST_STALE_MIN:
        out.append(f"host metrics are {int(age)} min old - is hostmetrics.cmd still scheduled?")

    return out


def format_health(d: dict) -> str:
    """The whole picture as plain text, problems first."""
    if not d:
        return ("Could not reach the stats endpoint.\n"
                "  docker compose ps n8n")

    L: list[str] = []
    probs = problems(d)
    if probs:
        L.append("PROBLEMS")
        for p in probs:
            L.append("  ! " + p)
    else:
        L.append("All good - nothing is down, nothing is hot, nothing is full.")
    L.append("")

    # --- GPU ---
    gpu = d.get("gpu") or {}
    L.append("GPU")
    if not gpu:
        L.append("  not reported (is ollama running?)")
    else:
        vram = gpu.get("mem_pct")
        L.append(f"  vram   {gpu.get('mem_used_mb')} / {gpu.get('mem_total_mb')} MB"
                 + (f"  ({vram}%)" if vram is not None else ""))
        bits = []
        if gpu.get("util_pct") is not None:  bits.append(f"load {gpu['util_pct']}%")
        if gpu.get("temp_c") is not None:    bits.append(f"{gpu['temp_c']}C")
        if gpu.get("power_w") is not None:   bits.append(f"{gpu['power_w']}W")
        if gpu.get("fan_pct") is not None:   bits.append(f"fan {gpu['fan_pct']}%")
        if bits:
            L.append("  " + "  ".join(bits))

    # --- machine ---
    host = d.get("host") or {}
    vm = d.get("vm") or {}
    L.append("")
    L.append("Machine")
    if host:
        if host.get("uptime_hours") is not None:
            h = float(host["uptime_hours"])
            L.append(f"  up     {h/24:.1f} days" if h >= 48 else f"  up     {h:.1f} hours")
        if host.get("cpu_pct") is not None:
            t = host.get("cpu_temp_c")
            L.append(f"  cpu    {host['cpu_pct']}%" + (f"  {t}C" if t is not None else "  (no temp sensor)"))
        if host.get("ram_pct") is not None:
            L.append(f"  ram    {host.get('ram_used_gb')} / {host.get('ram_total_gb')} GB  ({host['ram_pct']}%)")
        for drv in host.get("drives") or []:
            L.append(f"  disk {drv.get('name')}: {drv.get('free_gb')} GB free of "
                     f"{drv.get('total_gb')} GB  ({drv.get('pct_used')}% used)")
    else:
        L.append("  Windows metrics not available (disk, CPU temp, uptime, internet).")
        L.append("  Set up: hostmetrics.cmd  -- then autostart.cmd -Install")
    if vm.get("pct") is not None:
        L.append(f"  docker vm ram  {vm.get('used_mb')} / {vm.get('total_mb')} MB  ({vm['pct']}%)")

    # --- network ---
    L.append("")
    L.append("Network")
    if host.get("net"):
        for n in host["net"]:
            rate = ""
            if n.get("rx_mbps") is not None:
                rate = f"  {n['rx_mbps']} down / {n['tx_mbps']} up Mbps"
            L.append(f"  {n.get('name')}  link {n.get('link_mbps')} Mbps{rate}")
    wifi = host.get("wifi") or {}
    if wifi:
        rate = ""
        if wifi.get("rx_rate") is not None:
            rate = f"  {wifi['rx_rate']}/{wifi.get('tx_rate')} Mbps rx/tx"
        L.append(f"  wifi   {wifi.get('ssid')}  signal {wifi.get('signal_pct')}%"
                 f"  {wifi.get('radio') or ''} ch{wifi.get('channel') or '?'}{rate}")

    lat = host.get("latency_ms") or {}
    if lat:
        L.append("  latency  " + ", ".join(
            f"{k} {v}ms" if v is not None else f"{k} no reply" for k, v in lat.items()))
    elif not host:
        L.append("  (needs the Windows collector)")

    # --- docker ---
    L.append("")
    L.append("Containers")
    conts = d.get("containers") or []
    running = [c for c in conts if c.get("state") == "running"]
    L.append(f"  {len(running)} running of {len(conts)}")
    for u in (d.get("uptime") or [])[:3]:
        L.append(f"    {u.get('name')} up {u.get('up')}")
    for dk in d.get("disk") or []:
        L.append(f"  docker {dk.get('type')}: {dk.get('size')} "
                 f"({dk.get('reclaimable')} reclaimable)")

    return "\n".join(L)
