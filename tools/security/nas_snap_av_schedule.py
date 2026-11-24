#!/usr/bin/env python3
"""nas_snap_av_schedule.py - NAS hardening, approved 2026-11-24 (item 2): hourly Btrfs share snapshots with
retention + 7-day immutable (WORM) lock, and a weekly Antivirus Essential full scan.

Snapshots (Snapshot Replication 7.4.6, DSM 7.2.2, volume1 = Btrfs, 19 TB free on 11-24):
  schedule  hourly, every day, 00:00-23:00 (SYNO.Core.Share.Snapshot set_schedule)
  retention 24 hourly / 14 daily / 8 weekly (+ DSM's 1 monthly, 1 yearly, keep at least 5)
            (SYNO.DisasterRecovery.Retention set, policyType 20 = advanced, same as the working
            "Website backup" share)
  immutable 7 days (SYNO.DisasterRecovery.Retention set_worm_lock). An immutable snapshot cannot be deleted
            by anyone, admin included, until its lock ends: space it pins comes back only after 7 days.
Antivirus Essential (package id AntiVirus 1.5.7): replaces the old job (full scan EVERY day 00:00) with one full
  scan Saturday 02:00 (triggerTime "6:2:0", 0 = Sunday); virusAction already "quarantine_it" (checked, kept).

Shares: NOW = Paperless private videos Downloads. LATER = homes Pictures: the photo move (photos_syno steps 3-7)
  is moving/deleting many files in homes (Synology Photos personal space) and Pictures; snapshotting them now
  would lock the deleted copies for 7 days. Add them after the job ends:  --apply --shares homes Pictures

Run (from the PC; DSM login from Pass via photos_syno/creds.py, nothing printed):
  python -I D:\\scratchpad\\tools\\security\\nas_snap_av_schedule.py              # plan only (default, no change)
  python -I D:\\scratchpad\\tools\\security\\nas_snap_av_schedule.py --apply      # NOW shares + AV schedule
  python -I D:\\scratchpad\\tools\\security\\nas_snap_av_schedule.py --apply --shares homes Pictures --no-av
  python -I D:\\scratchpad\\tools\\security\\nas_snap_av_schedule.py --undo D:\\scratchpad\\tools\\security\\backup\\nas-before-<ts>.json
Every --apply first writes the old settings to backup\\nas-before-<ts>.json (schedules and retention only;
  no credentials). --undo puts those back. Snapshots already taken and immutable stay until their lock ends.
Never starts a scan: the AV change is a schedule only.
OUTCOME 2026-11-24: API names/params read from the NAS's own UI code (disaster_recovery.js, admin_center.js,
  antivirus.js) and live get calls. Applied 06:39 to Paperless/private/videos/Downloads: all calls ok; read back
  hourly next 07:00, advHourly 24 / advDaily 14 / advWeekly 8 (policyType 20 uses the adv* fields; the plain
  hourly/daily/weekly fields are ignored), immutable 7d; AV job now "6:2:0" fullScan. Backup
  backup\\nas-before-20261124-063947.json.
WARNING found 11-24: Task Scheduler runs "btrfs filesystem defragment -r /volume1" every other day (tasks
  "Disk Defrag 1/2"). Defrag un-shares extents with snapshots, so snapshotted data gets duplicated and, with
  immutable snapshots, held for 7 days. Disable those two tasks (DSM > Control Panel > Task Scheduler).
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "photos_syno"))
from dsm_api import Dsm  # noqa: E402

NOW = ["Paperless", "private", "videos", "Downloads"]
BACKUP = Path(__file__).resolve().parent / "backup"
HOURLY = {"date_type": 0, "week_name": "0,1,2,3,4,5,6", "hour": 0, "min": 0,
          "repeat_hour": 1, "repeat_min": 0, "last_work_hour": 23}
RET = {"policyType": 20, "hourly": 24, "daily": 14, "weekly": 8, "monthly": 1, "yearly": 1,
       "advHourly": 24, "advDaily": 14, "advWeekly": 8, "advMonthly": 1, "advYearly": 1,
       "advMinimum": 5, "advRetainDay": 1, "advPolicyType": 127}
WORM_DAYS = 7
# --daily (Jason 2026-11-24: "I don't need so many snapshots, it was eating too much storage"): one snapshot a
# night at 00:00, keep 7 daily + 4 weekly, immutable 3 days. DAILY's repeat_hour 0 = once a day (DSM shows
# "every 0h", as on the homes share Jason set by hand).
DAILY = {"date_type": 0, "week_name": "0,1,2,3,4,5,6", "hour": 0, "min": 0,
         "repeat_hour": 0, "repeat_min": 0, "last_work_hour": 0}
RET_DAILY = {"policyType": 20, "hourly": 0, "daily": 7, "weekly": 4, "monthly": 0, "yearly": 0,
             "advHourly": 0, "advDaily": 7, "advWeekly": 4, "advMonthly": 0, "advYearly": 0,
             "advMinimum": 3, "advRetainDay": 1, "advPolicyType": 127}
WORM_DAYS_DAILY = 3
AV_JOBS = [{"activated": True, "triggerTime": "6:2:0", "scanType": "fullScan", "scanTarget": ""}]


def ok(j):
    return "ok" if j.get("success") else f"FAILED {j.get('error')}"


def read_state(d, shares):
    st = {"shares": {}, "av": d.call("SYNO.AntiVirus.Schedule", "load", 1).get("data")}
    for s in shares:
        st["shares"][s] = {
            "schedule": d.call("SYNO.Core.Share.Snapshot", "get_schedule", 1, name=s).get("data"),
            "retention": d.call("SYNO.DisasterRecovery.Retention", "get", 1, type="Share", name=s).get("data"),
            "worm": d.call("SYNO.DisasterRecovery.Retention", "get_worm_lock", 1, type="Share", name=s).get("data"),
        }
    return st


def set_share(d, s, sched_enable, sched, task_id, ret, worm_enable, worm_day):
    r1 = d.call("SYNO.Core.Share.Snapshot", "set_schedule", 1, name=s, enable_snapshot_schedule=sched_enable,
                schedule=sched, task_id=task_id)
    params = {k: v for k, v in (ret or {}).items() if k not in ("name", "prefix", "schedule")}
    r2 = d.call("SYNO.DisasterRecovery.Retention", "set", 1, type="Share", name=s, **params)
    r3 = d.call("SYNO.DisasterRecovery.Retention", "set_worm_lock", 1, type="Share", name=s,
                worm_lock_enable=worm_enable, worm_lock_day=worm_day)
    print(f"  {s}: schedule {ok(r1)}, retention {ok(r2)}, immutable {ok(r3)}")


def summary(d, shares):
    st = read_state(d, shares)
    for s, v in st["shares"].items():
        sc, rt, wm = v["schedule"] or {}, v["retention"] or {}, v["worm"] or {}
        print(f"  {s}: snapshots {'ON' if sc.get('enable_snapshot_schedule') else 'off'} every "
              f"{(sc.get('schedule') or {}).get('repeat_hour')}h next {(sc.get('schedule') or {}).get('next_trigger_time')}"
              f" | keep h{rt.get('advHourly')}/d{rt.get('advDaily')}/w{rt.get('advWeekly')} policy {rt.get('policyType')}"
              f" | immutable {wm.get('worm_lock_enable')} {wm.get('worm_lock_day')}d")
    print("  AV jobs:", json.dumps((st["av"] or {}).get("scheduleScanJobs")))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--shares", nargs="+", default=NOW)
    ap.add_argument("--no-av", action="store_true")
    ap.add_argument("--daily", action="store_true", help="nightly snapshot, keep 7 daily + 4 weekly, lock 3 days")
    ap.add_argument("--undo")
    a = ap.parse_args()
    d = Dsm()
    good, code = d.login()
    if not good:
        print("login failed", code)
        return 1
    try:
        if a.undo:
            b = json.loads(Path(a.undo).read_text(encoding="utf-8"))
            print("undo from", a.undo)
            for s, v in b["shares"].items():
                sc = v["schedule"] or {}
                sched = {k: x for k, x in (sc.get("schedule") or {}).items()
                         if k not in ("next_trigger_time", "date", "monthly_week", "repeat",
                                      "repeat_hour_store_config", "repeat_min_store_config")}
                wm = v["worm"] or {}
                set_share(d, s, bool(sc.get("enable_snapshot_schedule")), sched, sc.get("task_id", -1),
                          v["retention"], bool(wm.get("worm_lock_enable")), wm.get("worm_lock_day", WORM_DAYS))
            if b.get("av") is not None and "av" in b.get("changed", []):
                print("  AV:", ok(d.call("SYNO.AntiVirus.Schedule", "save", 1,
                                         scanJobArray=b["av"].get("scheduleScanJobs") or [])))
            summary(d, list(b["shares"]))
            return 0
        print("current:")
        summary(d, a.shares)
        if not a.apply:
            print("\nplan only. --apply would set, per share: hourly 00-23, keep 24h/14d/8w, immutable "
                  f"{WORM_DAYS}d; AV: {'unchanged' if a.no_av else json.dumps(AV_JOBS)}")
            return 0
        sysinfo = d.call("SYNO.AntiVirus.General", "get_sys_info", 1).get("data") or {}
        print("AV state now:", {k: sysinfo.get(k) for k in ("status", "scanStatus", "scan_status", "lastScanTime")})
        BACKUP.mkdir(exist_ok=True)
        before = read_state(d, a.shares)
        before["changed"] = [] if a.no_av else ["av"]
        bf = BACKUP / f"nas-before-{time.strftime('%Y%m%d-%H%M%S')}.json"
        bf.write_text(json.dumps(before, indent=1), encoding="utf-8")
        print("backup:", bf)
        print("apply:")
        for s in a.shares:
            tid = ((before["shares"][s]["schedule"] or {}).get("task_id", -1))
            ret = dict(before["shares"][s]["retention"] or {})
            ret.update(RET_DAILY if a.daily else RET)
            set_share(d, s, True, DAILY if a.daily else HOURLY, tid, ret, True,
                      WORM_DAYS_DAILY if a.daily else WORM_DAYS)
        if not a.no_av:
            print("  AV weekly Sat 02:00:", ok(d.call("SYNO.AntiVirus.Schedule", "save", 1, scanJobArray=AV_JOBS)))
        print("after:")
        summary(d, a.shares)
        print(f"\nundo: python -I {Path(__file__)} --undo {bf}")
        return 0
    finally:
        d.logout()


if __name__ == "__main__":
    sys.exit(main())
