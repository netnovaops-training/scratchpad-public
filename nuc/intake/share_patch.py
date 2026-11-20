#!/usr/bin/env python3
"""share_patch.py - wire share.py into the intake service (T0872, 2026-11-18). Run in the intake folder.

app.py: include the share router (one import + one line at the end).
portal.py: the page head gets the manifest link and the service-worker registration, so Chrome offers
"Install app" and the installed app appears in the phone's share sheet.
Refuses to run twice (checks for the marker) and never touches anything else.
"""
from pathlib import Path

app = Path("app.py").read_text(encoding="utf-8")
if "share_router" not in app:
    app += ("\n\n# Phone share sheet -> Drop (T0872, 2026-11-18): /manifest.webmanifest, /sw.js, /icon.svg, POST /share.\n"
            "from share import router as share_router  # noqa: E402\n"
            "app.include_router(share_router)\n")
    Path("app.py").write_text(app, encoding="utf-8")
    print("app.py patched")

portal = Path("portal.py").read_text(encoding="utf-8")
old = '<meta name="viewport" content="width=device-width,initial-scale=1">\n<style>{CSS}</style>'
new = ('<meta name="viewport" content="width=device-width,initial-scale=1">\n'
       '<link rel="manifest" href="manifest.webmanifest">\n'
       '<meta name="theme-color" content="#1f6feb">\n'
       '<script>if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");</script>\n'
       '<style>{CSS}</style>')
if "manifest.webmanifest" not in portal:
    assert portal.count(old) == 1, "portal.py head not as expected"
    Path("portal.py").write_text(portal.replace(old, new), encoding="utf-8")
    print("portal.py patched")
