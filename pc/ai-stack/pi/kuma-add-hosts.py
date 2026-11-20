#!/usr/bin/env python3
"""Add plain ping monitors for hosts. Run ON THE PI, as root, container STOPPED.

Jason, 2026-09-12: "a simple ping to it will work to save ram".

He is right, and not only about RAM. A ping monitor is one ICMP echo every
60 seconds -- no TCP handshake, no TLS, no HTTP client, no response body
to parse. On a Pi already running eight containers in 4GB that is the
difference between a monitor that costs nothing and one that costs
something, times however many hosts get added over time.

It is also the RIGHT check for these two. A port check answers "is this
service listening", which is the question for DSM or SMB. For a machine
that is not set up yet, and a printer that sleeps, the question is
simply "is it there" -- and a port check on a sleeping printer reports
DOWN for a device that is working perfectly.

USE IT AS AN ARRIVAL ALARM. The Pi 3 is not on the network yet. Rather
than rescanning the /24 by hand every few minutes, this monitor goes in
DOWN, and the moment the Pi boots and takes a lease, Kuma changes state
and pushes a notification. The monitoring finds it instead of us looking.
"""
import os
import sqlite3
import sys

DB = os.environ.get(
    "KUMA_DB", "/var/lib/docker/volumes/alex_kuma_data/_data/kuma.db")

# name, hostname. Ping only.
HOSTS = [
    # 2026-09-12: found by MAC, not by guess. b8:27:eb is the Raspberry
    # Pi Foundation OUI, and .237 carries it -- so .237 is the Pi 3, and
    # .67 (which we had here) is nothing at all. The NAS was under .237 in
    # this same list, which meant its monitor was reporting the Pi 3 was
    # up. A green light about the wrong machine is worse than a red one.
    ("Pi 3 (tor)",   os.environ.get("PI3_IP", "192.168.1.237")),
    ("Printer",      os.environ.get("PRINTER_IP", "192.168.1.211")),
    ("NAS (ping)",   os.environ.get("NAS_IP", "192.168.1.68")),
    ("Router",       "192.168.1.1"),
]

# Kuma builds an ntfy "view" action from monitorJSON.url. A ping monitor
# has no url, so ntfy rejects the whole notification with
#   400 actions invalid; parameter 'url' is required for action 'view'
# and the alert is silently never delivered. Nine monitors were already
# broken this way before it was noticed. Every monitor gets a url.
FALLBACK_URL = "https://watch.home.example.net/dashboard"


def main() -> int:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    uid = cur.execute("SELECT id FROM user").fetchone()["id"]
    nrow = cur.execute("SELECT id FROM notification").fetchone()
    nid = nrow["id"] if nrow else None
    cols = {r[1] for r in cur.execute("PRAGMA table_info(monitor)")}

    added = 0
    for name, host in HOSTS:
        if cur.execute("SELECT 1 FROM monitor WHERE name=?", (name,)).fetchone():
            print(f"  = {name} already there")
            continue
        vals = {"name": name, "type": "ping", "hostname": host,
                "url": FALLBACK_URL, "user_id": uid, "active": 1,
                "interval": 60, "maxretries": 2, "timeout": 10,
                "weight": 1000, "upside_down": 0}
        use = {k: v for k, v in vals.items() if k in cols}
        # Column names are this script's own dict keys, kept only if they are real columns of the table; every value is a ? parameter.
        cur.execute("INSERT INTO monitor ({}) VALUES ({})".format(  # nosec B608
            ",".join(f'"{k}"' for k in use), ",".join("?" * len(use))),
            list(use.values()))
        mid = cur.lastrowid
        if nid:
            cur.execute("INSERT INTO monitor_notification "
                        "(monitor_id, notification_id) VALUES (?,?)", (mid, nid))
        print(f"  + {name:<14} ping {host}")
        added += 1

    con.commit()
    total = cur.execute("SELECT COUNT(*) c FROM monitor").fetchone()["c"]
    con.close()
    print(f"  added {added}; {total} monitors total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
