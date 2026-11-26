#!/usr/bin/env python3
"""hw_watch.py - every 5 minutes: is any fan, drive or NAS disk/pool/cache getting worse? (2026-11-24)

    python3 ~/hwmon/hw_watch.py            (cron */5, user server)
    python3 ~/hwmon/hw_watch.py --dry      print what it would alert and the card, send nothing, save nothing

Why: Jason, 11-24: "PC fan noise is louder; I need to monitor all the fans and the HDDs and SSDs now". He expects a
hardware failure within a month. Reads only Prometheus (127.0.0.1:9090): the PC's pc_noise_* (fans, CPU/GPU temps,
pc_noise_logger.ps1) and hw_smart (hw_pc_smart.ps1), the NUC's and NAS's hw_smart / hw_nas_* (nuc_smart_prom.py,
nas_dsm_prom.py). Same pattern as vpn_watch.py: ONLY CHANGES ALERT. A condition mails once when it starts and once
when it clears (state in ~/.hw-watch.json); a SMART error counter mails each time it goes up.

Alert rules and why these numbers (data: D:\\pc-health on the PC, 11-23 15:44 to 11-24 04:50):
  SMART reallocated / pending / uncorrectable / reported_uncorrect, NVMe media_errors: any increase. A healthy
      drive's are 0 for life; a rising count is the classic sign of a drive on its way out.
  SMART verdict failed, NVMe critical_warning not 0: at once.
  Head parks (load_unload) per HDD: more than 2,000 in 24 h, or more than 500 in one hour.
      M: (ZFL24MA5) and G: (ZFL2507H) park ~34 times an hour each since 11-23 17:00 (~820/day). In the bursts
      seen 11-21..11-23 one drive parked ~3,300 an hour. Lifetime average is 4/h (M:) and 0.8/h (G:) over
      11,810 power-on hours. The ST2000DM008 is rated for 600,000 cycles: 2,000/day is 2.5x today's steady rate
      and would use the rating up in ~10 months; 500 in an hour catches a burst inside its first hour.
  Drive temperature: hard drives over 55 C (Seagate/WD desktop max is 60), SSD/NVMe over 70 C (they throttle near
      80; the PC's NVMe idle at 36-46 C, so 55 would mail on every big copy).
  Fans (PC, from the 5-second noise logger):
      pump ("System Fan #4 / Pump") below 3,000 RPM: lowest seen 4,355. A slowing pump is how AIO coolers fail.
      pump swing over 2,500 RPM inside 10 minutes: 11-23/24 the 10-minute swing was 1,750 typical, 2,263 at most
      (it follows CPU temperature, correlation 0.92). Bigger than that = worse than the night Jason heard it.
      CPU Fan or CPU Optional Fan below 800 RPM while the CPU is over 70 C (lowest seen 1,188).
      GPU fan at 0 while the GPU is over 75 C (0 is normal below ~60 C: zero-RPM mode).
      GPU fans starting more than 30 times an hour (11-23 worst hour: 22 starts; each start is audible).
      CPU over 90 C (11-23/24 max 84 C).
      Fan data older than 10 minutes while the PC is up (the noise logger stopped).
  NAS: any pool / volume / SSD cache / disk status change, the disk count changing, or no read for 2 hours.
Writes: ~/alerts/hw-watch.alert (mailed at once by nuc_alert_mail.py), ~/.hw-watch.json (state),
        ~/hwmon/hw-status.json (worst items first; home_cards.py turns it into Jason's "Hardware" tile on /home).
OUTCOME 2026-11-24: written; first dry run listed below in the commit.
"""
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

H = Path.home()
PROM = "http://127.0.0.1:9090"
STATE = H / ".hw-watch.json"
ALERT = H / "alerts" / "hw-watch.alert"
STATUS = H / "hwmon" / "hw-status.json"
DRY = "--dry" in sys.argv
COUNTERS = ("reallocated", "pending", "uncorrectable", "reported_uncorrect", "media_errors")
PUMP = "System Fan #4 / Pump"
PC_FIX = 1795471980   # 2026-11-23 17:13 EST: pc_drive_health.ps1 rows stopped swapping between the Seagates
PC_DISKS = {"0": {"drive": "G:", "serial": "ZFL2507H"}, "1": {"drive": "M:", "serial": "ZFL24MA5"}}  # Get-PhysicalDisk


def q(expr: str) -> list:
    url = f"{PROM}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    return json.loads(urllib.request.urlopen(url, timeout=15).read())["data"]["result"]


def qr(expr: str, seconds: int, step: int = 15) -> list:
    now = time.time()
    url = f"{PROM}/api/v1/query_range?" + urllib.parse.urlencode(
        {"query": expr, "start": now - seconds, "end": now, "step": step})
    return json.loads(urllib.request.urlopen(url, timeout=20).read())["data"]["result"]


def val(r) -> float:
    return float(r["value"][1])


