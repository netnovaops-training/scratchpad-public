#!/usr/bin/env python3
"""abyss_trim_probe3.py - READ-ONLY: find the AppPriv rule read format; user get format (names only)."""
import json, sys
from dsm_api import Dsm
d = Dsm(); ok, code = d.login()
if not ok: sys.exit("login failed %s" % code)
try:
    for api, m, v, kw in [
        ("SYNO.Core.AppPriv.Rule", "list", 1, {}),
        ("SYNO.Core.AppPriv.Rule", "get", 1, {"entity_type": "user", "entity_name": "homelab"}),
        ("SYNO.Core.AppPriv.Rule", "list", 1, {"offset": 0, "limit": 20, "app_id": "SYNO.Desktop"}),
        ("SYNO.Core.AppPriv", "list", 2, {"offset": 0, "limit": 20, "type": "local_user", "name": "homelab"}),
        ("SYNO.Core.AppPriv", "get", 2, {"type": "local_user", "name": "homelab"}),
        ("SYNO.Core.AppPriv", "get", 1, {"type": "local_user", "name": "homelab"}),
        ("SYNO.Core.User", "get", 1, {"name": "homelab"}),
        ("SYNO.Core.Group.Member", "list", 1, {"group": "administrators"}),
    ]:
        r = d.call(api, m, v, **kw)
        print(api, m, v, json.dumps(r)[:900])
finally:
    d.logout()
