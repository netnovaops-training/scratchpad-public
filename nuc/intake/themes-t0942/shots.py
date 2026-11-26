"""shots.py - phone-size screenshots of the drop's sending page in both themes (T0942). Run on the PC with an ssh
tunnel to the two NUC sandboxes (18241 atomic, 18242 garden). Uses the installed Chrome. PNGs land next to it."""
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).parent
HEADERS = {"X-Drop-Key": "sbx-th", "X-Auth-Email": "tester@example.com", "X-Auth-Level": "full"}
with sync_playwright() as p:
    browser = p.chromium.launch(channel="chrome")
    for name, port in (("atomic", 18241), ("garden", 18242)):
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2,
                                  extra_http_headers=HEADERS)
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{port}/sending", wait_until="load")
        page.wait_for_timeout(1500)
        # the SENDING state: no done/bad class, a busy line and a half-full bar (the idle page counts as done)
        page.evaluate("document.body.classList.remove('done','bad'); document.getElementById('state').textContent ="
                      " 'Sending 2 of 5…'; document.querySelector('#bar > i').style.width = '45%'")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(HERE / f"{name}-sending.png"))
        page.evaluate("document.body.classList.add('done'); document.getElementById('state').textContent = 'All filed.'")
        page.wait_for_timeout(1200)
        page.screenshot(path=str(HERE / f"{name}-done.png"))
        print(name, "title:", page.title())
        ctx.close()
    browser.close()
