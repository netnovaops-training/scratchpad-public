#!/usr/bin/env python3
"""egress_watch.py - every 5 minutes: is the NUC uploading far more than normal, or is one client pulling
a bulk download through Caddy? (approved security fix 4, 2026-11-24)

    python3 ~/hwmon/egress_watch.py              (cron */5, user server)
    python3 ~/hwmon/egress_watch.py --dry        print what it would alert, send nothing, save nothing
    python3 ~/hwmon/egress_watch.py --calibrate  re-print the 7-day upload percentiles from sar (sysstat)

Same pattern as hw_watch.py / vpn_watch.py: ONLY CHANGES ALERT. A condition mails once when it starts and once
when it clears (state in ~/.egress-watch.json). Mail: the note goes to ~/egress-watch.alert and
~/learning-src/nuc_alert_mail.py is run at once (its ~/*.alert glob picks the file up; no edit there needed).

1. Upload rate. Source: the kernel counters of the PHYSICAL interfaces (/sys/class/net/<if>/device exists;
   today only eno1), read on each run; rate = bytes sent since the last run / seconds (a 5-minute average).
   Why not Prometheus: the node-exporter container is on the nuc-homelab_default bridge, not the host network,
   so node_network_transmit_bytes_total{job="nuc"} is the container's own eth0 (0.6 kB/s) - not the NUC.
   (wall_snapshot.py's nuc_upl reads the same series; it under-reports for the same reason.)
   Physical NICs only, so Docker bridges / veths (internal) and tailscale0 (its packets also leave via eno1)
   are never counted twice - the same aim as wall_snapshot.py's NET_EXCL.
   Threshold 1.65 MB/s (~13 Mbit/s), from sar 10-minute eno1 txkB/s, 2026-11-17 00:10 to 11-24 05:00 UTC
   (1,029 samples): p50 24.6, p95 58.5, p99 3,282 kB/s with every sample; the top samples are all 01:40-02:00 UTC,
   the nightly Kopia snapshot to the NAS (01:30 UTC cron), LAN traffic. Without 01:30-02:30 UTC (981 samples):
   p99 549 kB/s, max 28.4 MB/s. 3 x p99 = 1.65 MB/s. Over it in those 7 days: 6 samples in 4 episodes
   (11-17 05:10, 11-18 15:20 at 27.7 MB/s, 11-19 23:40, 11-23 22:20) = about one mail pair every 2 days.
   01:30-02:30 NUC local time is skipped (Kopia to the NAS, cron 30 1 * * *). The NUC moved from UTC to
   America/New_York on 2026-11-23 20:28 (/etc/localtime), so the sar spikes above are 01:40 UTC before that
   and 06:40 UTC (01:40 EST) on 11-24. Note: eno1 counts LAN uploads too (NUC to NAS/PC).
2. One client pulling > 5 GB in an hour through Caddy. Source: Loki (127.0.0.1:3100), container caddy-nuc,
   JSON access log: sum of "size" per request.client_ip over the last hour. 7-day check 11-24 (168 hourly
   queries): the biggest hour of any single IP was 0.08 GB (own network), public at most 0.02 GB, so 5 GB is a
   real outlier. Own networks (192.168/16, 100.64/10, 10/8, 172.16/12, 127/8; the NUC's own tools arrive as
   the router hairpin 192.168.1.1) alert at 50 GB instead. Caddy logs a request when it finishes, so a long
   download counts in the hour it ends. Loki down = noted in the state file, no mail.
OUTCOME 2026-11-24: written; first --dry run in the commit message.
"""
import ipaddress
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

H = Path.home()
STATE = H / ".egress-watch.json"
ALERT = H / "egress-watch.alert"
LOKI = "http://127.0.0.1:3100"
DRY = "--dry" in sys.argv
UP_LIMIT = 1.65e6          # bytes/s, 3 x p99 (see docstring)
SKIP_LOCAL = ("01:30", "02:30")  # nightly Kopia snapshot to the NAS (cron 30 1 * * *, system local time)
CADDY_PUBLIC = 5e9         # bytes in 1 h, one public client
CADDY_OWN = 50e9           # bytes in 1 h, one client on our own networks
OWN = [ipaddress.ip_network(n) for n in
       ("192.168.1.0/16", "100.64.0.0/10", "10.0.0.0/8", "172.16.0.0/12", "127.0.0.0/8", "fd7a:115c:a1e0::/48")]
CADDY_Q = ('topk(10, sum by (ip) (sum_over_time({container="caddy-nuc"} |= "handled request" '
           '| json ip="request.client_ip", size="size" | __error__="" | unwrap size [1h])))')


def physical_tx() -> tuple:
    """(sum of tx_bytes over physical, up interfaces, their names)."""
    tot, names = 0, []
    for d in sorted(Path("/sys/class/net").iterdir()):
        if not (d / "device").exists():
            continue  # lo, veth*, docker0, br-*, tailscale0 have no device link
        if (d / "operstate").read_text().strip() != "up":
            continue
        tot += int((d / "statistics" / "tx_bytes").read_text())
        names.append(d.name)
    return tot, names


