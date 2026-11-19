#!/usr/bin/env python3
"""Move the three Caddy shared keys out of Caddyfile.nuc into web/caddy.nuc.env.

T0023 step 1 / T0259 (2026-11-18, autopilot). The X-Notes-Key, X-Backlog-Key and
X-Secrets-Key values were literal in Caddyfile.nuc (tracked in git). This replaces each
literal with {$NOTES_KEY} / {$BACKLOG_KEY} / {$SECRETS_KEY}, adds those names to the
env file caddy-nuc already loads (env_file: ./web/caddy.nuc.env), and recreates the
container so the new env is in it. The values themselves are NOT changed here; rotation
is a later step (new value into the env file + its consumer, `up -d caddy-nuc`).

Never prints a key: only names, counts, lengths and yes/no matches.

Usage:  caddy_keys_to_env.py check   # dry run: builds .new files, validates, compares
        caddy_keys_to_env.py apply   # check + backup + swap + recreate caddy-nuc

OUTCOME lines are added below after each real run.
"""
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = os.path.expanduser("~")
WEB = f"{HOME}/nuc-homelab/web"
CADDY = f"{WEB}/Caddyfile.nuc"
ENV = f"{WEB}/caddy.nuc.env"
ARCHIVE = f"{HOME}/archive/{time.strftime('%Y-%m-%d')}/nuc-homelab/web"
NAMES = {"Notes": "NOTES_KEY", "Backlog": "BACKLOG_KEY", "Secrets": "SECRETS_KEY"}
# Consumer copies used only to confirm the Caddyfile value is the one the service checks.
CONSUMER = {"Notes": f"{HOME}/learning-src/.notes-key",
            "Backlog": f"{HOME}/backlog-page/.env"}
PAT = re.compile(r"(header_up X-(Notes|Backlog|Secrets)-Key )(\S+)")


def consumer_value(kind):
    """Return the consumer's copy of a key, or None. Never printed."""
    p = CONSUMER.get(kind)
    if not p or not os.path.exists(p):
        return None
    t = Path(p).read_text()
    if p.endswith(".env"):
        m = re.search(r"^BACKLOG_PROXY_KEY=(\S+)", t, re.MULTILINE)
        return m.group(1).strip('"') if m else None
    return t.strip()


def build():
    """Return (new_caddyfile_text, new_env_text, values) or exit on any doubt."""
    text = Path(CADDY).read_text()
    values = {}
    for _, kind, val in PAT.findall(text):
        values.setdefault(kind, set()).add(val)
    for kind, vals in values.items():
        print(f"{kind}: {sum(1 for m in PAT.finditer(text) if m.group(2) == kind)} lines, "
              f"{len(vals)} distinct value(s), length {[len(v) for v in vals]}")
        if len(vals) != 1:
            sys.exit(f"ABORT: {kind} has {len(vals)} distinct values")
        if next(iter(vals)).startswith("{$"):
            sys.exit(f"ABORT: {kind} already uses an env placeholder")
    values = {k: next(iter(v)) for k, v in values.items()}
    for kind, val in values.items():
        c = consumer_value(kind)
        print(f"{kind}: consumer copy {'matches' if c == val else 'MISSING' if c is None else 'DIFFERS'}")
    new_text = PAT.sub(lambda m: m.group(1) + "{$" + NAMES[m.group(2)] + "}", text)
    env = Path(ENV).read_text()
    for kind, val in values.items():
        if re.search(rf"^{NAMES[kind]}=", env, re.MULTILINE):
            sys.exit(f"ABORT: {NAMES[kind]} already in env file")
    if not env.endswith("\n"):
        env += "\n"
    env += "".join(f"{NAMES[k]}={v}\n" for k, v in values.items())
    return new_text, env, values


def validate(new_caddy, new_env, values):
    """Adapt the new Caddyfile in a throwaway caddy container with the new env.

    Passes only if adapt succeeds AND every old value appears in the adapted JSON
    (proves the {$NAME} substitution happened; an unset name would adapt to "").
    """
    r = subprocess.run(["docker", "run", "--rm", "--network", "none", "--env-file", new_env,
                        "-v", f"{new_caddy}:/etc/caddy/Caddyfile:ro", "caddy:latest",
                        "caddy", "adapt", "--config", "/etc/caddy/Caddyfile",
                        "--adapter", "caddyfile"], capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print("validate FAILED (stderr hidden: it may quote config lines)")
        return False
    ok = all(v in r.stdout for v in values.values())
    print(f"adapt ok, all values substituted: {ok}")
    return ok


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    new_text, env_text, values = build()
    nc, ne = CADDY + ".new", ENV + ".new"
    Path(nc).write_text(new_text)
    with open(os.open(ne, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(env_text)
    os.chmod(nc, 0o644)
    if not validate(nc, ne, values):
        sys.exit(1)
    left = len(re.findall(r"Key [A-Za-z0-9_-]{20,}", new_text))
    print(f"literal keys left in new Caddyfile: {left}")
    if mode != "apply":
        os.remove(ne)  # holds the values; do not leave a stray copy
        print("check only; nothing swapped")
        return
    os.makedirs(ARCHIVE, exist_ok=True)
    shutil.copy2(CADDY, f"{ARCHIVE}/Caddyfile.nuc.before-T0023")
    shutil.copy2(ENV, f"{ARCHIVE}/caddy.nuc.env.before-T0023")
    os.chmod(f"{ARCHIVE}/Caddyfile.nuc.before-T0023", 0o600)
    # env first (container not yet recreated, so live Caddy still uses its loaded config)
    with open(ENV, "w") as f:
        f.write(env_text)
    os.chmod(ENV, 0o600)
    # Caddyfile is a single-file bind mount: write in place to keep the inode.
    with open(CADDY, "w") as f:
        f.write(new_text)
    r = subprocess.run(["docker", "compose", "up", "-d", "caddy-nuc"],
                       cwd=f"{HOME}/nuc-homelab", capture_output=True, text=True, check=False)
    print("compose up:", "ok" if r.returncode == 0 else "FAILED")
    os.remove(ne)
    print(f"backups in {ARCHIVE}")


if __name__ == "__main__":
    main()
