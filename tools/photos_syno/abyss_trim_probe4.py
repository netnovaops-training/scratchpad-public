#!/usr/bin/env python3
"""abyss_trim_probe4.py - READ-ONLY: which entities hold rules on each DSM app (names only)."""
import sys
from dsm_api import Dsm
d = Dsm(); ok, code = d.login()
if not ok: sys.exit("login failed %s" % code)
try:
    apps = d.call("SYNO.Core.AppPriv.App", "list", 2, offset=0, limit=-1)["data"]["applications"]
    for a in apps:
        r = d.call("SYNO.Core.AppPriv.Rule", "list", 1, offset=0, limit=50, app_id=a["app_id"])
        rules = (r.get("data") or {}).get("rules", [])
        print(a["app_id"], [(x["entity_type"], x["entity_name"], "allow" if x["allow_ip"] else "", "deny" if x["deny_ip"] else "") for x in rules])
finally:
    d.logout()