def own(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(a in n for n in OWN)


def caddy_top() -> list:
    """[(ip, bytes in the last hour)] for the top clients; raises if Loki fails."""
    u = f"{LOKI}/loki/api/v1/query?" + urllib.parse.urlencode({"query": CADDY_Q, "time": int(time.time())})
    with urllib.request.urlopen(u, timeout=60) as r:
        res = json.load(r)["data"]["result"]
    return [(s["metric"].get("ip", "?"), float(s["value"][1])) for s in res]


def _kopia_utc(ts: str) -> bool:
    """True if a sar UTC timestamp "YYYY-MM-DD HH:MM:SS UTC" falls in the Kopia window (01:30-02:30 local
    from 2026-11-23 20:28 on; 01:30-02:30 UTC before, when the NUC ran on UTC)."""
    import calendar
    t = calendar.timegm(time.strptime(ts[:19], "%Y-%m-%d %H:%M:%S"))
    hm = time.strftime("%H:%M", time.gmtime(t) if t < 1795483724 else time.localtime(t))
    return SKIP_LOCAL[0] < hm <= SKIP_LOCAL[1]


def calibrate():
    """Print 7-day eno1-style upload percentiles from sar files (outside the Kopia window too)."""
    rows = []
    for f in sorted(Path("/var/log/sysstat").glob("sa[0-9]*"))[-8:]:
        out = subprocess.run(["sadf", "-d", str(f), "--", "-n", "DEV"], capture_output=True, text=True).stdout
        for line in out.splitlines():
            p = line.split(";")
            if len(p) > 7 and p[3] in physical_tx()[1]:
                rows.append((p[2], float(p[7])))
    for label, vals in (("all", [v for _, v in rows]),
                        ("outside Kopia", [v for t, v in rows if not _kopia_utc(t)])):
        s = sorted(vals)
        if s:
            pc = lambda q: s[min(len(s) - 1, int(q * len(s)))]  # noqa: E731
            print(f"{label}: n {len(s)} p50 {pc(.5):.1f} p95 {pc(.95):.1f} p99 {pc(.99):.1f} max {s[-1]:.1f} kB/s"
                  f" -> 3 x p99 = {3 * pc(.99) / 1024:.2f} MB/s")


def main() -> int:
    if "--calibrate" in sys.argv:
        calibrate()
        return 0
    now = time.time()
    st = json.loads(STATE.read_text()) if STATE.exists() else {}
    active = set(st.get("active", []))
    tx, nics = physical_tx()
    lines, new_active, notes = [], set(), {}

    # 1. upload rate
    hm = time.strftime("%H:%M", time.localtime(now))
    last = st.get("tx")
    rate = None
    if last and 200 <= now - last["ts"] <= 900 and tx >= last["bytes"]:
        rate = (tx - last["bytes"]) / (now - last["ts"])
    if SKIP_LOCAL[0] < hm <= SKIP_LOCAL[1]:
        new_active |= {a for a in active if a == "upload"}  # hold state through the Kopia window
        notes["upload"] = "skipped (Kopia window)"
    elif rate is not None:
        notes["upload"] = f"{rate / 1e6:.2f} MB/s on {','.join(nics)}"
        if rate > UP_LIMIT:
            new_active.add("upload")
            if "upload" not in active:
                lines.append(f"upload from the NUC {rate / 1e6:.1f} MB/s over the last 5 min "
                             f"(limit {UP_LIMIT / 1e6:.2f} MB/s = 3 x 7-day p99) on {','.join(nics)}")
        elif "upload" in active:
            lines.append(f"cleared: upload back to {rate / 1e6:.2f} MB/s")

    # 2. one client, Caddy, last hour
    try:
        top = caddy_top()
        notes["caddy"] = f"top client {max((b for _, b in top), default=0) / 1e9:.2f} GB/h"
        for ip, b in top:
            lim = CADDY_OWN if own(ip) else CADDY_PUBLIC
            key = f"caddy:{ip}"
            if b > lim:
                new_active.add(key)
                if key not in active:
                    lines.append(f"one client pulled {b / 1e9:.1f} GB through Caddy in the last hour: {ip} "
                                 f"({'own network' if own(ip) else 'public'}; limit {lim / 1e9:.0f} GB). "
                                 f"Grafana Explore: {{container=\"caddy-nuc\"}} |= \"{ip}\"")
        for key in active - new_active:
            if key.startswith("caddy:"):
                lines.append(f"cleared: {key[6:]} under its Caddy limit for the last hour")
    except Exception as e:  # Loki down: keep the old caddy state, no mail
        notes["caddy"] = f"loki error: {type(e).__name__}"
        new_active |= {a for a in active if a.startswith("caddy:")}

    if DRY:
        print(json.dumps({"nics": nics, "notes": notes, "active": sorted(new_active), "would_mail": lines}, indent=1))
        return 0
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"tx": {"ts": now, "bytes": tx}, "active": sorted(new_active), "notes": notes,
                               "checked": time.strftime("%F %T")}))
    os.replace(tmp, STATE)
    if not lines:
        return 0
    text = "\n".join(f"{time.strftime('%F %T')} egress-watch: {x}" for x in lines)
    text += "\n\nWhat to do: check who is connected (Grafana > Explore > Loki, container caddy-nuc) and what is " \
            "running (docker stats, nethogs). Rule details: ~/hwmon/egress_watch.py docstring.\n"
    with ALERT.open("a") as a:
        a.write(text + "\n")
    print(text, flush=True)
    subprocess.run(["python3", str(H / "learning-src" / "nuc_alert_mail.py")], cwd=H / "learning-src",
                   capture_output=True, timeout=120)
    return 0


if __name__ == "__main__":
    sys.exit(main())
