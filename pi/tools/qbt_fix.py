#!/usr/bin/env python3
"""qbt_fix.py - give the Pi's qBittorrent a permanent WebUI password kept in Proton Pass (T0872, 2026-11-18).

Why: qBittorrent had no saved WebUI password, so linuxserver's image made a new temporary one at every
restart (printed only in `docker logs qbittorrent`); nobody could log in from the Drop page link.
What it does, never printing a secret:
  1. reads the newest temporary password from the container log,
  2. logs in to the WebUI API (http://192.168.1.69:8080, user admin),
  3. sets a new random 24-char password and lets the NUC (192.168.1.86/32) skip the login, so the
     SSO-protected torrent. subdomain on the NUC Caddy needs only the household sign-in,
  4. stores the password in Proton Pass ("Local accounts") and checks it logs in.
Order matters: the Pass item is written BEFORE the password changes, so a failure can't lock us out.
OUTCOME: see the Review line on T0872 for the run result.
"""
import json
import re
import secrets
import string
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

BASE = "http://192.168.1.69:8080"
TITLE = "Pi qBittorrent WebUI 192.168.1.69:8080"
PASS = [str(__import__("pathlib").Path.home() / "bin" / "pass")]


def opener():
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))


def post(op, path, data):
    req = urllib.request.Request(BASE + path, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Referer": BASE, "Origin": BASE})
    with op.open(req, timeout=15) as r:
        return r.status, r.read().decode(errors="replace")


def login(pw):
    # qBittorrent 5.x answers 204 with an empty body on success, 401 on a wrong password (urllib raises);
    # 4.x answered 200 "Ok.". Checked 2026-11-18 against v5.2.3.
    op = opener()
    try:
        status, body = post(op, "/api/v2/auth/login", {"username": "admin", "password": pw})
    except urllib.error.HTTPError:
        return None
    return op if status == 204 or body.strip() == "Ok." else None


def main():
    log = subprocess.run(["docker", "logs", "qbittorrent"], capture_output=True, text=True, check=False)
    temps = re.findall(r"temporary password is provided for this session:\s*(\S+)", log.stdout + log.stderr)
    if not temps:
        sys.exit("no temporary password in the log; stop")
    op = login(temps[-1])
    if not op:
        sys.exit("temporary password did not log in; stop")
    print("logged in with the temporary password")

    alphabet = string.ascii_letters + string.digits
    new = "".join(secrets.choice(alphabet) for _ in range(24))
    item = {"title": TITLE, "username": "admin", "email": None, "password": new, "totp_uri": None,
            "urls": [BASE, "https://torrent.home.example.net"], "extra_fields": []}
    r = subprocess.run(PASS + ["item", "create", "login", "--vault-name", "Local accounts", "--from-template", "-"],
                       input=json.dumps(item), capture_output=True, text=True, check=False)
    if r.returncode != 0:
        sys.exit("Pass item create failed; password NOT changed")
    print("Pass item written")

    prefs = {"web_ui_password": new,
             "bypass_auth_subnet_whitelist_enabled": True,
             "bypass_auth_subnet_whitelist": "192.168.1.86/32"}
    status, _ = post(op, "/api/v2/app/setPreferences", {"json": json.dumps(prefs)})
    print("setPreferences HTTP", status)

    check = login(new)
    print("new password logs in:", bool(check))
    got = subprocess.run(PASS + ["item", "view", "--vault-name", "Local accounts", "--item-title", TITLE,
                                 "--field", "password"], capture_output=True, text=True, check=False).stdout.strip()
    print("Pass copy matches:", got == new)


if __name__ == "__main__":
    main()
