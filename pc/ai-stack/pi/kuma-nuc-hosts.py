#!/usr/bin/env python3
"""Add host monitors to the NUC's Kuma. Run ON THE NUC, container STOPPED.

WHY THIS EXISTS, AND WHY IT IS NOT THE PI SCRIPT.

There are two Uptime Kuma instances on this network:

  * the Pi 5's  -- 30 monitors: every host, the NAS ports, the printer,
                   the ping checks. The comprehensive one.
  * the NUC's   -- 5 monitors: the public HTTP endpoints. That is all.

On 2026-09-12 the Pi 5 went offline. Nothing reported it. It could not
report it: the monitors that watch the Pi 5 RUN ON the Pi 5, so the
host going down takes its own alarm with it. The failure was found by
hand, hours later, by pinging addresses off a screenshot.

That is the oldest mistake in monitoring, and the fix is not to make
the Pi more reliable. It is to make sure something ELSE is watching.
The NUC has 18GB of RAM, two days of uptime, and is not the machine
being watched -- so the host checks belong here, and the Pi's copy
becomes the redundant one rather than the only one.

These are ping monitors, per Jason: "a simple ping to it will work to
save ram". One ICMP echo a minute: no handshake, no TLS, no HTTP client,
no body to parse. It is also the RIGHT check for a printer that sleeps
-- a port check on a sleeping printer reports DOWN for a device that is
working perfectly.
"""
import os
import shutil
import sqlite3
import sys
import time

# Fixed work path on a machine Jason controls; the script's own scratch file, overridable where it reads an env var.
DB = os.environ.get("KUMA_DB", "/tmp/kuma-work/kuma.db")  # nosec B108

# name, hostname. Addresses verified on the wire 2026-09-12, not taken
# from the router's reservation list -- the list and reality disagree.
HOSTS = [
    # The one that matters most: the box that holds the other Kuma.
    ("Pi 5 (alex)", "192.168.1.69"),
    # The Pi 3 answers at .237, NOT at its .67 reservation. The .67 entry
    # is bound to a MAC that is not this Pi's eth0 (b8:27:eb:e9:e3:7e) --
    # almost certainly its wlan0, from when it was set up over WiFi.
    ("Pi 3 (tor)",    os.environ.get("PI3_IP", "192.168.1.237")),
    ("NAS UltraNova", "192.168.1.68"),
    # .221, not .211. The router reservation ("Jasons Printer") and
    # CUPS (ipp://192.168.1.221/ipp/print) both say .221; .211 was a
    # transposed digit watching an empty address, so it reported DOWN
    # forever including while the printer worked fine.
    #
    # Slow interval on purpose. A consumer printer powers down and drops
    # off the network -- that is it working as designed, not a fault.
    # At 60s/2 retries this fires a Telegram alert every night.
    ("Printer",       "192.168.1.221"),
    ("Router (Deco)", "192.168.1.1"),
]

# Kuma builds an ntfy "view" action out of monitorJSON.url. A ping
# monitor has no url, so ntfy rejects the entire notification with
#   400 actions invalid; parameter 'url' is required for action 'view'
# and the alert is silently never delivered. Nine monitors on the Pi were
# already broken this way before anyone noticed. Every monitor gets a url.
FALLBACK_URL = "https://watch.home.example.net/dashboard"


def main() -> int:
    if not os.path.exists(DB):
        sys.exit(f"no database at {DB}")

    bak = f"{DB}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    shutil.copy2(DB, bak)
    print(f"  backup: {bak}")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    uid = cur.execute("SELECT id FROM user LIMIT 1").fetchone()["id"]
    nrow = cur.execute("SELECT id FROM notification LIMIT 1").fetchone()
    nid = nrow["id"] if nrow else None
    if nid is None:
        print("  !! no notification configured on this Kuma -- monitors will")
        print("     go red on the dashboard and tell nobody. Add one in the")
        print("     UI (Settings -> Notifications) and they attach on save.")

    # Read the schema instead of assuming it. Kuma adds and renames
    # columns between releases, and an INSERT written against one version
    # fails loudly on another -- or worse, succeeds with a wrong default.
    cols = {r[1] for r in cur.execute("PRAGMA table_info(monitor)")}

    added = 0
    for name, host in HOSTS:
        if cur.execute("SELECT 1 FROM monitor WHERE name=?", (name,)).fetchone():
            print(f"  = {name} already there")
            continue
        vals = {
            "name": name, "type": "ping", "hostname": host,
            "url": FALLBACK_URL, "user_id": uid, "active": 1,
            "interval": 60, "maxretries": 2, "timeout": 10,
            "retry_interval": 60, "resend_interval": 0,
            # (the Printer row is retuned to 300s/3 after insert -- see
            #  the note on its entry above)
            "weight": 1000, "upside_down": 0,
        }
        use = {k: v for k, v in vals.items() if k in cols}
        cur.execute(
            # Column names are this script's own dict keys, kept only if they are real columns of the table; every value is a ? parameter.
            "INSERT INTO monitor ({}) VALUES ({})".format(  # nosec B608
                ",".join(f'"{k}"' for k in use), ",".join("?" * len(use))),
            list(use.values()))
        mid = cur.lastrowid
        if nid:
            cur.execute("INSERT INTO monitor_notification "
                        "(monitor_id, notification_id) VALUES (?,?)", (mid, nid))
        print(f"  + {name:<16} ping {host}")
        added += 1

    con.commit()
    total = cur.execute("SELECT COUNT(*) c FROM monitor").fetchone()["c"]
    con.close()
    print(f"  added {added}; {total} monitors total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
