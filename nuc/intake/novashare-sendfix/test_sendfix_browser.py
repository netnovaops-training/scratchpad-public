"""test_sendfix_browser.py - Novashare "cannot send" check (2026-11-24), real Chrome at phone size (412 x 915, touch).

    python test_sendfix_browser.py      (on the PC; runs sendfix_sbx.sh up on the NUC, opens its own ssh tunnel,
                                         sendfix_sbx.sh down at the end unless --keep)

Runs the HARDENED (step 7) intake behind a front door that adds the sign-in headers and the live step-7 Caddy header
set. Checks:
  1 every drop page (/, /sending, /routes, /devices, /relay/send, /relay/inbox, /relay/share) loads with NO console
    error, page error, CSP / Trusted Types violation or failed request (a missing /favicon.ico is ignored);
  2 a real Drop-page upload (form #f1, resumable /up/* pieces) reaches the server: the file's bytes are on disk;
  3 the PWA share-target path: POST /share is caught by the service worker, the picker opens, "File in my library"
    goes to /sending, the file is uploaded (/up/start ... /finish) and its bytes are on disk.
Outcome 2026-11-24: ALL PASS on the hardened image -> the step-7 drop pages are NOT what stopped Jason's sends.
Last line: ALL PASS / SOME FAILED.
"""
import argparse
import os
import pathlib
import subprocess
import time

from playwright.sync_api import sync_playwright

NUC = "server@100.64.0.10"
PJ = 18393
SBX = "bash ~/sandbox/novashare-sendfix/sendfix_sbx.sh"
ap = argparse.ArgumentParser()
ap.add_argument("--keep", action="store_true", help="leave the sandbox up")
args = ap.parse_args()
work = pathlib.Path(__file__).parent / "work"
work.mkdir(exist_ok=True)
fails = 0


def check(name, cond):
    global fails
    fails += not cond
    print(("PASS " if cond else "FAIL ") + name, flush=True)


def nuc(cmd, ok=(0,)):
    r = subprocess.run(["ssh", NUC, cmd], capture_output=True, text=True)
    if r.returncode not in ok:
        raise SystemExit(f"NUC command failed ({r.returncode}): {r.stderr.strip()[-400:]}")
    return r


def until(cond, secs=60):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(1)
    return False


tag = os.urandom(4).hex()
NEEDLE1, NEEDLE2 = f"needle-upload-{tag}", f"needle-share-{tag}"
F1 = work / f"drop-{tag}.pdf"
F1.write_bytes(b"%PDF-1.4\n% " + NEEDLE1.encode() + b"\n" + os.urandom(300000))
CSPWATCH = ("document.addEventListener('securitypolicyviolation', (e) => console.error('CSPVIOLATION ' + "
            "location.pathname + ' ' + e.violatedDirective + ' ' + e.blockedURI));")

print(nuc(f"{SBX} up").stdout.strip().splitlines()[-1], flush=True)
tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"{PJ}:127.0.0.1:{PJ}", NUC])
time.sleep(3)
try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True)
        c = b.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True,
                          base_url=f"http://localhost:{PJ}")
        c.add_init_script(CSPWATCH)
        p = c.new_page()
        bad, sent = [], []
        c.on("console", lambda m: m.type == "error" and "404" not in m.text and bad.append(f"console: {m.text[:200]}"))
        p.on("pageerror", lambda e: bad.append(f"pageerror: {e}"))
        c.on("requestfailed", lambda r: bad.append(f"failed: {r.method} {r.url} {r.failure}"))
        c.on("response", lambda r: r.status >= 400 and not r.url.endswith("/favicon.ico")
             and bad.append(f"HTTP {r.status} {r.url}"))
        c.on("request", lambda r: r.method != "GET" and sent.append(f"{r.method} {r.url}"))

        # ---- 1. every drop page, no errors
        for path in ["/", "/sending", "/routes", "/devices", "/relay/send", "/relay/inbox", "/relay/share"]:
            n = len(bad)
            p.goto(path)
            p.wait_for_timeout(2500)
            check(f"1: {path} loads with no console / CSP / Trusted Types error or failed request" +
                  (f" {bad[n:][:3]}" if len(bad) > n else ""), len(bad) == n)

        # ---- 2. a real Drop-page upload
        p.goto("/")
        p.wait_for_timeout(1500)
        n = len(sent)
        p.set_input_files("#u1", str(F1))
        p.click("#f1 button[type=submit]")
        until(lambda: p.locator("#o1").inner_text().strip())   # wait_for_function evals a string: the CSP forbids it
        p.wait_for_timeout(2000)
        ups = [s for s in sent[n:] if "/up/" in s]
        check(f"2: the Drop page sends the file in /up/* pieces ({len(ups)} requests)",
              any(s.startswith("POST") and s.endswith("/finish") for s in ups))
        check("2: the uploaded bytes are on the server", nuc(f"{SBX} grep {NEEDLE1}", ok=(0, 1)).returncode == 0)

        # ---- 3. share target -> service worker -> picker -> library -> /sending
        p.goto("/")
        p.wait_for_timeout(1500)
        n = len(sent)
        p.evaluate("""async ([name, needle]) => { const fd = new FormData(); fd.append('title', 't');
            const body = new TextEncoder().encode('%PDF-1.4 % ' + needle + ' ' + 'x'.repeat(200000));
            fd.append('files', new File([body], name, { type: 'application/pdf' }));
            const r = await fetch('share', { method: 'POST', body: fd }); location.href = r.url; }""",
                   [f"share-{tag}.pdf", NEEDLE2])
        p.wait_for_url("**/relay/share?k=**", timeout=20000)
        check("3: the service worker caught the share and opened the picker", "/relay/share?k=" in p.url)
        p.wait_for_selector("#items li:not(.dim)", timeout=20000)
        p.check("#d-library")
        p.click("#send")
        p.wait_for_url("**/sending**", timeout=20000)
        until(lambda: "100%" in p.locator("body").inner_text())
        check("3: /sending uploads the shared file (/up/start ... /finish)",
              any(s.startswith("POST") and s.endswith("/finish") for s in sent[n:]))
        check("3: the shared bytes are on the server", nuc(f"{SBX} grep {NEEDLE2}", ok=(0, 1)).returncode == 0)
        check(f"no console / CSP / Trusted Types error anywhere ({len(bad)})", not bad)
        for x in bad[:8]:
            print("    " + x)
        b.close()
finally:
    tunnel.terminate()
    if not args.keep:
        nuc(f"{SBX} down", ok=(0, 1))

print("ALL PASS" if not fails else "SOME FAILED")
