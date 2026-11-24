#!/usr/bin/env python3
"""abyss_trim_probe.py - READ-ONLY: DSM API versions + state for the abyss-trim user/share/task (2026-11-24).
Prints names, versions and app ids only; never a password or the sid."""
import json, sys
from dsm_api import Dsm
d = Dsm(); ok, code = d.login()
if not ok: sys.exit("login failed %s" % code)
try:
    q = "SYNO.Core.User,SYNO.Core.Share,SYNO.Core.Share.Permission,SYNO.Core.AppPriv,SYNO.Core.AppPriv.App,SYNO.Core.AppPriv.Rule,SYNO.Core.TaskScheduler,SYNO.Core.TaskScheduler.Root,SYNO.Core.User.PasswordConfirm,SYNO.Core.User.PasswordPolicy,SYNO.Core.User.PasswordExpiry,SYNO.Core.Group.Member,SYNO.API.Encryption,SYNO.Core.EventScheduler,SYNO.Core.EventScheduler.Root"
    info = d._post("/webapi/query.cgi", {"api": "SYNO.API.Info", "version": "1", "method": "query", "query": q})
    for k, v in sorted((info.get("data") or {}).items()):
        print(k, v.get("minVersion"), v.get("maxVersion"), v.get("path"))
    print("abyss-trim exists:", d.call("SYNO.Core.User", "get", 1, name="abyss-trim").get("success"))
    sh = d.call("SYNO.Core.Share", "list", 1)
    print("shares:", [s.get("name") for s in (sh.get("data") or {}).get("shares", [])])
    apps = d.call("SYNO.Core.AppPriv.App", "list", 2, offset=0, limit=-1)
    if not apps.get("success"): apps = d.call("SYNO.Core.AppPriv.App", "list", 1, offset=0, limit=-1)
    print("apps ok", apps.get("success"), (apps.get("error") or {}))
    for a in (apps.get("data") or {}).get("applications", []):
        print(" app", a.get("app_id"), "|", a.get("name"), "|", a.get("grant_type"), a.get("grant_by_default"))
    ts = d.call("SYNO.Core.TaskScheduler", "list", 3, offset=0, limit=50, sort_by="next_trigger_time", sort_direction="ASC")
    print("tasks ok", ts.get("success"), (ts.get("error") or {}))
    for t in (ts.get("data") or {}).get("tasks", []):
        print(" task", t.get("id"), t.get("name"), t.get("owner"), t.get("type"), t.get("enable"))
    pp = d.call("SYNO.Core.User.PasswordPolicy", "get", 1)
    print("pwpolicy", json.dumps(pp.get("data"))[:400])
    pe = d.call("SYNO.Core.User.PasswordExpiry", "get", 1)
    print("pwexpiry", json.dumps(pe.get("data"))[:300], pe.get("error"))
finally:
    d.logout()
