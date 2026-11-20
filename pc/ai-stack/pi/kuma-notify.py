#!/usr/bin/env python3
"""Attach the ntfy channel to every Kuma monitor. Run ON THE PI, as root,
with the container STOPPED.

Jason, 2026-09-12, away from the machine: "Can you run any of this without
me doing anything manually".

WHY THIS EXISTS AS A SCRIPT rather than six clicks in Settings. The
clicking is genuinely easy -- but the 23 monitors seeded on 2026-09-11
have been running since with no way to reach anybody, which makes them a
dashboard, and a dashboard served through the PC's Caddy is hidden by the
exact outage it exists to report. The gap between "monitors exist" and
"monitors can tell you" is the whole value, and it should not wait on
somebody being at a keyboard.

KUMA HAS NO API FOR THIS. Its admin surface is socket.io, not REST, so
there is no endpoint to POST a notification to. Writing the row directly
is the only way in from outside a browser -- hence the container has to
be stopped first, because Kuma holds the SQLite file open and caches
notification config in memory at startup.

THE CONFIG BLOB IS WHAT KUMA'S OWN UI WOULD HAVE WRITTEN. Field names
come from Kuma's ntfy provider: ntfyserverurl, ntfytopic, ntfyPriority,
ntfyAuthenticationMethod, ntfyaccesstoken. They are not guessable and
they are not consistent in their casing -- that is Kuma's, not a typo
here.

    docker stop uptime-kuma-pi
    sudo python3 kuma-notify.py
    docker start uptime-kuma-pi
"""
import json
import os
import sqlite3
import sys

DB = os.environ.get(
    "KUMA_DB",
    "/var/lib/docker/volumes/alex_kuma_data/_data/kuma.db")

NAME = "Phone (ntfy)"
SERVER = os.environ.get("NTFY_URL", "https://push.home.example.net")
TOPIC = os.environ.get("NTFY_TOPIC", "house")
TOKEN = os.environ.get("NTFY_TOKEN", "")


def main() -> int:
    if not TOKEN:
        sys.exit("set NTFY_TOKEN (the tk_... issued to the kuma account)")
    if not os.path.exists(DB):
        sys.exit(f"no database at {DB}")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    users = cur.execute("SELECT id, username FROM user").fetchall()
    if not users:
        sys.exit("Kuma has no account yet -- nothing to attach a channel to")
    uid = users[0]["id"]

    cfg = {
        "name": NAME,
        "type": "ntfy",
        "isDefault": True,          # so monitors added LATER inherit it
        "applyExisting": True,
        "ntfyserverurl": SERVER,
        "ntfytopic": TOPIC,
        "ntfyPriority": 5,
        "ntfyAuthenticationMethod": "accessToken",
        "ntfyaccesstoken": TOKEN,
        "ntfyIcon": "",
        "ntfyusername": "",
        "ntfypassword": "",
    }

    row = cur.execute("SELECT id FROM notification WHERE name = ?",
                      (NAME,)).fetchone()
    if row:
        nid = row["id"]
        cur.execute("UPDATE notification SET config=?, active=1, is_default=1 "
                    "WHERE id=?", (json.dumps(cfg), nid))
        print(f"  updated notification #{nid} ({NAME})")
    else:
        cur.execute(
            "INSERT INTO notification (name, active, user_id, is_default, config) "
            "VALUES (?,1,?,1,?)", (NAME, uid, json.dumps(cfg)))
        nid = cur.lastrowid
        print(f"  created notification #{nid} ({NAME})")

    # is_default only helps FUTURE monitors. The 23 that already exist have
    # to be linked one by one -- which is exactly what the UI's "Apply on
    # all existing monitors" checkbox does, and exactly what gets forgotten.
    mons = cur.execute("SELECT id, name FROM monitor").fetchall()
    linked = 0
    for m in mons:
        have = cur.execute(
            "SELECT 1 FROM monitor_notification WHERE monitor_id=? AND "
            "notification_id=?", (m["id"], nid)).fetchone()
        if not have:
            cur.execute("INSERT INTO monitor_notification "
                        "(monitor_id, notification_id) VALUES (?,?)",
                        (m["id"], nid))
            linked += 1

    con.commit()
    total = cur.execute(
        "SELECT COUNT(*) c FROM monitor_notification WHERE notification_id=?",
        (nid,)).fetchone()["c"]
    con.close()

    print(f"  linked {linked} new; {total} of {len(mons)} monitors now notify")
    print(f"  server {SERVER}  topic {TOPIC}  priority 5")
    print("\n  start the container:  docker start uptime-kuma-pi")
    return 0


if __name__ == "__main__":
    sys.exit(main())
