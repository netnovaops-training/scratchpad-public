#!/usr/bin/env python3
"""Seed the Pi's Uptime Kuma with the checks that would have caught tonight.

Jason, 2026-09-10. Run ON THE PI, as root, with the container STOPPED.

WHY THESE CHECKS AND NOT THE OBVIOUS ONES. On 2026-09-10 haproxy-relay
exited during a reboot and did not come back. Every container on the PC
stayed up and healthy, every internal check would have been green, and the
entire public presence was gone for hours. Nobody noticed until Jason tried
to load a page.

So the checks here are deliberately from the OUTSIDE IN, in three layers,
and the layering is the useful part -- when something breaks, the pattern
of what is red tells you where it broke without any investigation:

  L1  public URL over HTTPS      what a visitor actually experiences
  L2  the relay's TCP ports      the NUC hop that failed tonight
  L3  the origin's TCP ports     the PC's Caddy behind the relay

  all of L1 red, L2 red          the relay -- tonight's failure
  all of L1 red, L2 up, L3 red   Caddy on the PC
  all of L1 red, L2 up, L3 up    DNS, or the router forward
  one of L1 red                  just that app

ACCEPTED STATUS CODES ARE 200-399, NOT 200-299. Almost every site here
sits behind forward_auth and answers 303 to the login page when
unauthenticated -- which is the CORRECT response and proof the gate works.
Treating it as an outage would mean a permanently red dashboard, and a
permanently red dashboard is one nobody reads.

THE SCHEMA IS READ, NOT ASSUMED. Kuma's monitor table gains and loses
columns between releases, so this introspects the table and fills only
what is actually there. A hardcoded INSERT works until the image updates.

    sudo systemctl stop docker  # no -- just the container:
    docker stop uptime-kuma-pi
    sudo python3 kuma-seed.py
    docker start uptime-kuma-pi
"""
import json
import os
import sqlite3
import sys

DB = os.environ.get(
    "KUMA_DB",
    "/var/lib/docker/volumes/sachin_kuma_data/_data/kuma.db")

INTERVAL = 60
RETRY = 60
MAXRETRIES = 2

# L1 -- what a visitor gets. Every public hostname Caddy serves.
PUBLIC = [
    "example.org",
    "bloodline.home.example.net",
    "cloud.home.example.net",
    "dashboard.home.example.net",
    "dj.home.example.net",
    "gym.home.example.net",
    "homebox.home.example.net",
    "llm.home.example.net",
    "money.home.example.net",
    "n8n.home.example.net",
    "office.home.example.net",
    "peewee.home.example.net",
    "photos.home.example.net",
    "status.home.example.net",
    "studio.home.example.net",
    "tandoor.home.example.net",
    "watch.home.example.net",
]

# garden.example.org is in the Caddyfile but nothing is deployed behind
# it yet (the rewrite is phase 1 of its own plan). A monitor that is red by
# design teaches you to ignore red.

# L2/L3 -- the hops. THESE are the ones that matter most.
PORTS = [
    ("L2 relay :443  (haproxy on the NUC)", "192.168.1.86", 443),
    ("L2 relay :80   (haproxy on the NUC)", "192.168.1.86", 80),
    ("L3 Caddy :443  (the PC)", "192.168.1.205", 443),
    ("L3 Caddy :80   (the PC)", "192.168.1.205", 80),
    ("NUC ssh :22", "192.168.1.86", 22),
]

ACCEPTED = json.dumps(["200-399"])


def main():
    if not os.path.exists(DB):
        sys.exit(f"no database at {DB}\n"
                 f"(set KUMA_DB, or check the volume name with "
                 f"`docker volume ls`)")

    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    users = cur.execute("SELECT id, username FROM user").fetchall()
    if not users:
        sys.exit("Uptime Kuma has no admin account yet.\n"
                 "Create it first at http://192.168.1.69:3002/setup, "
                 "then re-run this.")
    uid = users[0]["id"]
    print(f"  owner: {users[0]['username']} (id {uid})")

    cols = {r["name"]: r for r in cur.execute("PRAGMA table_info(monitor)")}
    print(f"  monitor table has {len(cols)} columns")

    existing = {r["name"] for r in cur.execute("SELECT name FROM monitor")}
    print(f"  {len(existing)} monitor(s) already defined\n")

    def add(name, **vals):
        """Insert, keeping only the columns this Kuma version actually has."""
        if name in existing:
            print(f"    = {name}  (already there)")
            return 0
        vals["name"] = name
        vals.setdefault("user_id", uid)
        vals.setdefault("active", 1)
        vals.setdefault("interval", INTERVAL)
        vals.setdefault("retryInterval", RETRY)
        vals.setdefault("maxretries", MAXRETRIES)
        vals.setdefault("timeout", 20)
        vals.setdefault("weight", 2000)
        vals.setdefault("upside_down", 0)
        vals.setdefault("expiry_notification", 1)
        use = {k: v for k, v in vals.items() if k in cols}
        dropped = set(vals) - set(use)
        # Built outside the f-string: a backslash inside an f-string
        # expression is a syntax error before Python 3.12, and this needs
        # to run on whatever the Pi happens to ship.
        collist = ",".join(f'"{c}"' for c in use)
        marks = ",".join("?" * len(use))
        # Column names are this script's own dict keys, kept only if they are real columns of the table; every value is a ? parameter.
        q = f"INSERT INTO monitor ({collist}) VALUES ({marks})"  # nosec B608
        cur.execute(q, list(use.values()))
        note = f"   [skipped: {','.join(sorted(dropped))}]" if dropped else ""
        print(f"    + {name}{note}")
        return 1

    n = 0
    print("  L1 -- public URLs, as a visitor sees them:")
    for host in PUBLIC:
        n += add(f"L1 {host}",
                 type="http",
                 url=f"https://{host}/",
                 method="GET",
                 accepted_statuscodes_json=ACCEPTED,
                 ignoreTls=0,
                 maxredirects=0,
                 expiry_notification=1)

    print("\n  L2/L3 -- the hops between the internet and the app:")
    for name, hostname, port in PORTS:
        n += add(name,
                 type="port",
                 hostname=hostname,
                 port=port,
                 accepted_statuscodes_json=ACCEPTED)

    print("\n  DNS -- the third way this breaks:")
    n += add("DNS example.org",
             type="dns",
             hostname="example.org",
             dns_resolve_server="1.1.1.1",
             dns_resolve_type="A",
             port=53,
             accepted_statuscodes_json=ACCEPTED)

    con.commit()
    total = cur.execute("SELECT COUNT(*) c FROM monitor").fetchone()["c"]
    con.close()

    print(f"\n  added {n}, {total} monitors total")
    print("\n  Start the container:  docker start uptime-kuma-pi")
    print("\n  THEN DO THE ONE THING THAT MAKES THIS WORTH ANYTHING:")
    print("  Settings -> Notifications -> add a channel, and tick")
    print("  'Apply on all existing monitors'. Until that is done this is")
    print("  a dashboard, and the outage it exists to catch also hides it.")


if __name__ == "__main__":
    main()
