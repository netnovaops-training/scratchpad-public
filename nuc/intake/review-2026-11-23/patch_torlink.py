#!/usr/bin/env python3
"""patch_torlink.py - a "Tor Browser" button for Jason on the Novashare page (2026-11-23), on a COPY of intake.

    python3 patch_torlink.py COPY_OF_INTAKE

Jason, 11-23: "let me share the Tor to novashare". The admin-only links (portal.py, INTAKE_ADMIN) get the Tor
Browser at tor.home.example.net (Caddy: SSO + admin_only), next to the sandbox browser and qBittorrent.
Refuses the live folder, an already patched copy, or old text not found exactly once.
Variables: folder, p (portal.py), s (its text).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
p = folder / "portal.py"
s = p.read_text(encoding="utf-8")
if "tor.example" in s:
    raise SystemExit("already patched")
old = '''      <a href="https://sandbox.home.example.net/">Sandbox browser</a>
'''
new = old + '''      <a href="https://tor.home.example.net/">Tor Browser</a>
'''
if s.count(old) != 1:
    raise SystemExit("sandbox link not found exactly once")
s = s.replace(old, new)
compile(s, "portal.py", "exec")
p.write_text(s, encoding="utf-8")
print("patched portal.py (Tor Browser admin link)")
