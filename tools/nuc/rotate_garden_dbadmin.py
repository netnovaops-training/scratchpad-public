#!/usr/bin/env python3
"""Rotate the Robin's Garden MariaDB password for user dbadmin. T0024 step 2-6, 2026-11-19 autopilot.

The old value was printed in a chat on 2026-09-24 and sits as a literal in docker-compose.yml
(service garden-orchestrator, `- DB_PASS=...`, in git since 95304c8), so it is burned.
The SAME value is MYSQL_PASSWORD in ~/nuc-homelab/db.env (checked by equality 11-16; this script
re-checks and aborts if they differ).

What it does, in this order (nothing is printed but lengths and status):
  1. new value = secrets.token_urlsafe(24) (letters, digits, - and _: safe inside SQL quotes and env files)
  2. saves it FIRST to Proton Pass, vault "NUC services", via `~/bin/pass item create login
     --from-template -` (value on stdin, never on a command line). Pass fails -> abort, nothing changed.
  3. backs up db.env + docker-compose.yml to ~/archive/<date>/garden-dbadmin-rotate-<time>/
  4. ALTER USER 'dbadmin'@'%' (host checked in mysql.user 11-19) as root inside the container
  5. db.env: MYSQL_PASSWORD=<new>, plus DB_PASS=<new> for the orchestrator (one file holds the value)
  6. compose: removes the DB_PASS literal, adds ./db.env to the orchestrator's env_file;
     edited as docker-compose.yml.new, `docker compose config -q` must pass before the swap
  7. test: dbadmin login with the new value works, with the burned value fails
     (value passed on stdin into the container, not as an argument)
  On a failed test: ALTER back to the saved old hash and restore both files from the backup.
  Never print the stdout/stderr of a command that carries a password: on 11-19 a mariadb error
  echoed a whole SQL statement, value included, into the transcript (the burned value, so no new loss).

Nothing is restarted: on 11-19 neither garden-orchestrator nor garden-php can run (their code
folders are gone, no image exists), and no app is connected as dbadmin. They pick up db.env when started.

Usage: rotate_garden_dbadmin.py
"""
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from pathlib import Path

H = os.path.expanduser("~")
NH = f"{H}/nuc-homelab"
DBENV = f"{NH}/db.env"
COMPOSE = f"{NH}/docker-compose.yml"
DB = "nuc-homelab-garden-db-1"
ARCHIVE = f"{H}/archive/{time.strftime('%Y-%m-%d')}/garden-dbadmin-rotate-{time.strftime('%H%M%S')}"
PASS_TITLE = "garden DB dbadmin (rotated {}, T0024)".format(time.strftime("%Y-%m-%d"))
LIT = re.compile(r"^      - DB_PASS=(?!\$)(\S+)\n", re.MULTILINE)


def sh(cmd, inp=None, cwd=None):
    return subprocess.run(cmd, input=inp, cwd=cwd, capture_output=True, text=True, check=False)


