#!/usr/bin/env python3
"""nas_security_probe.py - READ-ONLY check of the NAS before scheduling snapshots and AV (2026-11-24).

Reports: DSM version, installed packages of interest (Snapshot Replication, Antivirus Essential, Antivirus by
McAfee), volumes (filesystem, size, free), shares (name, volume), and any existing snapshot / AV schedules.
Uses photos_syno/dsm_api.py (login without printing; sid never printed). Prints names, numbers and codes only.
Run:  python D:\\scratchpad\\tools\\security\\nas_security_probe.py
OUTCOME 2026-11-24: written for the approved security fixes, item 2 (NAS snapshots + AV).
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "photos_syno"))
from dsm_api import Dsm  # noqa: E402

SECRETISH = ("session", "sid", "cookie", "auth", "cred", "secret", "key", "token", "pass")


def redact(o):
    """Replace any value under a secret-looking key name with its length."""
    if isinstance(o, dict):
        return {k: (f"<{len(str(v))} chars>" if any(s in k.lower() for s in SECRETISH) else redact(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [redact(v) for v in o]
    return o


def show(label, j, n=1500):
    print(f"--- {label}: " + json.dumps(redact(j))[:n])


def main():
    d = Dsm()
    ok, code = d.login()
    if not ok:
        print("login failed", code)
        return
    try:
        info = d.call("SYNO.Core.System", "info", 1).get("data", {})
        print("dsm", info.get("firmware_ver"), "model", info.get("model"))
        pk = d.call("SYNO.Core.Package", "list", 2, additional=["status"])
        pkgs = {p["id"]: (p.get("version"), (p.get("additional") or {}).get("status"))
                for p in pk.get("data", {}).get("packages", [])}
        for want in ("SnapshotReplication", "AntiVirus", "AntivirusEssential", "McAfeeAntiVirus"):
            print("package", want, pkgs.get(want, "NOT INSTALLED"))
        print("all packages:", ", ".join(sorted(pkgs)))
        st = d.call("SYNO.Storage.CGI.Storage", "load_info", 1).get("data", {})
        for v in st.get("volumes", []):
            size = v.get("size", {})
            tot, used = int(size.get("total", 0)), int(size.get("used", 0))
            print(f"volume {v.get('id')} fs {v.get('fs_type')} total {tot/1e12:.2f} TB used {used/1e12:.2f} TB "
                  f"free {(tot-used)/1e12:.2f} TB ({100*(tot-used)/max(tot,1):.1f}%) status {v.get('status')}")
        sh = d.call("SYNO.Core.Share", "list", 1, additional=["vol_path", "share_quota", "enable_snapshot"])
        for s in sh.get("data", {}).get("shares", []):
            print("share", s.get("name"), s.get("vol_path"))
        # snapshot + AV schedule APIs (only answer when the packages are installed)
        for api, meth, ver, extra in (("SYNO.Core.Share.Snapshot", "list", 1, {"name": "homes"}),
                                      ("SYNO.DR.Plan", "list", 1, {}),
                                      ("SYNO.Core.Share.Snapshot.Plan", "get", 1, {"name": "homes"}),
                                      ("SYNO.AntiVirus.Schedule", "list", 1, {}),
                                      ("SYNO.AntiVirus.General", "get", 1, {})):
            show(f"{api}.{meth}", d.call(api, meth, ver, **extra), 600)
    finally:
        d.logout()


if __name__ == "__main__":
    main()