def who(m: dict) -> str:
    """Human name for a drive: 'PC M: (ZFL24MA5)', 'NAS Drive 3 (DX517-1)', 'NUC vault'."""
    mach = {"supernova": "PC", "nuc": "NUC", "nas": "NAS"}.get(m.get("machine"), m.get("machine", "?"))
    ser = f" ({m['serial']})" if m.get("serial") and mach == "PC" else ""
    return f"{mach} {m.get('drive', '?')}{ser}"


def checks():
    """-> (conds, counters, info). conds: key -> (severity 1|2, text) for each condition active NOW.
    counters: key -> (value, name, field) for the SMART error counters. info: always-shown readings."""
    conds, counters, info, idle_bad = {}, {}, [], {}
    smart = q("hw_smart")
    nas_status = {}
    for r in q("hw_nas_state"):
        nas_status[r["metric"].get("name")] = r["metric"].get("status", "")
    for r in smart:
        m, v = r["metric"], val(r)
        f, name = m.get("field"), who(m)
        key = f"{m.get('machine')}|{m.get('serial') or m.get('drive')}|{f}"
        in_use = nas_status.get(m.get("drive"), "normal") != "not_use"
        if f in COUNTERS:
            counters[key] = (v, name, f)
            if v > 0 and in_use:
                conds[f"bad|{key}"] = (2, f"{name}: {f} = {v:g}")
            elif v > 0:   # a drive DSM already took out of use: one line for it, below
                idle_bad.setdefault(m.get("drive"), []).append(f"{f} {v:g}")
        elif f == "passed" and v == 0:
            conds[f"failed|{key}"] = (2, f"{name}: SMART verdict FAILED")
        elif f == "critical_warning" and v != 0:
            conds[f"crit|{key}"] = (2, f"{name}: NVMe critical warning {v:g}")
        elif f == "temp_c":
            limit = 55 if m.get("kind") == "hdd" else 70
            if v > limit:
                conds[f"hot|{key}"] = (2, f"{name}: {v:g} C (limit {limit})")
    hottest = sorted(((val(r), who(r["metric"])) for r in smart if r["metric"].get("field") == "temp_c"), reverse=True)
    if hottest:
        info.append(f"hottest drive {hottest[0][1]} {hottest[0][0]:g} C")

    # head parks, per hard drive. hw_smart once smartctl runs on the PC; until then Windows' own counter.
    parks = {}
    for win, limit in (("24h", 2000), ("1h", 500)):
        rows = q(f'increase(hw_smart{{field="load_unload",kind="hdd"}}[{win}])')
        if not any(r["metric"].get("machine") == "supernova" for r in rows):
            # Fallback: Windows' counter (pc_drive_health.ps1). Before 11-23 17:13 its rows swapped between the two
            # Seagates, which increase() reads as huge jumps, so its window never reaches back past that time.
            hours = min(int(win[:-1]), int((time.time() - PC_FIX) // 3600))
            if hours >= 1:
                rows += [{"metric": {"machine": "supernova", **PC_DISKS.get(r["metric"].get("disk"), {"drive": "disk ?"})},
                          "value": [0, val(r) * int(win[:-1]) / hours]}
                         for r in q(f'increase(pc_drive_value{{counter="load_unload_cycles"}}[{hours}h])')]
        for r in rows:
            n, v = who(r["metric"]), val(r)
            if win == "24h":
                parks[n] = v
            if v > limit:
                conds[f"parks{win}|{n}"] = (1, f"{n}: {v:.0f} head parks in {win} (limit {limit})")
    if parks:
        n, v = max(parks.items(), key=lambda x: x[1])
        info.append(f"most head parks {n} {v:.0f}/24h")

    # PC fans and temperatures (pc_noise_logger.ps1)
    pc_up = any(val(r) == 1 for r in q('up{job="pc"}'))
    fans = {r["metric"].get("fan"): val(r) for r in q("pc_noise_fan_rpm")}
    temps = {r["metric"].get("part"): val(r) for r in q("pc_noise_temp_c")}
    age = q('time() - windows_textfile_mtime_seconds{file=~".*pc_noise.prom"}')
    if pc_up and (not age or val(age[0]) > 600):
        conds["fans|stale"] = (1, "PC fan data stopped (pc_noise_logger not writing for 10+ min)")
    cpu, gpu = temps.get("cpu", 0), temps.get("gpu", 0)
    if pc_up and fans:
        pump = fans.get(PUMP)
        if pump is not None and pump < 3000:
            conds["fans|pump-slow"] = (2, f"PC pump at {pump:.0f} RPM (lowest normal 4,355)")
        sw = q(f'max_over_time(pc_noise_fan_rpm{{fan="{PUMP}"}}[10m]) - min_over_time(pc_noise_fan_rpm{{fan="{PUMP}"}}[10m])')
        if sw and val(sw[0]) > 2500:
            conds["fans|pump-swing"] = (1, f"PC pump swinging {val(sw[0]):.0f} RPM in 10 min (11-23 max 2,263)")
        for fan in ("CPU Fan", "CPU Optional Fan"):
            if fan in fans and fans[fan] < 800 and cpu > 70:
                conds[f"fans|{fan}"] = (2, f"PC {fan} at {fans[fan]:.0f} RPM with CPU {cpu:.0f} C")
        for fan in ("GPU Fan 1", "GPU Fan 2"):
            if fan in fans and fans[fan] == 0 and gpu > 75:
                conds[f"fans|{fan}"] = (2, f"PC {fan} stopped with GPU {gpu:.0f} C")
        starts = 0
        for s in qr('pc_noise_fan_rpm{fan="GPU Fan 1"}', 3600):
            vals = [float(v) for _, v in s["values"]]
            starts = sum(1 for a, b in zip(vals, vals[1:]) if a == 0 and b > 0)
        if starts > 30:
            conds["fans|gpu-cycling"] = (1, f"PC GPU fans started {starts} times in the last hour (11-23 max 22)")
        hot = q('max_over_time(pc_noise_temp_c{part="cpu"}[5m])')
        if hot and val(hot[0]) > 90:
            conds["fans|cpu-hot"] = (2, f"PC CPU at {val(hot[0]):.0f} C (11-23/24 max 84)")
        info.append(f"pump {fans.get(PUMP, 0):.0f} RPM, CPU {cpu:.0f} C, GPU {gpu:.0f} C")

    # NAS pools / caches / volumes / disks: anything not "normal" is a condition; a status change mails.
    nas_ok = q("max_over_time(hw_nas_ok[2h])")
    if not nas_ok or val(nas_ok[0]) == 0:
        conds["nas|unread"] = (1, "NAS not read for 2 hours (nas_dsm_prom.py; see ~/logs/nas-dsm.log)")
    for r in q("hw_nas_state"):
        m = r["metric"]
        st = m.get("status", "")
        if not st.startswith("normal"):
            sev = 1 if m.get("kind") == "disk" and st == "not_use" else 2
            extra = f" ({', '.join(idle_bad[m['name']])})" if st == "not_use" and m.get("name") in idle_bad else ""
            conds[f"nas|{m.get('kind')}|{m.get('name')}|{st}"] = (sev, f"NAS {m.get('name')}: {st}{extra}")
    cnt = q("hw_nas_disk_count")
    if cnt:
        info.append(f"NAS {val(cnt[0]):.0f} disks")
    return conds, counters, info, int(val(cnt[0])) if cnt else None


def main() -> int:
    try:
        conds, counters, info, ndisks = checks()
    except Exception as ex:  # noqa: BLE001 - Prometheus down: change nothing, say so once per run in the log
        print(time.strftime("%F %T"), "hw_watch: Prometheus query failed:", type(ex).__name__, flush=True)
        return 1
    try:
        old = json.loads(STATE.read_text())
    except (OSError, ValueError):
        old = {}
    first = not old
    lines = []
    if first:
        lines.append("hw-watch started. Problems standing right now (later mails are only changes):")
        lines += [f"  {t}" for _, t in sorted(conds.values(), reverse=True)] or ["  none"]
    else:
        for k in sorted(set(conds) - set(old.get("conds", {}))):
            lines.append(f"NEW: {conds[k][1]}")
        for k in sorted(set(old.get("conds", {})) - set(conds)):
            lines.append(f"cleared: {old['conds'][k]}")
        for k, (v, name, f) in counters.items():
            before = old.get("counters", {}).get(k)
            if before is not None and v > before:
                lines.append(f"UP: {name} {f} {before:g} -> {v:g}")
        if ndisks is not None and old.get("ndisks") not in (None, ndisks):
            lines.append(f"NAS disk count {old['ndisks']} -> {ndisks}")
    worst = sorted(conds.values(), key=lambda c: -c[0])
    status = {"at": int(time.time()), "level": max([c[0] for c in worst] or [0]),
              "items": [{"sev": s, "text": t} for s, t in worst] + [{"sev": 0, "text": t} for t in info]}
    if DRY:
        print("\n".join(lines) or "(no change)")
        print(json.dumps(status, indent=1))
        return 0
    new = {"conds": {k: t for k, (_, t) in conds.items()},
           "counters": {k: max(v, old.get("counters", {}).get(k, v)) for k, (v, _, _) in counters.items()},
           "ndisks": ndisks if ndisks is not None else old.get("ndisks")}
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(new))
    os.replace(tmp, STATE)
    STATUS.parent.mkdir(exist_ok=True)
    tmp = STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(status))
    os.replace(tmp, STATUS)
    if not lines:
        return 0
    text = "\n".join(f"{time.strftime('%F %T')} hw-watch: {x}" for x in lines)
    text += "\n\nDashboard: https://example.org/grafana/d/hw-health/\n"
    ALERT.parent.mkdir(exist_ok=True)
    with ALERT.open("a") as a:
        a.write(text + "\n")
    print(text, flush=True)
    subprocess.run(["python3", str(H / "learning-src" / "nuc_alert_mail.py")], cwd=H / "learning-src",
                   capture_output=True, timeout=120)
    return 0


if __name__ == "__main__":
    sys.exit(main())
