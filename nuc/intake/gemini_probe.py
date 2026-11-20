"""gemini_probe.py - one-off: what does a Gemini share URL look like to a plain fetch and to gotenberg-web? (2026-11-18)
Run inside the intake container: python3 /tmp/gemini_probe.py [url ...]. Writes /tmp/probe-N.pdf, prints text size.
"""
import pathlib
import sys

import httpx

urls = sys.argv[1:] or ["https://g.co/gemini/share/0a1b2c3d4e5f", "https://gemini.google.com/share/0a1b2c3d4e5f"]
for n, u in enumerate(urls):
    r0 = httpx.get(u, follow_redirects=True, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    t = r0.text
    title = t.split("<title>")[1].split("</title>")[0][:60] if "<title>" in t else "-"
    print(u, "->", r0.status_code, str(r0.url)[:90], "| title:", title, "| html len", len(t))
    for wait in ("3s", "8s"):
        r = httpx.post("http://gotenberg-web:3000/forms/chromium/convert/url",
                       files={"url": (None, u), "waitDelay": (None, wait)}, timeout=180)
        path = f"/tmp/probe-{n}-{wait}.pdf"
        pathlib.Path(path).write_bytes(r.content)
        txt = ""
        try:
            import pypdf
            txt = " ".join((p.extract_text() or "") for p in pypdf.PdfReader(path).pages)
        except Exception as e:  # noqa: BLE001 - probe only
            txt = f"(no text: {e})"
        print(f"  wait {wait}: pdf {r.status_code} {len(r.content)} bytes | text: {txt[:200]!r}")
