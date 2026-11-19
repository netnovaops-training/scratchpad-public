#!/usr/bin/env python3
"""Rotate the Caddy -> notes_api house key (X-Notes-Key), env-file way. T0023, 2026-11-18 autopilot.

Replaces rotate_notes_key.sh for the post-T0259 layout: Caddy no longer has the literal
(caddy_keys_to_env.py moved it to {$NOTES_KEY} in web/caddy.nuc.env), so the old script's
string replace would find nothing. The old script is kept, unchanged, for history.

Copies of the key (mode 600, never printed):
  ~/learning-src/.notes-key        read by notes_api.py at start (the only live reader)
  ~/nuc-homelab/web/caddy.nuc.env  NOTES_KEY=  (caddy-nuc env_file)
new_page.py and caddy_admin_view.py write the {$NOTES_KEY} placeholder since this item.

Test: GET 127.0.0.1:8781/api/notes. The wrong key gets 403 "not through the front door";
the right key gets past that check (any other answer). New must pass, old must fail.
On failure both files are restored and notes_api + caddy-nuc restarted.
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
KEYF = f"{H}/learning-src/.notes-key"
ENV = f"{H}/nuc-homelab/web/caddy.nuc.env"
ARCHIVE = f"{H}/archive/{time.strftime('%Y-%m-%d')}/notes-key-rotate-{time.strftime('%H%M%S')}"


def env_key():
    m = re.search(r"^NOTES_KEY=(\S+)", Path(ENV).read_text(), re.MULTILINE)
    return m.group(1) if m else None


def put(new):
    with open(KEYF, "w") as f:
        f.write(new)
    os.chmod(KEYF, 0o600)
    t, n = re.subn(r"^NOTES_KEY=\S+", "NOTES_KEY=" + new, Path(ENV).read_text(), flags=re.MULTILINE)
    assert n == 1
    with open(ENV, "w") as f:
        f.write(t)
    os.chmod(ENV, 0o600)


def restart():
    subprocess.run(["pkill", "-f", "[n]otes_api.py"], check=False)
    time.sleep(1)
    subprocess.Popen(["setsid", "nohup", "bash", f"{H}/learning-src/notes_api_ensure.sh"],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=f"{H}/learning-src")
    r = subprocess.run(["docker", "compose", "up", "-d", "caddy-nuc"], cwd=f"{H}/nuc-homelab",
                       capture_output=True, text=True, check=False).returncode
    print("caddy-nuc recreate:", "ok" if r == 0 else "FAILED")
    time.sleep(5)


def passes(key):
    """True when notes_api accepts the key (gets past the front-door check)."""
    req = urllib.request.Request("http://127.0.0.1:8781/api/notes",
                                 headers={"X-Notes-Key": key, "X-Notes-Person": "demo",
                                          "X-Auth-Email": "user@example.com"})
    try:
        body = urllib.request.urlopen(_http_only(req), timeout=10).read().decode(errors="replace")  # nosec B310
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
    except Exception as e:  # noqa: BLE001
        return f"error {type(e).__name__}"
    return "front door" not in body


def main():
    old = Path(KEYF).read_text().strip()
    if env_key() != old:
        sys.exit("ABORT: .notes-key and NOTES_KEY differ")
    if passes(old) is not True:
        sys.exit("ABORT: the current key does not pass before rotating; nothing changed")
    os.makedirs(ARCHIVE, mode=0o700, exist_ok=True)
    shutil.copy2(KEYF, f"{ARCHIVE}/.notes-key")
    shutil.copy2(ENV, f"{ARCHIVE}/caddy.nuc.env")
    new = secrets.token_hex(32)
    print(f"old length {len(old)}, new length {len(new)}, backups in {ARCHIVE}")
    put(new)
    restart()
    n, o = passes(new), passes(old)
    print(f"new key passes: {n}, old key passes: {o}")
    if n is True and o is False:
        print("ROTATED")
        return
    print("TEST FAILED: restoring old key")
    shutil.copy2(f"{ARCHIVE}/.notes-key", KEYF)
    shutil.copy2(f"{ARCHIVE}/caddy.nuc.env", ENV)
    restart()
    print("after restore, old key passes:", passes(old))
    sys.exit(1)


if __name__ == "__main__":
    main()
