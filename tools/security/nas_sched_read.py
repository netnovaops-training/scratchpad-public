#!/usr/bin/env python3
"""nas_sched_read.py - READ-ONLY: show the existing snapshot schedule/retention of one share (default
"Website backup", an existing working example) and the Antivirus schedule list, via the DSM API.
Secret-looking keys and any e-mail address are redacted. Changes nothing.
Run:  python D:\\scratchpad\\tools\\security\\nas_sched_read.py ["share name" ...]
OUTCOME 2026-11-24: written to learn the set_schedule / AntiVirus.Schedule save formats from live data.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "photos_syno"))
from dsm_api import Dsm  # noqa: E402

SECRETISH = ("session", "sid", "cookie", "auth", "cred", "secret", "key", "token", "pass")
MAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def redact(o):
    if isinstance(o, dict):
        return {k: (f"<{len(str(v))} chars>" if any(s in k.lower() for s in SECRETISH) else redact(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [redact(v) for v in o]
    if isinstance(o, str):
        return MAIL.sub("<email>", o)
    return o


def main():
    shares = sys.argv[1:] or ["Website backup"]
    d = Dsm()
    ok, code = d.login()
    if not ok:
        print("login failed", code)
        return
    try:
        for s in shares:
            for meth in ("get_schedule", "get_share_conf"):
                for ver in (2, 1):
                    j = d.call("SYNO.Core.Share.Snapshot", meth, ver, name=s)
                    if j.get("success"):
                        break
                print(f"{s} {meth} v{ver}:", json.dumps(redact(j))[:1500])
        print("AV schedule:", json.dumps(redact(d.call("SYNO.AntiVirus.Schedule", "load", 1)))[:1500])
        print("AV config:", json.dumps(redact(d.call("SYNO.AntiVirus.Config", "get", 1)))[:1500])
    finally:
        d.logout()


if __name__ == "__main__":
    main()
