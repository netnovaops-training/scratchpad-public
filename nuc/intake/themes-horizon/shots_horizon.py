"""shots_horizon.py - the horizon theme at several progress values, forward and in reverse (2026-11-22).
Sets #bar's aria-valuenow exactly like the page's paint() does, then screenshots the picture only. Needs an ssh
tunnel to the horizon sandbox on 18243. PNGs land next to this file.
Variables: HERE (output folder), HEADERS (sandbox test identity), STEPS (name, percent, done, wait in ms)."""
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
HEADERS = {"X-Drop-Key": "sbx-th", "X-Auth-Email": "tester@example.com", "X-Auth-Level": "full"}
STEPS = [("g00-star", 0, False, 1300), ("g35", 35, False, 1300), ("g70", 70, False, 1300),
         ("g100-gulp", 100, True, 450), ("g100-done", 100, True, 1600), ("g35-reverse", 35, False, 1300), ("idle-done", 0, True, 1600)]
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, extra_http_headers=HEADERS)
    page = ctx.new_page()
    page.goto("http://127.0.0.1:18243/sending", wait_until="load")
    page.wait_for_timeout(1200)
    for name, pct, done, wait in STEPS:
        page.evaluate("""([pct, done]) => {
            document.body.classList.toggle('done', done); document.body.classList.remove('bad');
            const bar = document.getElementById('bar');
            bar.firstElementChild.style.transform = 'scaleX(' + (pct / 100) + ')';
            bar.setAttribute('aria-valuenow', String(pct));
        }""", [pct, done])
        page.wait_for_timeout(wait)
        page.locator("svg.atom").screenshot(path=str(HERE / f"hz-{name}.png"))
        print(name, "--g =", page.evaluate("getComputedStyle(document.querySelector('.atom')).getPropertyValue('--g')"))
    page.screenshot(path=str(HERE / "hz-page-done.png"))
    browser.close()
