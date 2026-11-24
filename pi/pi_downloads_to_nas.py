#!/usr/bin/env python3
"""pi_downloads_to_nas.py - move the Pi's download lane onto the NAS Downloads share (2026-11-23, T0082).

    python3 ~/stack/pi_downloads_to_nas.py         (on the Pi, in ~/stack)

Why: DOWNLOADS=/mnt/downloads was meant to be an sshfs mount of the NUC, but the mount was not active, so
qBittorrent and MeTube wrote to the Pi's SD card (df on 11-23: /dev/mmcblk0p2). Jason 11-23 chose one NAS share
for the sandbox browser, Tor and qBittorrent. Changes in compose-download.yml (backup made by the caller):
  - qbittorrent and metube: /downloads -> volume nas_downloads (CIFS //NAS/Downloads)
  - firefox: + nas_downloads:/config/Downloads (its download folder)
  - volume nas_downloads: password ${NAS_HOMELAB_PASSWORD} injected from Proton Pass by ~/bin/dc-stack
Also writes ~/stack/secrets.refs (references only) and ~/bin/dc-stack (compose with `pass run`).
Refuses if any expected text is not found exactly as many times as expected. Nothing is printed but status.
"""
import pathlib
import stat

STACK = pathlib.Path.home() / "stack"
p = STACK / "compose-download.yml"
s = p.read_text()
if "nas_downloads:" in s:
    raise SystemExit("already patched")
old = "      - ${DOWNLOADS:?set DOWNLOADS in ai-stack/pi/.env -- it must NOT be on the SD card}:/downloads\n"
assert s.count(old) == 2, "DOWNLOADS lines"
s = s.replace(old, "      # 2026-11-23: the NAS Downloads share (DOWNLOADS=/mnt/downloads had fallen back to the SD card)\n"
                   "      - nas_downloads:/downloads\n")
old_ff = "      - firefox_config:/config\n"
assert s.count(old_ff) == 1, "firefox volume"
s = s.replace(old_ff, old_ff + "      - nas_downloads:/config/Downloads   # 2026-11-23: sandbox browser downloads -> NAS share\n")
old_v = "volumes:\n  gluetun_data:\n"
assert s.count(old_v) == 1, "volumes block"
s = s.replace(old_v, (
    "volumes:\n"
    "  # 2026-11-23: NAS share Downloads (Firefox, qBittorrent, MeTube; Tor Browser on the NUC; the NUC's download-scan\n"
    "  # checks it). The password comes from Proton Pass at start: run compose through ~/bin/dc-stack.\n"
    "  nas_downloads:\n"
    "    driver: local\n"
    "    driver_opts:\n"
    "      type: cifs\n"
    "      device: \"//192.168.1.68/Downloads\"\n"
    "      o: \"username=homelab,password=${NAS_HOMELAB_PASSWORD:-},vers=3.0,uid=1000,gid=1000,file_mode=0664,"
    "dir_mode=0775,noserverino\"\n"
    "  gluetun_data:\n"))
p.write_text(s)

refs = STACK / "secrets.refs"
refs.write_text("# Pass references for the Pi stack (2026-11-23). No values here; ~/bin/dc-stack injects them at start.\n"
                "NAS_HOMELAB_PASSWORD=pass://NUC services/NAS homelab SMB password/password\n")
refs.chmod(0o600)

w = pathlib.Path.home() / "bin" / "dc-stack"
w.write_text("#!/bin/sh\n"
             "# dc-stack (2026-11-23): docker compose for ~/stack with secrets from Proton Pass, in memory only.\n"
             "cd /home/user/stack && exec /home/user/bin/pass run --env-file /home/user/stack/secrets.refs -- "
             "docker compose -p alex -f compose.yml -f compose-download.yml \"$@\"\n")
w.chmod(w.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
print("compose-download.yml, secrets.refs, ~/bin/dc-stack written")
