#!/usr/bin/env python3
"""T0889 step 5: readable.extract keeps the article and drops menus, scripts, banners and attributes.

Offline, standard library only: python3 test_readable.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import readable  # noqa: E402

FAILS = []


def check(name, got, want):
    """Print PASS/FAIL for one comparison and remember failures."""
    ok = got == want
    print(("PASS" if ok else "FAIL"), name, "" if ok else f"got {got!r} want {want!r}")
    if not ok:
        FAILS.append(name)


PARA = "<p>" + "The council voted on the new library hours after a long public meeting. " * 10 + "</p>"
PAGE = f"""<!doctype html><html><head><meta charset="utf-8"><title>Library  hours
 change | Town News</title><script>alert(1)</script><style>p{{color:red}}</style></head>
<body><header><a href="/">Town News</a> HEADERTEXT</header><nav><a>Home</a> NAVTEXT</nav>
<div class="cookie-banner">We use cookies COOKIETEXT</div>
<main><article class="story has-comments"><h1 onclick="x()">Library hours change</h1>{PARA}{PARA}
<img src="https://evil.example/x.png" onerror="alert(2)"><iframe src="https://ads.example"></iframe>
<p>Tags &amp; more: <a href="javascript:alert(3)">click &lt;here&gt;</a></p>
<ul><li>Mon 9-5</li><li>Tue 9-8</li></ul><div class="share-buttons">SHARETEXT</div></article>
<aside>ASIDETEXT</aside></main><footer>FOOTERTEXT</footer><script>evil()</script></body></html>"""

title, body, n = readable.extract(PAGE.encode())
check("title from <title>, spaces squeezed", title, "Library hours change | Town News")
check("long enough for an article", n >= readable.MIN_CHARS, True)
for word in ("HEADERTEXT", "NAVTEXT", "COOKIETEXT", "ASIDETEXT", "FOOTERTEXT", "SHARETEXT", "alert", "evil()",
             "color:red"):
    check(f"dropped {word}", word in body, False)
for bad in ("onclick", "onerror", "src=", "href", "<img", "<iframe", "<a", "<script", "javascript:"):
    check(f"no {bad} in output", bad in body, False)
check("keeps the heading", "<h1>Library hours change</h1>" in body, True)
check("keeps the list", "<li>Mon 9-5</li>" in body, True)
check("link text kept, escaped", "click &lt;here&gt;" in body, True)
check("ampersand escaped once", "Tags &amp; more" in body, True)

short = "<html><title>App</title><body><div id=root></div><script>load()</script>Loading</body></html>"
check("JS app page is short", readable.extract(short)[2] < readable.MIN_CHARS, True)

nomain = f"<html><body><div>{PARA}{PARA}</div></body></html>"
check("no article/main -> body used", readable.extract(nomain)[2] >= readable.MIN_CHARS, True)

latin = ('<meta charset="iso-8859-1"><title>Caf\xe9</title><body>' + PARA + PARA + "</body>").encode("latin-1")
check("meta charset honoured", readable.extract(latin)[0], "Café")
check("unknown charset falls back to utf-8", readable.extract(b'<meta charset="nope-99"><title>x</title>')[0], "x")

out = readable.page_html("A <b> title", "<p>body</p>", "https://x.test/?a=1&b=<2>")
check("page_html escapes title", "<h1>A &lt;b&gt; title</h1>" in out, True)
check("page_html url is text, escaped", "https://x.test/?a=1&amp;b=&lt;2&gt;" in out and "href" not in out, True)

print(f"{len(FAILS)} failed" if FAILS else "ALL PASS")
sys.exit(1 if FAILS else 0)
