#!/usr/bin/env python3
"""patch_torrent2.py - torrent.py login check for qBittorrent 5.x (2026-11-23), on a COPY of intake.

qBittorrent 5.2.3 answers a good login with 204 (not 200) and may name its cookie QBT_SID_<port> (not SID), so the
first live check said "login failed (204)". Now: any 2xx with a session cookie set counts; "Fails." body does not.
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
p = folder / "torrent.py"
s = p.read_text(encoding="utf-8")
old = '''    if r.status_code != 200 or "SID" not in c.cookies:'''
new = '''    # qBittorrent 5.x: 204 + cookie QBT_SID_<port>; 4.x: 200 "Ok." + cookie SID (2026-11-23 live check)
    if not (200 <= r.status_code < 300) or r.text.strip().lower().startswith("fails") or not any("SID" in k for k in c.cookies.keys()):'''
if s.count(old) != 1:
    raise SystemExit("login check not found")
p.write_text(s.replace(old, new), encoding="utf-8")
compile(p.read_text(encoding="utf-8"), "torrent.py", "exec")
print("patched torrent.py login check")
