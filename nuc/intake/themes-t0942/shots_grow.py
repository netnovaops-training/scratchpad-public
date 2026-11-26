"""shots_grow.py - the garden plant at several progress values, forward and in reverse (T0942).
Sets #bar's aria-valuenow exactly like the page script's paint() does, then screenshots the picture only.
Needs an ssh tunnel to the garden sandbox on 18242. PNGs land next to this file."""
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
HEADERS = {"X-Drop-Key": "sbx-th", "X-Auth-Email": "tester@example.com", "X-Auth-Level": "full"}
STEPS = [("g00", 0, False), ("g35", 35, False), ("g70", 70, False), ("g100-done", 100, True),
         ("g35-reverse", 35, False), ("idle-done", 0, True)]
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, extra_http_headers=HEADERS)
    page = ctx.new_page()
    page.goto("http://127.0.0.1:18242/sending", wait_until="load")
    page.wait_for_timeout(1200)
    for name, pct, done in STEPS:
        page.evaluate("""([pct, done]) => {
            document.body.classList.toggle('done', done); document.body.classList.remove('bad');
            const bar = document.getElementById('bar');
            bar.firstElementChild.style.transform = 'scaleX(' + (pct / 100) + ')';
            bar.setAttribute('aria-valuenow', String(pct));
            document.getElementById('pct').textContent = pct + '%';
        }""", [pct, done])
        page.wait_for_timeout(1300)
        page.locator("svg.atom").screenshot(path=str(HERE / f"grow-{name}.png"))
        vals = page.evaluate("getComputedStyle(document.querySelector('.atom')).getPropertyValue('--g')")
        print(name, "--g =", vals)
    browser.close()
