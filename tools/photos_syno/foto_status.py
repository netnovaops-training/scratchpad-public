#!/usr/bin/env python3
"""foto_status.py - plan step 7: read-only check that Synology Photos sees the personal-space library.

Calls (DSM Web API, LAN): SYNO.Foto.Browse.Folder list (top folders of the personal space and of Library),
SYNO.Foto.Browse.Item count, SYNO.Foto.Index get/status if offered, SYNO.Foto.BackgroundTask.Info list.
Prints counts and folder names only. Changes nothing.
Run:  python D:\\scratchpad\\tools\\photos_syno\\foto_status.py
OUTCOME 2026-11-24: written for plan step 7.
"""
import json

from dsm_api import Dsm


def short(j, n=600):
    return json.dumps(j)[:n]


def main():
    d = Dsm()
    ok, code = d.login()
    if not ok:
        print("login failed", code)
        return
    try:
        root = d.call("SYNO.Foto.Browse.Folder", "get", 1)
        print("root folder:", short(root, 300))
        rid = (root.get("data", {}).get("folder") or {}).get("id")
        lst = d.call("SYNO.Foto.Browse.Folder", "list", 1, offset=0, limit=100, id=rid) if rid else \
            d.call("SYNO.Foto.Browse.Folder", "list", 1, offset=0, limit=100)
        folders = lst.get("data", {}).get("list", [])
        print("personal-space folders:", [f.get("name") for f in folders])
        lib = [f for f in folders if f.get("name") in ("/Library", "Library")]
        if lib:
            sub = d.call("SYNO.Foto.Browse.Folder", "list", 1, offset=0, limit=200, id=lib[0]["id"])
            print("Library subfolders seen:", len(sub.get("data", {}).get("list", [])),
                  [f.get("name") for f in sub.get("data", {}).get("list", [])][:40])
        print("items (timeline count):", short(d.call("SYNO.Foto.Browse.Item", "count", 1), 200))
        for api, m in (("SYNO.Foto.Index", "get"), ("SYNO.Foto.BackgroundTask.Info", "list"),
                       ("SYNO.Foto.Setting.User", "get")):
            print(api, m, short(d.call(api, m, 1)))
    finally:
        d.logout()


if __name__ == "__main__":
    main()
