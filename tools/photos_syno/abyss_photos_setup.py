#!/usr/bin/env python3
"""abyss_photos_setup.py - read-only NAS photos for Abyss Find (nas-photos), Jason's "do the small fixes" 2026-11-24.

Why this shape: DSM cannot grant one user read on homes/jay/Photos alone without editing Jason's home ACLs
(group users reads all of homes/jay; README-FIND.md "Photos login"). So, like the abyss-trim drop but reversed:
  - a separate shared folder AbyssPhotos (hidden), and inside it AbyssPhotos/Photos = a READ-ONLY BIND MOUNT of
    /volume1/homes/jay/Photos (kernel-enforced ro: nothing can write through it, not even root);
  - DSM user abyss-photos-ro: Read-only on AbyssPhotos, No access on every other share, every app denied but SMB.
  Samba never leaves a share's path (wide links off), so the user sees Photos and nothing else of homes/jay.
  Jason's own permissions are not touched. No copy: originals and @eaDir thumbnails are read in place.
  The bind is made at boot by a root triggered task and undone at shutdown by another (DSM keeps no fstab).

Steps (each idempotent; prints names, codes and counts only, never a password, sid or token):
  pass   - random 32-char password saved FIRST to Pass "NUC services" / TITLE (stdin template), read back by
           SHA-256 fingerprint
  root   - as root over SSH (stdin script): share AbyssPhotos, user abyss-photos-ro (password from Pass, inside
           the stdin script only), share perms (RO here, NA everywhere else), the bind script, the bind itself
  apps   - deny every application privilege except SMB (DSM Web API)
  tasks  - root triggered tasks "Abyss photos bind (boot)" / "Abyss photos unbind (shutdown)"
  check  - groups, share perms, app rules, tasks, the bind mount state
Use:  python abyss_photos_setup.py pass root apps tasks check
"""
import json
import os
import secrets
import string
import sys

import abyss_trim_setup as base
import nas
from dsm_web import DsmWeb as Dsm

SHARE = "AbyssPhotos"
USER = "abyss-photos-ro"
TITLE = "NAS abyss-photos-ro SMB (Abyss read-only)"
BIND = "/volume1/homes/jay/photos_syno/abyss-photos-bind.sh"
TASKS = {"bootup": ("Abyss photos bind (boot)", "bash %s start" % BIND),
         "shutdown": ("Abyss photos unbind (shutdown)", "bash %s stop" % BIND)}

BIND_SH = r"""#!/bin/bash
# abyss-photos-bind.sh (2026-11-24) - run as root by DSM triggered tasks (boot: start, shutdown: stop).
# Makes /volume1/AbyssPhotos/Photos a READ-ONLY bind mount of /volume1/homes/jay/Photos, so the SMB user
# abyss-photos-ro (Read-only on AbyssPhotos only) can list and read the photo library for Abyss Find and
# nothing else of homes/jay. Source copy: D:\scratchpad\tools\photos_syno\abyss_photos_setup.py (BIND_SH).
SRC=/volume1/homes/jay/Photos
DST=/volume1/AbyssPhotos/Photos
is_mnt() { awk -v d="$DST" '$2 == d {f=1} END {exit !f}' /proc/mounts; }
if [ "${1:-start}" = stop ]; then
    is_mnt && { umount "$DST" 2>/dev/null || umount -l "$DST"; }
    exit 0
fi
for i in $(seq 1 60); do [ -d "$SRC" ] && [ -d "$DST" ] && break; sleep 5; done
is_mnt || mount --bind "$SRC" "$DST" || exit 1
mount -o remount,bind,ro "$DST"
"""


def pw_from_pass():
    r = base.pass_run(["item", "view", "--vault-name", base.VAULT, "--item-title", TITLE, "--field", "password"])
    v = r.stdout.strip()
    return v if r.returncode == 0 and len(v) == 32 else None


def step_pass(d):
    if pw_from_pass():
        print("pass item exists (len 32); not changed")
        return
    pw = "".join(secrets.choice(string.ascii_letters + string.digits) for _ in range(32))
    tpl = {"title": TITLE, "username": USER, "email": None, "password": pw, "totp_uri": None,
           "urls": ["smb://100.64.0.12/AbyssPhotos/Photos"]}
    r = base.pass_run(["item", "create", "login", "--vault-name", base.VAULT, "--from-template", "-"], json.dumps(tpl))
    if r.returncode != 0:
        sys.exit("ABORT: Pass save failed (rc %d)" % r.returncode)
    got = pw_from_pass()
    if not got or base.fp(got) != base.fp(pw):
        sys.exit("ABORT: Pass read-back mismatch")
    print("pass saved + read back: len 32, fingerprint match %s" % base.fp(pw))


