#!/usr/bin/env python3
"""creds.py - read Jason's DSM login from Proton Pass into memory. Never prints a value or an item title.

Which item: the login in vault "Rotated" whose title mentions quickconnect (rotated 2026-11-24; the DSM login).
It is matched here in code; callers see only "found/not found" and field lengths.
Use:  from creds import dsm_login; user, pw, qc = dsm_login()
      python creds.py --check     -> prints found/not found, vault name and value LENGTHS only
OUTCOME 2026-11-24: written for the Synology Photos move (plan functional-greeting-horizon, steps 3-7).
"""
import json
import os
import subprocess
import sys

VAULT = "Rotated"
ENV = dict(os.environ, PROTON_PASS_KEY_PROVIDER="fs")


def _pass(*args):
    """Run pass-cli and return stdout (never echoed)."""
    r = subprocess.run(["pass-cli", *args], capture_output=True, text=True, env=ENV, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"pass-cli {args[0]} {args[1] if len(args) > 1 else ''} failed (rc {r.returncode})")
    return r.stdout


def _find_item():
    """Return the raw JSON dict of the DSM login item, or None. Title never leaves this function."""
    data = json.loads(_pass("item", "list", VAULT, "--output", "json"))
    items = data.get("items", data) if isinstance(data, dict) else data
    for it in items:
        title = (it.get("title") or (it.get("content") or {}).get("title") or "").lower()
        if "quickconnect" in title:
            return it
    return None


def _walk(o, out, key=""):
    """Collect every (key, string) leaf of a JSON tree."""
    if isinstance(o, dict):
        for k, v in o.items():
            _walk(v, out, k)
    elif isinstance(o, list):
        for v in o:
            _walk(v, out, key)
    elif isinstance(o, str):
        out.append((key, o))


def dsm_login():
    """(user, password, quickconnect_host) from Pass; raises if not found. Values stay in memory only."""
    it = _find_item()
    if it is None:
        raise RuntimeError(f"DSM login item not found in vault {VAULT}")
    leaves = []
    _walk(it, leaves)
    user = pw = qc = None
    for k, v in leaves:
        kl = k.lower()
        if kl in ("username", "email", "item_email") and v and not user:
            user = v
        elif kl == "password" and v and not pw:
            pw = v
        elif kl == "title" and "quickconnect" in v.lower():
            qc = v.strip()
    if not pw:  # list output may omit secrets: read the one field
        iid = it.get("id") or it.get("item_id")
        pw = _pass("item", "view", "--vault-name", VAULT, "--item-id", iid, "--field", "password").strip()
        if not user:
            user = _pass("item", "view", "--vault-name", VAULT, "--item-id", iid, "--field", "username").strip()
    return user, pw, qc


if __name__ == "__main__":
    if "--check" in sys.argv:
        try:
            u, p, q = dsm_login()
            print(f"found in vault {VAULT}: user len {len(u or '')}, password len {len(p or '')}, qc len {len(q or '')}")
        except RuntimeError as e:
            print("not found:", e)
