#!/usr/bin/env python3
"""map_p.py - plan step 6: map P: to \\\\192.168.1.68\\home\\Photos (Jason's personal-space photos), persistent.

Stores the DSM login (from Pass, via creds.py) in Windows Credential Manager for 192.168.1.68 with cmdkey,
then maps P: with net use /persistent:yes. Values are passed as process arguments only, never printed;
the output of cmdkey / net use is filtered so nothing but status lines reaches the console.
The old P: mapping (\\\\192.168.1.68\\Pictures) is removed first. Nothing on the NAS changes.
Run:  python D:\\scratchpad\\tools\\photos_syno\\map_p.py
OUTCOME 2026-11-24: written for plan step 6.
"""
import subprocess

from creds import dsm_login

NAS = "192.168.1.68"
TARGET = rf"\\{NAS}\home\Photos"


def quiet(args):
    r = subprocess.run(args, capture_output=True, text=True, errors="replace")
    return r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1:] if (r.stdout + r.stderr).strip() else []


def main():
    user, pw, _ = dsm_login()
    print("cmdkey", quiet(["cmdkey", f"/add:{NAS}", f"/user:{user}", f"/pass:{pw}"])[0])
    print("net use P: /delete", quiet(["net", "use", "P:", "/delete", "/y"])[0])
    rc, tail = quiet(["net", "use", "P:", TARGET, "/persistent:yes"])
    print("net use P:", rc, tail)


if __name__ == "__main__":
    main()