def root_sql(sql):
    # root password comes from the container's own env, never from here
    return sh(["docker", "exec", "-i", DB, "sh", "-c", 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD"'], inp=sql)


def can_login(pw):
    r = sh(["docker", "exec", "-i", DB, "sh", "-c",
            'read -r P; MYSQL_PWD="$P" mariadb -udbadmin -N -e "SELECT 1" juns_garden_db'], inp=pw + "\n")
    return r.returncode == 0


def alter(pw):
    return root_sql(f"ALTER USER 'dbadmin'@'%' IDENTIFIED BY '{pw}'; FLUSH PRIVILEGES;\n").returncode == 0


def restore_hash(old_hash):
    """Put dbadmin back on its pre-rotation password by hash (the clear value is unknown, see main)."""
    return root_sql(f"ALTER USER 'dbadmin'@'%' IDENTIFIED BY PASSWORD '{old_hash}'; FLUSH PRIVILEGES;\n").returncode == 0


def main():
    env = Path(DBENV).read_text()
    comp = Path(COMPOSE).read_text()
    m_env = re.search(r"^MYSQL_PASSWORD=(\S+)$", env, re.MULTILINE)
    m_lit = LIT.search(comp)
    if not m_env or not m_lit:
        sys.exit("ABORT: MYSQL_PASSWORD in db.env or the DB_PASS literal in compose not found")
    old = m_env.group(1)
    if m_lit.group(1) != old:
        sys.exit("ABORT: compose literal and db.env MYSQL_PASSWORD differ (expected one value)")
    if re.search(r"^DB_PASS=", env, re.MULTILINE):
        sys.exit("ABORT: db.env already has DB_PASS=")
    # OUTCOME 2026-11-19: the first run stopped here on a check "dbadmin can log in with the db.env
    # value". It cannot: a boolean-only SQL check (authentication_string = PASSWORD(<db.env value>))
    # returned 0, so the DB's live dbadmin password is some other, unknown value (the DB was likely
    # re-created after 11-16), and the burned value lives only in db.env, compose and git. Nothing
    # connects as dbadmin (processlist, grep of NUC compose/scripts). So instead of logging in with
    # the old value, the old HASH is saved to the archive (mode 600) and is what a rollback restores.
    os.makedirs(ARCHIVE, mode=0o700, exist_ok=True)
    h = root_sql("SELECT CONCAT('H:', authentication_string) FROM mysql.user "
                 "WHERE User='dbadmin' AND Host='%';\n")
    hashes = [ln[2:] for ln in h.stdout.splitlines() if ln.startswith("H:")]
    if h.returncode != 0 or len(hashes) != 1 or not re.fullmatch(r"\*[0-9A-F]{40}", hashes[0]):
        sys.exit("ABORT: could not read one mysql_native_password hash for dbadmin@%")
    old_hash = hashes[0]
    with open(f"{ARCHIVE}/dbadmin.hash", "w") as f:
        f.write(old_hash + "\n")
    os.chmod(f"{ARCHIVE}/dbadmin.hash", 0o600)
    new = secrets.token_urlsafe(24)
    print(f"old length {len(old)}, new length {len(new)}")

    tpl = {"title": PASS_TITLE, "username": "dbadmin", "email": None, "password": new, "totp_uri": None,
           "urls": [], "extra_fields": []}
    r = sh([f"{H}/bin/pass", "item", "create", "login", "--vault-name", "NUC services", "--from-template", "-"],
           inp=json.dumps(tpl))
    if r.returncode != 0:
        sys.exit(f"ABORT: saving to Proton Pass failed (rc {r.returncode}); nothing changed")
    print(f"saved to Pass: NUC services / {PASS_TITLE}")

    shutil.copy2(DBENV, f"{ARCHIVE}/db.env")
    shutil.copy2(COMPOSE, f"{ARCHIVE}/docker-compose.yml")
    print(f"backups in {ARCHIVE}")

    if not alter(new):
        sys.exit("ABORT: ALTER USER failed; files untouched, Pass item is unused")

    env2 = env.replace("MYSQL_PASSWORD=" + old, "MYSQL_PASSWORD=" + new, 1)
    env2 = env2.rstrip("\n") + ("\n# DB_PASS: same value, read by garden-orchestrator via env_file "
                                f"(T0024, 2026-11-19). Rotate both lines together.\nDB_PASS={new}\n")
    comp2 = LIT.sub("", comp, count=1)
    anchor = "      - /home/user/garden-data/plant_orchestrator/.env\n"
    at = comp2.find(anchor, m_lit.start())
    if at < 0 or at - m_lit.start() > 1200:
        restore_hash(old_hash)
        sys.exit("ABORT: orchestrator env_file line not found; password set back")
    comp2 = comp2[:at] + ("      # DB_PASS (dbadmin) comes from db.env since T0024 (2026-11-19); the literal\n"
                          "      # that was here was printed in a chat on 09-24 and is rotated.\n"
                          "      - ./db.env\n") + comp2[at:]
    with open(COMPOSE + ".new", "w") as f:
        f.write(comp2)
    # OUTCOME 2026-11-19: `docker compose config -q` on the UNCHANGED file already fails ("env file
    # .../plant_orchestrator/.env not found": that folder is gone). So the check is "the edit adds no
    # new error": the .new file must pass, or fail with exactly the same message as the current file.
    base = sh(["docker", "compose", "-f", COMPOSE, "config", "-q"], cwd=NH)
    chk = sh(["docker", "compose", "-f", COMPOSE + ".new", "config", "-q"], cwd=NH)
    if chk.returncode != 0 and (base.returncode == 0 or
                                chk.stderr.replace(COMPOSE + ".new", COMPOSE) != base.stderr):
        os.remove(COMPOSE + ".new")
        restore_hash(old_hash)
        sys.exit("ABORT: compose config check failed; password set back, files untouched")
    with open(DBENV, "w") as f:
        f.write(env2)
    os.chmod(DBENV, 0o600)
    shutil.copymode(f"{ARCHIVE}/docker-compose.yml", COMPOSE + ".new")
    os.replace(COMPOSE + ".new", COMPOSE)

    n, o = can_login(new), can_login(old)
    lit_left = len(re.findall(r"DB_PASS=[^$\s]", Path(COMPOSE).read_text()))
    print(f"login new -> {'ok' if n else 'FAIL'}, old -> {'ok' if o else 'refused'}, compose literals left {lit_left}")
    if n and not o and lit_left == 0:
        print("ROTATED")
        return
    print("TEST FAILED: restoring")
    restore_hash(old_hash)
    shutil.copy2(f"{ARCHIVE}/db.env", DBENV)
    shutil.copy2(f"{ARCHIVE}/docker-compose.yml", COMPOSE)
    print("restored files and the old dbadmin hash")
    sys.exit(1)


if __name__ == "__main__":
    main()
