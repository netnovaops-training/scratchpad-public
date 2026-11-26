"""test_ui.py - proof for patch_ui.py (screens review 2026-11-23). Run INSIDE the patched image with a horizon
drop.toml mounted at /config/drop.toml:

    docker run --rm -v nova.toml:/config/drop.toml:ro --entrypoint python IMAGE test_ui.py [DUMP_DIR]

Checks the pages say the theme name, not "Drop"; no build notes; admin links for the admin only; the theme palette
and mark on the Drop page; the new states in /sending; plain words on the fallback page; every page parses.
With DUMP_DIR, writes drop.html, drop-other.html, sending.html, result.html there for a screenshot.
Variables: fails, check(), name.
"""
import html.parser
import os
import pathlib
import re
import sys

os.environ.setdefault("INTAKE_KEY", "k-test")
os.environ.setdefault("INTAKE_TOKEN", "t-test")
for k in ("LLM_BASE", "AUTH_LEVELS_URL", "GOTENBERG_URL"):
    os.environ.setdefault(k, "http://x")
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import atomic_ui  # noqa: E402
import portal  # noqa: E402
import share  # noqa: E402
import themes  # noqa: E402

fails = 0
name = themes.current()["name"]


def check(label, ok):
    global fails
    print(("PASS " if ok else "FAIL ") + label)
    fails += not ok


class P(html.parser.HTMLParser):
    pass


admin = portal.page("a@example.invalid", "jason")
other = portal.page("b@example.invalid", "casey")
results = [{"name": "book.pdf", "status": "filed", "kind": "book", "dest": "/srv/books/casey", "person": "casey", "path": "/srv/books/casey/book.pdf"},
           {"name": "x.bin", "status": "error", "why": "This file could not be filed."}]
result = share._page(results)
pages = {"drop.html": admin, "drop-other.html": other, "sending.html": atomic_ui.PAGE, "result.html": result}
# Step 7 (2026-11-24): the pages' style and script are files under /assets now (strict CSP: no inline script/style);
# the content checks below read page + its files. ADMIN_ALL / SENDING_ALL are those joined texts.
import site_assets  # noqa: E402

ADMIN_ALL = admin + site_assets.body("portal.css").decode() + site_assets.body("portal.js").decode()
SENDING_ALL = atomic_ui.PAGE + site_assets.body("sending.css").decode() + site_assets.body("sending.js").decode()

check(f"theme name is {name!r} (horizon config mounted)", name != "Atomic Share")
check("#1 Drop page heading is the theme name", f"{name}</h1>" in admin and "<h1>Drop</h1>" not in admin)
check("#1 no 'Back to Drop' / 'Drop could not' / 'Too big for Drop' anywhere",
      not any(s in x for x in (*pages.values(), atomic_ui.ATOMIC_JS) for s in ("Back to Drop", "Drop could not", "Too big for Drop", "Drop: done")))
check("#1 engine messages carry the name", f"Too big for {name}." in atomic_ui.ATOMIC_JS and "@@" not in atomic_ui.ATOMIC_JS)
check("#2 no build notes (T0872, Proton Pass, VPN, routed by its tag, unsorted/)",
      not any(s in admin for s in ("T0872", "Proton Pass", "VPN", "routed by its tag", "<code>unsorted/</code>", "<footer>")))
check("#3 theme palette + mark on the Drop page", ":root:not(#_){--ink:#efe9ff" in ADMIN_ALL and 'class="mark"' in admin)
check("#4/#9/#14 horizon error, done and debris rules", all(s in SENDING_ALL for s in (
    "body.bad .atom .star .nucleus{fill:var(--bad)}", "body.done .atom.hz-full .ring{stroke:var(--ok)", "opacity:calc(1 - max(0, var(--g) - 0.9) * 10)")))
check("#5 fallback page in plain words, themed", "Filed: a book on Casey&#x27;s shelf." in result and "&rarr;" not in result
      and "#07050d" in result and f"· {name}</title>" in result)
check("#6 Drop page numbers row + per-file percent", 'id="n1"' in admin and "'Sending… '+Math.floor" in ADMIN_ALL)
check("#7 empty /sending hides bar and numbers", "$('bar').hidden = true; $('nums').hidden = true;" in SENDING_ALL)
check("#8 buttons named by result", "Upload to library</button>" in admin and "Upload videos</button>" in admin)
check("#10 admin links for the admin only", "torrent." in admin and "sandbox." in admin and "grab." in admin
      and not any(s in other for s in ("torrent.", "sandbox.", "grab.")))
check("#10 everyone keeps Reader and Jellyfin", ">Reader<" in other and ">Jellyfin<" in other)
check("#12 full-width Done", "<a class=done href=\"./\">Done</a>" in atomic_ui.PAGE)
check("#13 no arrows in link text", "&rarr;" not in admin)
check("#15 tab title follows state", "'% · ' + APP" in SENDING_ALL and "'Needs a look' : 'Shared') + ' · ' + APP" in SENDING_ALL
      and f'data-app="{name}"' in atomic_ui.PAGE)
check("no @@ placeholder left", not any("@@" in x for x in pages.values()))
for fn, text in pages.items():
    try:
        P().feed(text)
        check(f"{fn} parses", True)
    except Exception as e:  # noqa: BLE001
        check(f"{fn} parses ({e})", False)
check("no unquoted attribute value runs into '/>' in the mark", not re.search(r"=[^\"'\s>]+/>", themes.current().get("mark", "")))

if len(sys.argv) > 1:
    out = pathlib.Path(sys.argv[1])
    for fn, text in pages.items():
        (out / fn).write_text(text, encoding="utf-8")
    (out / "atomic.js").write_text(atomic_ui.ATOMIC_JS, encoding="utf-8")
print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
