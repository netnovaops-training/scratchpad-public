#!/usr/bin/env python3
"""patch_torrent.py - Novashare -> qBittorrent route (magnet links, .torrent files), 2026-11-23, on a COPY of intake.

    python3 patch_torrent.py COPY_OF_INTAKE

Adds torrent.py (next to this patcher); share.py /share: a magnet link (in the shared URL or text) goes to
torrent.handle_magnet before any other link branch (a magnet was refused as "not public http(s)");
app.py route_file: a .torrent file goes to torrent.handle_file right after the virus gate (it went to unsorted).
Refuses the live folder, an already patched copy, or old text not found exactly once.
"""
import shutil
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
files = {n: (folder / n).read_text(encoding="utf-8") for n in ("share.py", "app.py")}
if "torrent.handle_magnet" in files["share.py"]:
    raise SystemExit("already patched")


def swap(name, old, new):
    if files[name].count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:60]!r}, found {files[name].count(old)}")
    files[name] = files[name].replace(old, new)


swap("share.py", "import onion  # 2026-11-23: .onion links over Tor\n",
     "import onion  # 2026-11-23: .onion links over Tor\nimport torrent  # 2026-11-23: magnet links -> qBittorrent\n")
swap("share.py", "            if link and onion.is_onion(link):\n",
     "            if (mag := torrent.find_magnet(f\"{url} {text}\")):\n"
     "                # 2026-11-23 (Jason: \"more of a BitTorrent\"): magnet -> qBittorrent on the Pi (torrent.py)\n"
     "                from starlette.concurrency import run_in_threadpool as _in_thread\n"
     "                results.append(await _in_thread(torrent.handle_magnet, mag, email, details))\n"
     "            elif link and onion.is_onion(link):\n")
swap("app.py", '''    _virus_gate(tmp, name, digest, f"drop:{who or 'unknown'}", f"drop from {who or 'unknown'}")
''', '''    _virus_gate(tmp, name, digest, f"drop:{who or 'unknown'}", f"drop from {who or 'unknown'}")
    if name.lower().endswith(".torrent"):   # 2026-11-23: a .torrent goes to qBittorrent (torrent.py), not unsorted
        import torrent
        return {**torrent.handle_file(tmp, name, email), "size": size}
''')
for n, t in files.items():
    compile(t, n, "exec")
    (folder / n).write_text(t, encoding="utf-8")
shutil.copyfile(Path(__file__).with_name("torrent.py"), folder / "torrent.py")
print("patched share.py, app.py; added torrent.py")
