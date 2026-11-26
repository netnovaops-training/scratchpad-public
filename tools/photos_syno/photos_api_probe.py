#!/usr/bin/env python3
"""photos_api_probe.py - READ-ONLY: list the Synology Photos (SYNO.Foto*) APIs and their versions on the NAS,
and the DSM version. Never prints the sid, password or any share passphrase.
OUTCOME 2026-11-24: written for the Abyss nas-photos share-link job (step 1, probe before any write)."""
import json
from dsm_api import Dsm

d = Dsm()
ok, code = d.login()
if not ok:
    raise SystemExit("login failed %s" % code)
try:
    info = d._post("/webapi/query.cgi", {"api": "SYNO.API.Info", "version": "1", "method": "query", "query": "all"})
    for k, v in sorted((info.get("data") or {}).items()):
        if k.startswith("SYNO.Foto") or k.startswith("SYNO.FotoTeam") or k.startswith("SYNO.Core.Sharing"):
            print(k, v.get("minVersion"), v.get("maxVersion"), v.get("path"))
    s = d.call("SYNO.Core.System", "info", 1).get("data", {})
    print("dsm", s.get("firmware_ver"))
finally:
    d.logout()
