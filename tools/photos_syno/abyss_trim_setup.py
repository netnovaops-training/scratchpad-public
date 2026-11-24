#!/usr/bin/env python3
"""abyss_trim_setup.py - create the NAS side of the Abyss trimmed-clips drop (Jason approved 11-24).

Steps (each idempotent; prints names, codes and counts only, never a password, sid or token):
  share  - shared folder AbyssTrimmed on /volume1 (no recycle bin, no encryption)
  user   - DSM user abyss-trim, random 32-char password saved FIRST to Pass "NUC services" /
           "NAS abyss-trim SMB (write-only drop)" (stdin template), read back and compared by SHA-256 fingerprint
  perms  - abyss-trim: Read/Write on AbyssTrimmed, No access (deny) on every other share
  apps   - abyss-trim: deny every application privilege except SMB
  task   - root user-defined script task "Abyss trimmed clips to Photos", every 5 min:
           bash /volume1/homes/jay/photos_syno/abyss-trim-move.sh
  check  - print the resulting state (groups, share perms, app rules, task)
Use:  python abyss_trim_setup.py share user perms apps task check
OUTCOME 2026-11-24: share + user steps FAILED through the Web API (share create 3300, user create 105, even
with a webui session + SynoToken + password-confirm token); both were done as root over SSH instead
(`synoshare --add`, `synouser --add`, password on stdin). perms, apps, task, check worked here. See D:\\scratchpad\\nuc\\abyss-trim\\README-TRIM.md "NAS drop (live)".
"""
import hashlib
import json
import os
import secrets
import string
import subprocess
import sys

from dsm_web import DsmWeb as Dsm

SHARE = "AbyssTrimmed"
USER = "abyss-trim"
VAULT = "NUC services"
TITLE = "NAS abyss-trim SMB (write-only drop)"
TASK = "Abyss trimmed clips to Photos"
SCRIPT = "bash /volume1/homes/jay/photos_syno/abyss-trim-move.sh"
SMB_APP = "SYNO.SDS.SMBService.AppPrivilege.Instance"
ENV = dict(os.environ, PROTON_PASS_KEY_PROVIDER="fs")


def ok(tag, r):
    """Print success or the error code only; return success."""
    print(tag, "ok" if r.get("success") else "FAILED %s" % json.dumps(r.get("error")))
    return bool(r.get("success"))


def fp(v):
    return hashlib.sha256(v.encode()).hexdigest()[:12]


def pass_run(args, inp=None):
    return subprocess.run(["pass-cli", *args], input=inp, capture_output=True, text=True, env=ENV,
                          encoding="utf-8")


def confirm(d):
    """SynoConfirmPWToken for sensitive DSM writes (DSM 7.2 answers 105 without it); token never printed."""
    tok = d.call("SYNO.Core.User.PasswordConfirm", "auth", 2, password=d._pw)
    token = (tok.get("data") or {}).get("SynoConfirmPWToken")
    print("password confirm", "ok" if token else "FAILED %s" % json.dumps(tok.get("error")))
    return {"SynoConfirmPWToken": token} if token else {}


def step_share(d):
    names = [s["name"] for s in d.call("SYNO.Core.Share", "list", 1)["data"]["shares"]]
    if SHARE in names:
        print("share exists")
        return
    info = {"name": SHARE, "vol_path": "/volume1", "desc": "Abyss trimmed clips drop (write-only, abyss-trim)",
            "hidden": False, "enable_recycle_bin": False, "recycle_bin_admin_only": False, "encryption": False,
            "enc_passwd": "", "hide_unreadable": False, "enable_share_cow": True, "enable_share_compress": False,
            "share_quota": 0, "name_org": ""}
    ok("share create", d.call("SYNO.Core.Share", "create", 1, name=SHARE, shareinfo=info, **confirm(d)))


def step_user(d):
    if d.call("SYNO.Core.User", "get", 1, name=USER).get("success"):
        print("user exists (password not changed)")
        return
    old = pass_run(["item", "view", "--vault-name", VAULT, "--item-title", TITLE, "--field", "password"])
    if old.returncode == 0 and len(old.stdout.strip()) == 32:  # saved by an earlier run whose user create failed
        pw = old.stdout.strip()
        print("reusing the Pass item from the earlier run: len %d, fingerprint %s" % (len(pw), fp(pw)))
        ok("user create", d.call("SYNO.Core.User", "create", 1, **user_fields(pw), **confirm(d)))
        return
    alpha = string.ascii_letters + string.digits
    pw = "".join(secrets.choice(alpha) for _ in range(32))
    tpl = {"title": TITLE, "username": USER, "email": None, "password": pw, "totp_uri": None,
           "urls": ["smb://192.168.1.68/AbyssTrimmed"]}
    r = pass_run(["item", "create", "login", "--vault-name", VAULT, "--from-template", "-"], json.dumps(tpl))
    if r.returncode != 0:
        sys.exit("ABORT: Pass save failed (rc %d); nothing created" % r.returncode)
    back = pass_run(["item", "view", "--vault-name", VAULT, "--item-title", TITLE, "--field", "password"])
    got = back.stdout.strip()
    if back.returncode != 0 or fp(got) != fp(pw):
        sys.exit("ABORT: Pass read-back mismatch (rc %d, len %d); user NOT created" % (back.returncode, len(got)))
    print("pass saved + read back: len %d, fingerprint match %s" % (len(pw), fp(pw)))
    ok("user create", d.call("SYNO.Core.User", "create", 1, **user_fields(pw), **confirm(d)))


