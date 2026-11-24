#!/usr/bin/env python3
"""T0889 step 6 (2026-11-22): the Drop page's link card (portal.page()).

Checks the rendered HTML, not a browser: the form posts to share with a url field that is type=url,
required, labelled, hinted, and wired to its error text; the YouTube sentence follows METUBE_URL; the
link script reaches the browser with single backslashes. Run from the intake folder: python3 test_t0889_step6.py
"""
import html.parser
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
FAILS = []


def check(cond, what):
    """Record one PASS/FAIL line."""
    print(("PASS " if cond else "FAIL ") + what)
    if not cond:
        FAILS.append(what)


class Grab(html.parser.HTMLParser):
    """Collect every start tag's attributes by id, plus each form's fields."""

    def __init__(self):
        super().__init__()
        self.ids, self.labels, self.form, self.fields = {}, {}, None, {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "id" in a:
            self.ids.setdefault(a["id"], []).append((tag, a))
        if tag == "label" and "for" in a:
            self.labels[a["for"]] = True
        if tag == "form":
            self.form = a.get("id")
        if tag == "input" and self.form:
            self.fields.setdefault(self.form, []).append(a)


def render(metube):
    """page() for a mapped person with METUBE_URL set or unset."""
    if metube:
        os.environ["METUBE_URL"] = "http://metube.test:8081"
    else:
        os.environ.pop("METUBE_URL", None)
    import link_route
    import portal
    importlib.reload(link_route)
    importlib.reload(portal)
    return portal.page("someone@example.com", None), portal


off, portal = render(False)
g = Grab()
g.feed(off)
f3 = g.ids.get("f3", [])
check(len(f3) == 1 and f3[0][0] == "form", "exactly one form f3")
check(f3 and f3[0][1].get("action") == "share" and f3[0][1].get("method") == "post", "f3 posts to share")
check(f3 and f3[0][1].get("enctype") == "multipart/form-data", "f3 multipart (share() reads request.form)")
u3 = g.ids.get("u3", [])
check(len(u3) == 1, "exactly one #u3")
a = u3[0][1] if u3 else {}
check(a.get("type") == "url" and a.get("name") == "url", "u3 type=url name=url")
check("required" in a and a.get("inputmode") == "url", "u3 required + inputmode=url")
check(a.get("autocomplete") == "off" and a.get("spellcheck") == "false", "u3 autocomplete off, spellcheck false")
check(a.get("pattern") == "https?://.+", "u3 pattern only http(s)")
check(g.labels.get("u3") and g.labels.get("u3t"), "visible labels for u3 and u3t")
check(a.get("aria-describedby") == "u3-hint", "error id NOT in aria-describedby at load")
check(a.get("data-error") == "u3-error" and "u3-error" in g.ids, "data-error points at an existing element")
names = [x.get("name") for x in g.fields.get("f3", [])]
check(names == ["url", "text"], f"f3 sends url + text only (got {names})")
check("saved as a PDF for now" in off and "mp3</strong>" not in off, "METUBE off: no mp3 promise")
on, _ = render(True)
check("mp3</strong>" in on and "saved as a PDF for now" not in on, "METUBE on: mp3/song note shown")
check("split(/\\s+/)" in portal.JS and "split(/\\\\s+/)" not in portal.JS, "JS regex has one backslash")
check(":user-invalid+.error-msg" in portal.CSS, "error text shows only on :user-invalid")
check(len(g.ids.get("u1", [])) == 1 and len(g.ids.get("u2", [])) == 1, "upload cards u1/u2 still there")
print(f"{'ALL PASS' if not FAILS else str(len(FAILS)) + ' FAIL'}")
sys.exit(1 if FAILS else 0)
