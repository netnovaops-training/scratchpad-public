#!/usr/bin/env python3
"""patch_onion.py - Tor (.onion) links in Novashare's /share (2026-11-23), on a COPY of intake.

    python3 patch_onion.py COPY_OF_INTAKE

Adds onion.py (next to this patcher), socksio to requirements (httpx needs it for SOCKS proxies), and one branch
in share.py's /share: a .onion link goes to onion.handle() before the _public_url gate that refused it.
Refuses the live folder, an already patched copy, or old text not found exactly once.
"""
import shutil
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
share = folder / "share.py"
s = share.read_text(encoding="utf-8")
if "onion.handle" in s:
    raise SystemExit("already patched")
old = """            if link and not safe:
                results.append({"name": link, "status": "refused",
                                "why": "only public http(s) links are fetched"})"""
new = """            if link and onion.is_onion(link):
                # 2026-11-23 (Jason: "If I send a tor link it saves it"): fetched over Tor (onion.py), Jason only.
                results.append(await onion.handle(link, tmp, email, details))
            elif link and not safe:
                results.append({"name": link, "status": "refused",
                                "why": "only public http(s) links are fetched"})"""
if s.count(old) != 1:
    raise SystemExit("share.py refusal branch not found exactly once")
s = s.replace(old, new)
imp = "import themes  # T0942\n"
if s.count(imp) != 1:
    raise SystemExit("share.py import anchor not found")
s = s.replace(imp, imp + "import onion  # 2026-11-23: .onion links over Tor\n")
compile(s, "share.py", "exec")
share.write_text(s, encoding="utf-8")
req = folder / "requirements.txt"
r = req.read_text(encoding="utf-8")
if "socksio" not in r:
    req.write_text(r.rstrip("\n") + "\n# 2026-11-23: SOCKS support for httpx (Tor links, onion.py)\nsocksio==1.0.0\n", encoding="utf-8")
shutil.copyfile(Path(__file__).with_name("onion.py"), folder / "onion.py")
print("patched share.py, requirements.txt; added onion.py")