def user_fields(pw):
    return dict(name=USER, password=pw, description="Abyss trimmer: SMB write-only drop into AbyssTrimmed (2026-11-24)",
                email="", expired="normal", cannot_chg_passwd=True, passwd_never_expire=True,
                notify_by_email=False, send_password=False)


def step_perms(d):
    shares = [s["name"] for s in d.call("SYNO.Core.Share", "list", 1)["data"]["shares"]]
    for s in shares:
        rw = s == SHARE
        perm = {"name": USER, "is_readonly": False, "is_writable": rw, "is_deny": not rw, "is_custom": False}
        ok("perm %s %s" % (s, "rw" if rw else "deny"),
           d.call("SYNO.Core.Share.Permission", "set", 1, name=s, user_group_type="local_user",
                  permissions=[perm]))


def step_apps(d):
    apps = d.call("SYNO.Core.AppPriv.App", "list", 2, offset=0, limit=-1)["data"]["applications"]
    rules = [{"entity_type": "user", "entity_name": USER, "app_id": a["app_id"], "allow_ip": [],
              "deny_ip": ["0.0.0.0"]} for a in apps if a["app_id"] != SMB_APP]
    ok("app deny x%d" % len(rules), d.call("SYNO.Core.AppPriv.Rule", "set", 1, rules=rules))


def step_task(d):
    tasks = d.call("SYNO.Core.TaskScheduler", "list", 3, offset=0, limit=100)["data"]["tasks"]
    if any(t["name"] == TASK for t in tasks):
        print("task exists")
        return
    sched = {"date_type": 0, "week_day": "0,1,2,3,4,5,6", "hour": 0, "minute": 0, "repeat_hour": 0,
             "repeat_min": 5, "last_work_hour": 23, "repeat_date": 1001, "monthly_week": []}
    extra = {"notify_enable": False, "notify_mail": "", "notify_if_error": False, "script": SCRIPT}
    kw = dict(name=TASK, real_owner="root", owner="root", enable=True, schedule=sched, extra=extra, type="script")
    kw.update(confirm(d))
    ok("task create", d.call("SYNO.Core.TaskScheduler.Root", "create", 4, **kw))


def step_check(d):
    grp = d.call("SYNO.Core.Group.Member", "list", 1, group="administrators")
    print("in administrators:", USER in [u["name"] for u in grp["data"]["users"]])
    for g in ("users",):
        m = d.call("SYNO.Core.Group.Member", "list", 1, group=g)
        print("in %s:" % g, USER in [u["name"] for u in (m.get("data") or {}).get("users", [])])
    p = d.call("SYNO.Core.Share.Permission", "list_by_user", 1, name=USER, user_group_type="local_user",
               share_type="all", offset=0, limit=100)
    for s in p["data"]["shares"]:
        print(" share %-24s %s" % (s["name"], "deny" if s["is_deny"] else "rw" if s["is_writable"] else
                                   "ro" if s["is_readonly"] else "none"))
    r = d.call("SYNO.Core.AppPriv.Rule", "get", 1, entity_type="user", entity_name=USER)
    rules = (r.get("data") or {}).get("rules", [])
    print("app rules: deny %d, allow %d" % (sum(1 for x in rules if x["deny_ip"]),
                                            sum(1 for x in rules if x["allow_ip"])))
    for t in d.call("SYNO.Core.TaskScheduler", "list", 3, offset=0, limit=100)["data"]["tasks"]:
        if t["name"] == TASK:
            print("task", t["id"], t["owner"], t["type"], "enabled" if t["enable"] else "DISABLED")


if __name__ == "__main__":
    d = Dsm()
    good, code = d.login()
    if not good:
        sys.exit("login failed %s" % code)
    try:
        for a in sys.argv[1:]:
            globals()["step_" + a](d)
    finally:
        d.logout()
