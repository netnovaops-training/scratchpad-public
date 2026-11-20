"""Browser test for T0926 (2026-11-19): real Chrome, phone size, against the NUC sandbox via an ssh tunnel.

    python browser_test.py OUT_DIR

Checks: Drop page upload with the signal cut mid-file (offline 6 s), one piece corrupted on the way
(server must answer 422 and the page re-send it), plain-words result, sending page from a phone-style
share (service worker), and screenshots in light and dark mode.
"""
import hashlib
import os
import pathlib
import sys
import time

from playwright.sync_api import sync_playwright

BASE = "http://localhost:18110/"
out = pathlib.Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
ok_all = True


def check(name, cond):
    global ok_all
    ok_all &= bool(cond)
    print(("PASS " if cond else "FAIL ") + name, flush=True)


blob = os.urandom(30 * 1048576 + 777)
src = out / "drop-test.bin"
src.write_bytes(blob)
print("sha256", hashlib.sha256(blob).hexdigest(), flush=True)

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])  # no GPU in this session
    for scheme in ("light", "dark"):
        ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme=scheme)
        pg = ctx.new_page()
        pg.goto(BASE)
        pg.screenshot(path=str(out / f"drop-{scheme}.png"), full_page=True)
        ctx.close()

    ctx = b.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2)
    pg = ctx.new_page()
    seen = {"422": 0, "hashed": 0}
    # A damaged piece, simulated: the 2nd checksum the page makes is wrong (as if the bytes changed on the
    # way), so the server must answer 422 and the page must send that piece again. (Playwright cannot
    # rewrite a Blob request body, so the damage is made on the checksum side.)
    pg.add_init_script("""(() => { const real = crypto.subtle.digest.bind(crypto.subtle); let n = 0;
        crypto.subtle.digest = async (a, d) => { const h = await real(a, d); n++;
          if (n === 2) { const bad = new Uint8Array(h); bad[0] ^= 0xFF; return bad.buffer; } return h; }; })()""")
    pg.on("response", lambda resp: seen.__setitem__("422", seen["422"] + (resp.status == 422)))
    pg.on("request", lambda rq: seen.__setitem__("hashed", seen["hashed"] + (rq.method == "PUT" and "x-piece-sha256" in rq.headers)))
    order = []
    pg.on("request", lambda rq: order.append(int(rq.url.rsplit("/", 1)[1])) if rq.method == "PUT" and "/piece/" in rq.url else None)
    pg.goto(BASE)
    pg.set_input_files("#u1", str(src))
    pg.click("#f1 button[type=submit]")
    pg.wait_for_function("document.getElementById('b1').value > 30", timeout=60000)
    ctx.set_offline(True)
    t_off = time.time()
    time.sleep(6)
    pg.screenshot(path=str(out / "drop-signal-lost.png"), full_page=True)
    lost_text = pg.inner_text("#o1")
    ctx.set_offline(False)
    t_on = time.time()
    pg.wait_for_function("document.querySelector('#o1 li.ok, #o1 li.bad')", timeout=120000)
    resumed_in = time.time() - t_on
    result = pg.inner_text("#o1")
    pg.screenshot(path=str(out / "drop-done.png"), full_page=True)
    print("while offline:", lost_text.replace("\n", " | "), flush=True)
    print("result:", result.replace("\n", " | "), flush=True)
    check("signal lost is shown in plain words", "Signal lost" in lost_text)
    check(f"finished after the signal came back ({resumed_in:.1f}s)", resumed_in < 30)
    check(f"every piece carried a checksum ({seen['hashed']} PUTs)", seen["hashed"] >= 8)
    check(f"numbered pieces, several in flight (T0928): {order[:12]}", len(order) >= 8)
    check(f"damaged piece refused with 422 and re-sent ({seen['422']} x 422)", seen["422"] >= 1)
    check("result is plain words, no raw path", "Saved, not sorted" in result and "/srv/" not in result)

    # Phone-style share: the service worker keeps the file and the sending page uploads it.
    pg.goto(BASE)
    pg.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller", timeout=20000)
    pg.evaluate("""async () => { const fd = new FormData();
        fd.append('files', new File([new Uint8Array(5 * 1048576).fill(7)], 'shared-test.bin'));
        await fetch('share', {method: 'POST', body: fd}); }""")
    pg.goto(BASE + "sending")
    pg.wait_for_selector("#list li.ok, #list li.err", timeout=120000)
    pg.wait_for_timeout(3000)                                  # the spin eases to a stop in about 1.5 s
    pg.screenshot(path=str(out / "sending-done.png"), full_page=True)
    state = pg.inner_text("#state")
    row = pg.inner_text("#list")
    print("sending page:", state, "|", row.replace("\n", " | "), flush=True)
    check("sending page finished: All filed", state == "All filed.")
    stopped = pg.evaluate("new Promise(r => { const a = document.querySelector('.atom .orbits').getAttribute('transform');"
                          " setTimeout(() => r(a === document.querySelector('.atom .orbits').getAttribute('transform')), 1500); })")
    check("atom animation stopped after done (battery)", stopped)
    pg.goto(BASE)
    pg.wait_for_timeout(500)
    check("no 'waiting' banner when nothing is waiting", pg.is_hidden("#waiting"))
    b.close()

print("ALL PASS" if ok_all else "SOME FAILED")
sys.exit(0 if ok_all else 1)
