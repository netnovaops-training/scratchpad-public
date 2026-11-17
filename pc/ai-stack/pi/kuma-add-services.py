#!/usr/bin/env python3
"""Monitor the services that went down unnoticed in the 2026-09-13 outage.

WHAT THE OUTAGE ACTUALLY EXPOSED. Kuma watched HOSTS -- ping the Pi, ping
the NAS -- and every host came back, so every monitor went green. Meanwhile
Navidrome and Liquidsoap were dead for fifteen hours and Jellyfin for five,
because a host answering ping says nothing about whether the things running
on it are working.

So these are HTTP checks against the actual endpoints, not the boxes.

WHY JELLYFIN'S EXPECTED STATUS IS 302 AND NOT 200. It redirects to its
login page, so 200 would never match and the monitor would sit permanently
red -- a monitor that cries wolf gets muted, and a muted monitor is worse
than none. Same reasoning as the printer's retry window earlier today.

Kuma must be STOPPED while this runs: it holds SQLite in WAL mode and
writing underneath a live Kuma corrupts it.
"""
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

CONTAINER = "nuc-homelab-uptime-kuma-1"
DB = "/app/data/kuma.db"

# name, url, accepted status codes, description
SERVICES = [
    ("Navidrome (music library)", "http://192.168.1.69:4533/", "200-299,302",
     "The DJ library. Dead 15h after the 2026-09-13 outage, unnoticed."),
    ("Radio stream", "http://192.168.1.69:8010/radio", "200-299",
     "Liquidsoap 24/7 stream. Dead 15h after the outage, unnoticed."),
    ("Jellyfin", "http://192.168.1.86:8096/", "200-299,302",
     "Dead 5h after the outage because one library's share would not mount."),
]

FALLBACK_URL = "https://watch.home.example.net/dashboard"


def sh(*a):
    r = subprocess.run(a, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"FAILED {' '.join(a)}\n{r.stderr}")
    return r.stdout.strip()


print("=== stopping Kuma (WAL-mode SQLite must not be written live) ===")
sh("docker", "stop", CONTAINER)

tmp = tempfile.mkdtemp()
local = os.path.join(tmp, "kuma.db")
for suffix in ("", "-wal", "-shm"):
    subprocess.run(["docker", "cp", f"{CONTAINER}:{DB}{suffix}", local + suffix],
                   capture_output=True)
shutil.copy2(local, local + f".bak-{time.strftime('%H%M%S')}")

con = sqlite3.connect(local)
con.row_factory = sqlite3.Row
cur = con.cursor()

uid = cur.execute("SELECT id FROM user LIMIT 1").fetchone()["id"]
nrow = cur.execute("SELECT id FROM notification LIMIT 1").fetchone()
nid = nrow["id"] if nrow else None
cols = {r[1] for r in cur.execute("PRAGMA table_info(monitor)")}

print()
for name, url, accepted, desc in SERVICES:
    if cur.execute("SELECT id FROM monitor WHERE name=?", (name,)).fetchone():
        print(f"  = {name}")
        continue
    vals = {
        "name": name, "type": "http", "url": url,
        "accepted_statuscodes_json": '["' + '","'.join(accepted.split(",")) + '"]',
        "user_id": uid, "active": 1,
        "interval": 120,
        "retry_interval": 120,
        # ~6 minutes of failure before it shouts. Long enough that a restart
        # or a scan is not an alert, short enough to catch a real death.
        "maxretries": 3,
        "resend_interval": 0,
        "description": desc,
        "method": "GET",
        "timeout": 30,
        "upside_down": 0,
        "weight": 1500,
    }
    use = {k: v for k, v in vals.items() if k in cols}
    cur.execute("INSERT INTO monitor (%s) VALUES (%s)" % (
        ",".join('"%s"' % k for k in use), ",".join("?" * len(use))),
        list(use.values()))
    mid = cur.lastrowid
    if nid:
        cur.execute("INSERT INTO monitor_notification (monitor_id, notification_id) "
                    "VALUES (?,?)", (mid, nid))
    print(f"  + {name}  -> {url}")

con.commit()
con.close()

print()
print("=== copying back and restarting ===")
sh("docker", "cp", local, f"{CONTAINER}:{DB}")
for suffix in ("-wal", "-shm"):
    subprocess.run(["docker", "exec", CONTAINER, "rm", "-f", DB + suffix],
                   capture_output=True)
sh("docker", "start", CONTAINER)
print("  started")
shutil.rmtree(tmp, ignore_errors=True)
