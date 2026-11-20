"""pwa_patch.py - share.py: PNG icons so Android can install Atomic Share as a share target (2026-11-18).

Jason 11-18: "Let's get the atomic drop working". Found: (1) the manifest listed only icon.svg; Android needs a
PNG >= 144 px to make the installed app (WebAPK) that appears in the share sheet. (2) Caddy kept the manifest
and icons behind sign-in; Chrome fetches the manifest without cookies, got the 303 to /auth, and saw no app
(fixed in Caddy, see caddy_pwa_patch.py). This adds /icon-192.png and /icon-512.png (from icons_png.py, made by
gen_icons.py) and lists them first in the manifest, purpose "any maskable". Idempotent (marker icons_png).
"""
import pathlib
import sys

p = pathlib.Path("share.py")
s = p.read_text()
if "icons_png" in s:
    sys.exit("already patched")

old_icons = '"icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]'
if old_icons not in s:
    # manifest is a Python dict in source: match its dict form
    old_icons = '"icons": [{"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}],'
assert old_icons.rstrip(",") in s, "manifest icons line not found"
new_icons = ('"icons": [{"src": "icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},\n'
             '              {"src": "icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},\n'
             '              {"src": "icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any"}]')
s = s.replace(old_icons.rstrip(","), new_icons, 1)

route_anchor = '@router.get("/sw.js")'
assert route_anchor in s
routes = '''@router.get("/icon-192.png")
def icon_192():
    import icons_png
    return Response(icons_png.ICON_192, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


@router.get("/icon-512.png")
def icon_512():
    import icons_png
    return Response(icons_png.ICON_512, media_type="image/png", headers={"Cache-Control": "public, max-age=86400"})


'''
s = s.replace(route_anchor, routes + route_anchor, 1)
s = s.replace("is refused, not filed.", "is refused, not filed.\nPNG icons 192/512 (pwa_patch.py, 11-18): Android needs a PNG to install the share target.", 1)
p.write_text(s)
print("patched")
