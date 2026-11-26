#!/usr/bin/env python3
"""abyss_ro_probe.py - READ-ONLY look at the DSM APIs needed for the abyss-ro user (2026-11-24). Prints API
versions, whether user abyss-ro exists, the homes share's permission summary and the ACL of homes, homes/jay and
homes/jay/Photos (account names + rights only). Never prints a password or the sid."""
import json
import sys

from dsm_api import Dsm

d = Dsm()
ok, code = d.login()
if not ok:
    sys.exit("login failed %s" % code)
try:
    info = d._post("/webapi/query.cgi", {"api": "SYNO.API.Info", "version": "1", "method": "query",
                                         "query": "SYNO.Core.User,SYNO.Core.Share,SYNO.Core.Share.Permission,"
                                                  "SYNO.Core.ACL,SYNO.Core.AppPriv,SYNO.Core.AppPriv.App,"
                                                  "SYNO.Core.AppPriv.Rule,SYNO.Core.User.Home,SYNO.Core.Group.Member,"
                                                  "SYNO.FileStation.List"})
    for k, v in sorted((info.get("data") or {}).items()):
        print(k, v.get("minVersion"), v.get("maxVersion"), v.get("path"))
    u = d.call("SYNO.Core.User", "get", 1, name="abyss-ro")
    print("abyss-ro exists:", bool(u.get("success")))
    shares = d.call("SYNO.Core.Share", "list", 1, additional=["hidden"])
    print("shares:", [s.get("name") for s in (shares.get("data") or {}).get("shares", [])])
    home = d.call("SYNO.Core.User.Home", "get", 1)
    print("user home service:", json.dumps(home.get("data"))[:300])
    for p in ("/homes", "/homes/jay", "/homes/jay/Photos"):
        a = d.call("SYNO.Core.ACL", "get", 1, file_path=p, type="all")
        dd = a.get("data") or {}
        rules = [(r.get("owner_name"), r.get("permission_type"), r.get("inherit"),
                  {k: v for k, v in (r.get("permission") or {}).items() if v}) for r in dd.get("rules", [])]
        print(p, "ok" if a.get("success") else a.get("error"), "acl_inherited:", dd.get("inherited"))
        for r in rules:
            print("   ", r)
finally:
    d.logout()