def step_root(d):
    pw = pw_from_pass()
    if not pw:
        sys.exit("ABORT: no Pass item; run the pass step first")
    sh = """set -u
export PATH=$PATH:/usr/syno/sbin:/usr/syno/bin:/usr/sbin:/sbin
S=%(share)s; U=%(user)s; P='%(pw)s'
if synoshare --get "$S" >/dev/null 2>&1; then echo "share exists"; else
  synoshare --add "$S" "Abyss Find read-only view of homes/jay/Photos" /volume1/$S "" "" "" 0 0 \
    >/dev/null 2>&1 && echo "share created" || echo "share create FAILED"; fi  # 11-24: a desc with ":" or "," failed
if id "$U" >/dev/null 2>&1; then echo "user exists (password not changed)"; else
  synouser --add "$U" "$P" "Abyss Find read-only photos (SMB, 2026-11-24)" 0 "" 0 >/dev/null 2>&1 \
    && echo "user created" || echo "user create FAILED"; fi
unset P
synoshare --setuser "$S" RO = "$U" >/dev/null 2>&1 && echo "perm $S RO" || echo "perm $S FAILED"
n=0; f=0
synoshare --enum ALL 2>/dev/null | tail -n +3 > /tmp/abyss-shares.txt   # names may hold spaces: one per line
while IFS= read -r s; do
  [ -z "$s" ] || [ "$s" = "$S" ] && continue
  if synoshare --setuser "$s" NA + "$U" >/dev/null 2>&1; then n=$((n+1)); else f=$((f+1)); fi
done < /tmp/abyss-shares.txt
rm -f /tmp/abyss-shares.txt
echo "perm NA on $n other shares, $f failed"
mkdir -p /volume1/$S/Photos
cat > %(bind)s <<'BINDEOF'
%(bind_sh)sBINDEOF
chown root:root %(bind)s; chmod 755 %(bind)s
bash %(bind)s start && echo "bind start rc 0" || echo "bind start FAILED"
awk -v d=/volume1/$S/Photos '$2 == d {print "mounted", $4}' /proc/mounts | cut -c1-12
""" % {"share": SHARE, "user": USER, "pw": pw, "bind": BIND, "bind_sh": BIND_SH}
    rc, out, err = nas.script(sh, as_root=True, timeout=300)
    print(out.strip())
    if err.strip():
        print("stderr lines:", len(err.strip().splitlines()))


def step_apps(d):
    base.USER = USER
    base.step_apps(d)


def step_tasks(d):
    have = [t["task_name"] for t in (d.call("SYNO.Core.EventScheduler", "list", 1).get("data") or [])]
    for event, (name, script) in TASKS.items():
        if name in have:
            print("task exists:", name)
            continue
        # OUTCOME 11-24: plain strings answer 117; EventScheduler wants every string JSON-quoted, like the web UI.
        q = json.dumps
        kw = dict(task_name=q(name), owner={"0": "root"}, event=q(event), enable=True, depend_on_task=q(""),
                  notify_enable=False, notify_mail=q(""), notify_if_error=False, operation_type=q("script"),
                  operation=q(script))
        kw.update(base.confirm(d))
        base.ok("task create %s" % event, d.call("SYNO.Core.EventScheduler.Root", "create", 1, **kw))


def step_check(d):
    base.USER, base.TASK = USER, "-"
    base.step_check(d)
    for t in (d.call("SYNO.Core.EventScheduler", "list", 1).get("data") or []):
        if t["task_name"] in [v[0] for v in TASKS.values()]:
            print("task", t["task_name"], t.get("event"), "enabled" if t.get("enable") else "DISABLED")
    rc, out, _ = nas.run("awk '$2 == \"/volume1/AbyssPhotos/Photos\" {print $4}' /proc/mounts | cut -d, -f1")
    print("bind mount:", out.strip() or "NOT MOUNTED")


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
