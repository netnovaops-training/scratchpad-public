#!/usr/bin/env python3
"""Fix the NUC Kuma: correct the Pi 3's address, and move power alerting here.

TWO PROBLEMS, both found 2026-09-13.

1. "Pi 3 (tor)" pings 192.168.1.237. The Pi 3 is at 192.168.1.67 -- its
   MAC is b8:27:eb:e9:e3:7e, the Raspberry Pi Foundation OUI, and it
   answers SSH there. So the monitor has been reporting DOWN for a box
   that is up. Nothing lives at .237 at all.

2. The power (brownout) push monitors live on the PI 5's Kuma, and the
   Pi 5 is off the network. So the power-alerting system died with the
   host it was watching -- and the Pi 3's watcher has been pushing into
   nothing (curl rc=7). Push monitors for hardware faults belong on the
   box that is NOT the one being watched. That is the NUC.

Kuma must be STOPPED while this runs: it holds the SQLite open in WAL
mode, and writing underneath a running Kuma is how you corrupt it.
"""
import os
import secrets
import sqlite3
import subprocess
import sys
import time

CONTAINER = "nuc-homelab-uptime-kuma-1"
DB_IN_CTR = "/app/data/kuma.db"
WORK = "/tmp/kumafix"

# monitor name -> the address it SHOULD ping (verified on the wire)
ADDRESS_FIX = {
    "Pi 3 (tor)": "192.168.1.67",
}

POWER_HOSTS = [
    ("Power: Pi 5 (sachin)", "under-voltage / throttling on the Pi 5"),
    ("Power: Pi 3 (tor)",    "under-voltage / throttling on the Pi 3"),
]

FALLBACK_URL = "https://watch.home.example.net/dashboard"


def sh(*args, check=True):
    r = subprocess.run(args, capture_output=True, text=True)
    if check and r.returncode:
        sys.exit(f"FAILED: {' '.join(args)}\n{r.stderr}")
    return r.stdout.strip()


def main():
    os.makedirs(WORK, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")

    print("=== 1. stop Kuma (SQLite in WAL mode must not be written live) ===")
    sh("docker", "stop", CONTAINER)
    print("    stopped")

    print("\n=== 2. copy the database out, with its WAL sidecars ===")
    local = os.path.join(WORK, "kuma.db")
    for suffix in ("", "-wal", "-shm"):
        subprocess.run(["docker", "cp", f"{CONTAINER}:{DB_IN_CTR}{suffix}",
                        local + suffix], capture_output=True)
    sh("cp", local, f"{local}.bak-{stamp}")
    print(f"    backup: {local}.bak-{stamp}")

    con = sqlite3.connect(local)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    print("\n=== 3. correct wrong addresses ===")
    for name, addr in ADDRESS_FIX.items():
        row = cur.execute("SELECT id, hostname FROM monitor WHERE name=?",
                          (name,)).fetchone()
        if not row:
            print(f"    ? no monitor named {name!r}")
            continue
        if row["hostname"] == addr:
            print(f"    = {name} already {addr}")
            continue
        cur.execute("UPDATE monitor SET hostname=? WHERE id=?", (addr, row["id"]))
        print(f"    ~ {name}: {row['hostname']} -> {addr}")

    print("\n=== 4. power push monitors, on the NUC this time ===")
    uid = cur.execute("SELECT id FROM user LIMIT 1").fetchone()["id"]
    nrow = cur.execute("SELECT id FROM notification LIMIT 1").fetchone()
    nid = nrow["id"] if nrow else None
    if nid is None:
        print("    WARNING: this Kuma has no notification configured, so these")
        print("    monitors will go red on the dashboard but send nothing.")
    cols = {r[1] for r in cur.execute("PRAGMA table_info(monitor)")}

    tokens = {}
    for name, desc in POWER_HOSTS:
        row = cur.execute("SELECT id, push_token FROM monitor WHERE name=?",
                          (name,)).fetchone()
        if row:
            tokens[name] = row["push_token"]
            print(f"    = {name}  token={row['push_token']}")
            continue
        token = secrets.token_urlsafe(12)[:16]
        vals = {
            "name": name, "type": "push", "push_token": token,
            # Kuma builds its ntfy action from monitorJSON.url; a null url
            # makes ntfy reject the notification with 400 and the alert is
            # silently never delivered.
            "url": FALLBACK_URL,
            "user_id": uid, "active": 1,
            "interval": 120, "retry_interval": 120,
            "maxretries": 2,          # ~5 min of silence before DOWN
            "resend_interval": 0,
            "description": desc,
            "weight": 2000,
            "upside_down": 0,
        }
        use = {k: v for k, v in vals.items() if k in cols}
        cur.execute("INSERT INTO monitor (%s) VALUES (%s)" % (
            ",".join('"%s"' % k for k in use), ",".join("?" * len(use))),
            list(use.values()))
        mid = cur.lastrowid
        if nid:
            cur.execute("INSERT INTO monitor_notification "
                        "(monitor_id, notification_id) VALUES (?,?)", (mid, nid))
        tokens[name] = token
        print(f"    + {name}  token={token}")

    con.commit()
    con.close()

    print("\n=== 5. copy back and restart ===")
    sh("docker", "cp", local, f"{CONTAINER}:{DB_IN_CTR}")
    # Stale WAL/SHM against a replaced main DB is a corruption risk.
    for suffix in ("-wal", "-shm"):
        subprocess.run(["docker", "exec", CONTAINER, "rm", "-f",
                        DB_IN_CTR + suffix], capture_output=True)
    sh("docker", "start", CONTAINER)
    print("    started")

    print("\n=== TOKENS (NUC Kuma, http://192.168.1.86:3001/api/push) ===")
    for k, v in tokens.items():
        print(f"  {k:<26} {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
