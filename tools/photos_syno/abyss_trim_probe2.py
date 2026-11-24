#!/usr/bin/env python3
"""abyss_trim_probe2.py - READ-ONLY: data formats of user homelab (groups, app rules, share perms) and script task 4."""
import json, sys
from dsm_api import Dsm
d = Dsm(); ok, code = d.login()
if not ok: sys.exit("login failed %s" % code)
def show(tag, r, n=700):
    s = json.dumps(r.get("data") if r.get("success") else r)
    print(tag, s[:n])
try:
    u = d.call("SYNO.Core.User", "get", 1, name="homelab", additional=["description","expired","cannot_chg_passwd","passwd_never_expire","groups"])
    show("homelab", u)
    show("rule", d.call("SYNO.Core.AppPriv.Rule", "list", 1, entity_type="user", entity_name="homelab", offset=0, limit=50) , 1500)
    show("rule-users", d.call("SYNO.Core.AppPriv.Rule", "list", 1, entity_type="group", entity_name="users", offset=0, limit=50), 1500)
    show("perm", d.call("SYNO.Core.Share.Permission", "list_by_user", 1, name="homelab", user_group_type="local_user", share_type="all", offset=0, limit=50, additional=["hidden","encryption","is_aclmode"]), 1200)
    show("perm-homes", d.call("SYNO.Core.Share.Permission", "list", 1, name="homes", is_unite_permission=False, user_group_type="local_user", offset=0, limit=50), 800)
    show("task4", d.call("SYNO.Core.TaskScheduler", "get", 4, id=4), 1500)
    show("share-get", d.call("SYNO.Core.Share", "get", 1, name="Downloads", additional=["encryption","enable_recycle_bin","hidden","recyclebin","share_quota","unite_permission"]), 900)
finally:
    d.logout()
