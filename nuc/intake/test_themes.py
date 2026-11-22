"""test_themes.py - T0942 step 1 proof. Run INSIDE the intake image (it needs fastapi):

    python test_themes.py dump            print sha256 of PAGE and MANIFEST of the code in this folder (JSON)
    python test_themes.py check OLD.json  compare with the old dump; with a garden config check the switch + contrast

With theme atomic (default) the sending page and the manifest must be byte-for-byte the old ones.
"""
import hashlib
import json
import sys


def dump() -> dict:
    import atomic_ui
    import share
    return {"page": hashlib.sha256(atomic_ui.PAGE.encode()).hexdigest(),
            "manifest": hashlib.sha256(json.dumps(share.MANIFEST, sort_keys=True).encode()).hexdigest(),
            "page_len": len(atomic_ui.PAGE)}


def luminance(hexcol: str) -> float:
    r, g, b = (int(hexcol[i:i + 2], 16) / 255 for i in (1, 3, 5))
    lin = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def contrast(a: str, b: str) -> float:
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


if __name__ == "__main__":
    if sys.argv[1] == "dump":
        print(json.dumps(dump()))
        sys.exit(0)
    old = json.load(open(sys.argv[2]))
    import dropconfig
    import themes
    fails = 0

    def check(name, ok):
        global fails
        fails += not ok
        print(("PASS " if ok else "FAIL ") + name)

    now = dump()
    if dropconfig.BRAND["theme"] == "atomic":
        check(f"atomic: sending page byte-for-byte the old one ({now['page_len']} chars)", now["page"] == old["page"])
        check("atomic: manifest identical", now["manifest"] == old["manifest"])
        import icons_png
        import share
        check("atomic: icon.svg / 192 / 512 are the old bytes",
              share.icon().body == share.ICON.encode() and share.icon_192().body == icons_png.ICON_192
              and share.icon_512().body == icons_png.ICON_512)
    else:
        import atomic_ui
        import share
        p = atomic_ui.PAGE
        check("garden: page says Garden Drop, not Atomic", "Garden Drop" in p and "Atomic Share" not in p)
        check("garden: no placeholder left", "@@" not in p)
        import re as _re
        check("garden: no unquoted attribute value runs into '/>' (it would leave the element open)",
              not _re.search(r"=[^\"'\s>]+/>", themes.GARDEN["svg"]))
        check("garden: garden colours + flower in the page", "#0c1f15" in p and "class=bloom" in p)
        check("garden: the script's hooks are still there (atom, orbits, nucleus, glow)",
              all(f"class={c}" in p or f'class="{c}"' in p for c in ("atom", "orbits", "nucleus", "glow")))
        check("garden: manifest name + colour", share.MANIFEST["name"] == "Garden Drop"
              and share.MANIFEST["theme_color"] == "#0c1f15")
        import struct
        import xml.etree.ElementTree as ET
        import icons_png_garden as ig
        svg_body = share.icon().body.decode()
        ET.fromstring(svg_body)  # raises (= test error) if the icon SVG is not well-formed
        check("garden: /icon.svg is the garden icon, well-formed", svg_body == ig.ICON_SVG and "#0c1f15" in svg_body)
        for size, route in ((192, share.icon_192), (512, share.icon_512)):
            b = route().body
            w, h = struct.unpack(">II", b[16:24])  # PNG IHDR width/height
            check(f"garden: /icon-{size}.png is a {size}x{size} PNG of the garden icon",
                  b[:8] == b"\x89PNG\r\n\x1a\n" and (w, h) == (size, size) and b == getattr(ig, f"ICON_{size}"))
        g = themes.GARDEN["root"]
        col = dict(x.split(":") for x in g[g.index("{") + 1:g.index("}")].split(";") if x.startswith("--"))
        track = g.split("html .bar{background:")[1][:7]
        c = contrast(track, col["--bg"])
        check(f"garden bar track {track} on --bg = {c:.1f}:1 (non-text, need 3)", c >= 3)
        for fg in ("--ink", "--dim", "--cyan", "--ok", "--bad"):
            c = contrast(col[fg], col["--bg"])
            check(f"garden contrast {fg} on --bg = {c:.1f}:1 (need 4.5)", c >= 4.5)
    print("ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
