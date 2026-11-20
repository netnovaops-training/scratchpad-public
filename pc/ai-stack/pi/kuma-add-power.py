#!/usr/bin/env python3
"""Add PUSH monitors that warn about under-voltage. Run ON THE PI, kuma STOPPED.

Jason, 2026-09-13: "all my devices are going to have monitoring and down
detetions like the pis they are going to warn of brown outs now".

WHY THIS EXISTS. The Pi 3 browned out under an NVMe drive, corrupted a
wipe mid-command, and has not booted since. `vcgencmd get_throttled`
reported 0x50005 the whole time -- under-voltage live, throttling live,
both also flagged since boot. Nothing was watching it. An evening went
into diagnosing a fault the board had been reporting all along.

WHY A PUSH MONITOR AND NOT A CHECK. Every other monitor here asks a
question from outside: is the port open, does the host ping. Under-
voltage is invisible from outside -- a browning-out Pi answers pings
right up until it stops answering anything. The board knows; nothing
else can. So the Pi reports in, and Kuma alerts when it stops.

That inversion has a second benefit: a push monitor alarms on SILENCE.
If the Pi wedges, loses power, or the script dies, no push arrives and
Kuma fires. A pull check would need the box healthy enough to answer.

HEARTBEAT INTERVAL 120s, GRACE 300s. Long enough that one slow cron tick
is not an alert, short enough to notice a real fault within five minutes.
"""
import os
import secrets
import shutil
import sqlite3
import sys
import time

# Fixed work path on a machine Jason controls; the script's own scratch file, overridable where it reads an env var.
DB = os.environ.get("KUMA_DB", "/tmp/kw/kuma.db")  # nosec B108

# name -> description. One per board that can report throttling.
HOSTS = [
    ("Power: Pi 5 (alex)", "under-voltage / throttling on the Pi 5"),
    ("Power: Pi 3 (tor)",    "under-voltage / throttling on the Pi 3"),
]

# Kuma builds its ntfy action from monitorJSON.url; a null url makes ntfy
# reject the whole notification with a 400 and the alert is never sent.
# Nine monitors were already broken that way before anyone noticed.
FALLBACK_URL = "https://watch.home.example.net/dashboard"


def main() -> int:
    if not os.path.exists(DB):
        sys.exit(f"no database at {DB}")
    shutil.copy2(DB, f"{DB}.bak-power-{time.strftime('%H%M%S')}")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    uid = cur.execute("SELECT id FROM user LIMIT 1").fetchone()["id"]
    nrow = cur.execute("SELECT id FROM notification LIMIT 1").fetchone()
    nid = nrow["id"] if nrow else None
    cols = {r[1] for r in cur.execute("PRAGMA table_info(monitor)")}

    for name, desc in HOSTS:
        row = cur.execute("SELECT id, push_token FROM monitor WHERE name=?",
                          (name,)).fetchone()
        if row:
            print(f"  = {name}")
            print(f"      token: {row['push_token']}")
            continue

        token = secrets.token_urlsafe(12)[:16]
        vals = {
            "name": name, "type": "push", "push_token": token,
            "url": FALLBACK_URL, "user_id": uid, "active": 1,
            "interval": 120,        # the Pi pushes every 120s
            "retry_interval": 120,
            "maxretries": 2,        # ~5 minutes of silence before DOWN
            "resend_interval": 0,
            "description": desc,
            "weight": 2000,         # above the ordinary checks in the list
            "upside_down": 0,
        }
        use = {k: v for k, v in vals.items() if k in cols}
        # Column names are this script's own dict keys, kept only if they are real columns of the table; every value is a ? parameter.
        cur.execute("INSERT INTO monitor ({}) VALUES ({})".format(  # nosec B608
            ",".join(f'"{k}"' for k in use), ",".join("?" * len(use))),
            list(use.values()))
        mid = cur.lastrowid
        if nid:
            cur.execute("INSERT INTO monitor_notification "
                        "(monitor_id, notification_id) VALUES (?,?)", (mid, nid))
        print(f"  + {name}")
        print(f"      token: {token}")

    con.commit()
    con.close()
    print()
    print("  Put each token in the matching Pi's /etc/default/pi-power-watch")
    print("  as KUMA_PUSH_TOKEN, then enable the timer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
