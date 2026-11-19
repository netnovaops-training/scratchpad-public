#!/usr/bin/env python3
"""Rotate the Caddy -> backlog shared key (X-Backlog-Key). T0023 step 1, 2026-11-18 autopilot.

The old value was printed in a chat log during the 11-16 audit, so it is burned.
Since caddy_keys_to_env.py, Caddy reads it as {$BACKLOG_KEY} from web/caddy.nuc.env,
so a rotation is: new value into the three copies, recreate `backlog` and `caddy-nuc`.

Copies of the key (all mode 600, never printed):
  ~/backlog-page/.env               BACKLOG_PROXY_KEY=  (compose interpolates it into the container)
  ~/nuc-homelab/web/caddy.nuc.env   BACKLOG_KEY=        (caddy-nuc env_file)
  ~/learning-src/.backlog-key       bare value          (reference copy)

Test: direct to 127.0.0.1:8770 with X-Auth-Email owner: new key -> 200, old key -> 403.
On a failed test every file is restored from the backup and both containers recreated.

Usage: rotate_backlog_key.py            (does it; prints only status, lengths, codes)
"""
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


def _http_only(target):
    """Return target (a URL string or urllib Request) if its scheme is http or https, else raise ValueError.

    urlopen also follows file:// and custom schemes (Bandit B310). Every caller here talks to an
    http(s) service, so anything else is a mistake or a planted URL. Added 2026-11-19.
    """
    url = str(getattr(target, "full_url", target))
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url[:80]}")
    return target


H = os.path.expanduser("~")
FILES = {  # path -> line prefix ("" = whole file is the value)
    f"{H}/backlog-page/.env": "BACKLOG_PROXY_KEY=",
    f"{H}/nuc-homelab/web/caddy.nuc.env": "BACKLOG_KEY=",
    f"{H}/learning-src/.backlog-key": "",
}
ARCHIVE = f"{H}/archive/{time.strftime('%Y-%m-%d')}/backlog-key-rotate-{time.strftime('%H%M%S')}"


def read_key(path, prefix):
    t = Path(path).read_text()
    if not prefix:
        return t.strip()
    m = re.search(rf"^{re.escape(prefix)}(\S+)", t, re.MULTILINE)
    return m.group(1) if m else None


def write_key(path, prefix, value):
    t = Path(path).read_text()
    if prefix:
        t, n = re.subn(rf"^{re.escape(prefix)}\S+", prefix + value, t, flags=re.MULTILINE)
        assert n == 1, f"{path}: {n} key lines"
    else:
        t = value
    with open(path, "w") as f:
        f.write(t)
    os.chmod(path, 0o600)


def recreate():
    a = subprocess.run(["docker", "compose", "up", "-d", "backlog"], cwd=f"{H}/backlog-page",
                       capture_output=True, text=True, check=False).returncode
    b = subprocess.run(["docker", "compose", "up", "-d", "caddy-nuc"], cwd=f"{H}/nuc-homelab",
                       capture_output=True, text=True, check=False).returncode
    print(f"recreate backlog: {'ok' if a == 0 else 'FAILED'}, caddy-nuc: {'ok' if b == 0 else 'FAILED'}")
    time.sleep(6)


def probe(key):
    req = urllib.request.Request("http://127.0.0.1:8770/",
                                 headers={"X-Auth-Email": "user@example.com", "X-Backlog-Key": key})
    try:
        return urllib.request.urlopen(_http_only(req), timeout=10).status  # nosec B310
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:  # noqa: BLE001 - report the kind, not the message
        return type(e).__name__


def main():
    olds = {p: read_key(p, pre) for p, pre in FILES.items()}
    if len(set(olds.values())) != 1 or None in olds.values():
        sys.exit("ABORT: the three copies do not hold one same key")
    old = next(iter(olds.values()))
    os.makedirs(ARCHIVE, mode=0o700, exist_ok=True)
    for p in FILES:
        shutil.copy2(p, f"{ARCHIVE}/{os.path.basename(os.path.dirname(p))}__{os.path.basename(p)}")
    new = secrets.token_urlsafe(32)
    print(f"old length {len(old)}, new length {len(new)}, backups in {ARCHIVE}")
    for p, pre in FILES.items():
        write_key(p, pre, new)
    recreate()
    n, o = probe(new), probe(old)
    print(f"new key -> {n}, old key -> {o}")
    if n == 200 and o == 403:
        print("ROTATED")
        return
    print("TEST FAILED: restoring old key")
    for p in FILES:
        shutil.copy2(f"{ARCHIVE}/{os.path.basename(os.path.dirname(p))}__{os.path.basename(p)}", p)
    recreate()
    print(f"after restore: old key -> {probe(old)}")
    sys.exit(1)


if __name__ == "__main__":
    main()
