"""test_purple.py - proof for patch_purple.py. Run INSIDE the patched image with a horizon drop.toml mounted:

    docker run --rm -v nova.toml:/config/drop.toml:ro --entrypoint python IMAGE test_purple.py

Checks: the horizon palette is black/purple/orange on every page; contrast meets WCAG (text 4.5:1, bar track 3:1);
the icon version tag is a hash of the new icon; no navy/cyan left in the horizon theme; atomic unchanged in shape.
Variables: fails, check(), lum(), ratio().
"""
import hashlib
import os
import pathlib
import re
import sys

for k, v in {"INTAKE_KEY": "k-test", "INTAKE_TOKEN": "t-test", "LLM_BASE": "http://x", "AUTH_LEVELS_URL": "http://x",
             "GOTENBERG_URL": "http://x"}.items():
    os.environ.setdefault(k, v)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import atomic_ui  # noqa: E402
import icons_png_horizon  # noqa: E402
import portal  # noqa: E402
import share  # noqa: E402
import themes  # noqa: E402

fails = 0


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label)
    fails += not ok


def lum(h):
    c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def ratio(a, b):
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


t = themes.current()
check("horizon theme active", t["name"] == "Novashare" and t["theme_color"] == "#07050d")
bg, card = "#07050d", "#151024"
for name, col, need in (("ink", "#efe9ff", 4.5), ("dim", "#ab9fcc", 4.5), ("accent", "#b388ff", 4.5),
                        ("orange", "#ff8a3d", 4.5)):
    for surf in (bg, card):
        r = ratio(col, surf)
        check(f"{name} {col} on {surf}: {r:.1f}:1 (need {need})", r >= need)
check(f"button text #07050d on purple: {ratio('#07050d', '#b388ff'):.1f}:1", ratio("#07050d", "#b388ff") >= 4.5)
check(f"bar track #7a62b3 on sky: {ratio('#7a62b3', bg):.1f}:1 (need 3)", ratio("#7a62b3", bg) >= 3)
page, drop = atomic_ui.PAGE, portal.page("x@example.invalid", "casey")
check("/sending uses the new palette", "--bg:#07050d" in page and "--cyan:#b388ff" in page and "#7a62b3" in page)
check("Drop page uses the new palette", "--bg:#07050d" in drop and "--accent:#b388ff" in drop)
check("result page uses the new palette", "#07050d" in share._page([{"name": "a", "status": "duplicate"}]))
check("no navy/cyan left in the horizon theme", not re.search(r"#0b1026|#5ad1ff|#131a3a|#bdeeff", t["svg"] + t["root"] + t["portal_root"] + t["mark"]))
want = "?v=" + hashlib.sha256(icons_png_horizon.ICON_192).hexdigest()[:10]
check(f"icon tag is the icon hash ({want})", share.ICON_VER == want and share.MANIFEST["icons"][0]["src"].endswith(want))
check("manifest colours are the new ones", share.MANIFEST.get("theme_color") == "#07050d")
check("icon is the black/purple one (SVG has #07050d and #a66bff)", "#07050d" in icons_png_horizon.ICON_SVG and "#a66bff" in icons_png_horizon.ICON_SVG)
check("atomic theme still its own navy (unchanged)", themes.ATOMIC["theme_color"] == "#0b1026")
check("[hidden] really hides on both pages", "[hidden]{display:none!important}" in atomic_ui.PAGE and "[hidden]{display:none!important}" in portal.page("x@example.invalid", "casey"))
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
