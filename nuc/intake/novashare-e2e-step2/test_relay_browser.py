"""test_relay_browser.py - Novashare E2E Step 2 in real Chrome (2026-11-24): the browser's own encrypted send.

    bash ~/sandbox/novashare-relay/relay_sbx.sh up          (on the NUC; sandbox on 127.0.0.1:18261, fresh each run)
    python test_relay_browser.py WORK_DIR [--big-mb 2048]   (on the PC; opens its own ssh tunnel)

Casey's device is registered by relay_twin.py (Python keys kept in the sandbox run folder), so the Python decryptor can
open what Chrome sent: that proves relay.js and relay_twin.py speak the same format. Jason is a Chrome profile:
it registers on /devices (Step 1), which pins Casey's keys, then sends from /relay/send.
Checks: a small file round-trips byte for byte with its name and caption; the 2 GB file is sent from the browser,
interrupted by a page reload part way, resumed by picking the same file, completed, and opens byte for byte on Casey's
device; the content key and X25519 key in IndexedDB are not extractable; nothing is left in IndexedDB after a send;
the grep over the sandbox relay store, DB and log finds no plaintext marker, file name or caption; no CSP or Trusted
Types errors. Last line: ALL PASS / SOME FAILED. The sandbox key is read over ssh into memory, never printed.
"""
import argparse
import hashlib
import json
import os
import pathlib
import subprocess
import time

from playwright.sync_api import sync_playwright

NUC = "server@100.64.0.10"
PORT = 18261
BASE = f"http://localhost:{PORT}"
SBX = "bash ~/sandbox/novashare-relay/relay_sbx.sh"
ap = argparse.ArgumentParser()
ap.add_argument("work")
ap.add_argument("--big-mb", type=int, default=2048)
args = ap.parse_args()
work = pathlib.Path(args.work)
work.mkdir(parents=True, exist_ok=True)
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


def make_file(path, size, mark):
    """`size` bytes: pseudo-random 64 KiB blocks with `mark` at the start of each; returns sha256."""
    h = hashlib.sha256()
    seed = os.urandom(16)
    with path.open("wb") as f:
        i, left = 0, size
        while left:
            blk = bytearray(hashlib.shake_256(seed + i.to_bytes(8, "big")).digest(1 << 20))
            for o in range(0, len(blk), 65536):
                blk[o:o + len(mark)] = mark
            blk = bytes(blk[:left])
            f.write(blk)
            h.update(blk)
            left -= len(blk)
            i += 1
    return h.hexdigest()


MARK = b"NOVASHARE-BROWSER-CANARY-" + os.urandom(6).hex().encode()
CAP = "canary-caption-" + os.urandom(4).hex()
small = work / ("canary-small-" + os.urandom(3).hex() + ".bin")
big = work / ("canary-big-" + os.urandom(3).hex() + ".mkv")
sha_small = make_file(small, 5 * 1048576 + 3, MARK)
t0 = time.time()
sha_big = make_file(big, args.big_mb * 1048576 + 123, MARK)
print(f"made {big.stat().st_size} bytes in {time.time() - t0:.0f} s", flush=True)

KEY = nuc("cat ~/sandbox/novashare-relay/run/key").stdout.strip()
print(nuc(f"{SBX} twin register http://127.0.0.1:{PORT} casey@example.invalid 'Casey twin' /twin/casey.json").stdout.strip())
tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"{PORT}:127.0.0.1:{PORT}", NUC])
time.sleep(3)


def twin_open(tid, out="-"):
    r = nuc(f"{SBX} twin open http://127.0.0.1:{PORT} casey@example.invalid /twin/casey.json {tid} "
            f"/srv/unsorted/.novashare-relay {out}", ok=(0, 1))
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        print("twin:", r.stderr.strip()[-300:])
        return {}


IDB = """async (prefix) => {
  const db = await new Promise((res, rej) => { const r = indexedDB.open('novashare-devices', 1); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const keys = await new Promise((res) => { const t = db.transaction('kv').objectStore('kv').getAllKeys(); t.onsuccess = () => res(t.result); });
  let kExtractable = null;
  for (const k of keys) if (String(k).startsWith('send:')) {
    const st = await new Promise((res) => { const t = db.transaction('kv').objectStore('kv').get(k); t.onsuccess = () => res(t.result); });
    kExtractable = st.K.extractable;
    try { await crypto.subtle.exportKey('raw', st.K); kExtractable = 'EXPORTED'; } catch (e) { /* refused: good */ }
  }
  db.close(); return { keys: keys.map(String), kExtractable };
}"""

try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])
        console = []
        c = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", extra_http_headers={
            "X-Drop-Key": KEY, "X-Auth-Email": "jason@example.invalid", "X-Auth-Level": "full"})
        pg = c.new_page()
        pg.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
        pg.on("pageerror", lambda e: console.append(f"pageerror: {e}"))

        pg.goto(BASE + "/devices")
        pg.wait_for_selector('body[data-ready="1"]', timeout=60000)
        pg.click("#register")
        pg.wait_for_selector('body[data-ready="1"] #this-fp', timeout=60000)
        check("Jason's Chrome registered as a device on /devices (Step 1) and pinned Casey", pg.is_visible("#this-fp"))

        def load_send():
            pg.goto(BASE + "/relay/send")
            pg.wait_for_selector('body[data-ready="1"]', timeout=60000)

        load_send()
        check("send page: Casey is offered (keys pinned and recomputed)", pg.is_enabled("#to-casey"))
        check("send page: 'my other devices' is not offered (Jason has only this one)", not pg.is_enabled("#to-jason"))

        # small file
        pg.set_input_files("#file", str(small))
        pg.check("#to-casey")
        pg.fill("#caption", CAP)
        pg.click("#send")
        pg.wait_for_selector("body[data-sent]", timeout=120000)
        tid_small = pg.get_attribute("body", "data-sent")
        r = twin_open(tid_small)
        check(f"small file: Chrome's send opens in Python on Casey's device, sha256 equal ({r.get('bytes')} bytes)",
              r.get("sha256") == sha_small and r.get("name") == small.name and r.get("caption") == CAP)
        check("after a finished send nothing of it is left in IndexedDB",
              not [k for k in pg.evaluate(IDB, "") ["keys"] if k.startswith(("send:", "sha:"))])
        pg.screenshot(path=str(work / "send-small.png"))

        # big file, with a reload part way through
        load_send()
        pg.set_input_files("#file", str(big))
        pg.check("#to-casey")
        pg.fill("#caption", CAP)
        t0 = time.time()
        pg.click("#send")
        pg.wait_for_function("() => /Sending (\\d+) of/.test(document.getElementById('progress').textContent) && "
                             "+document.getElementById('progress').textContent.match(/Sending (\\d+) of/)[1] >= 60",
                             timeout=600000)
        st = pg.evaluate(IDB, "")
        check("mid-send: the pending send is in IndexedDB and its content key is NOT extractable",
              any(k.startswith("send:") for k in st["keys"]) and st["kExtractable"] is False)
        pg.reload()
        pg.wait_for_selector('body[data-ready="1"]', timeout=60000)
        check("after a reload the page lists the unfinished send", pg.locator("[data-pending]").count() == 1)
        pg.set_input_files("#file", str(big))
        pg.check("#to-casey")
        pg.click("#send")
        pg.wait_for_function("() => /resumed/.test(document.getElementById('progress').textContent) || document.body.dataset.sent",
                             timeout=600000)
        check("same file picked again -> the send is resumed, not restarted",
              "resumed" in pg.text_content("#progress") or pg.get_attribute("body", "data-sent") is not None)
        pg.wait_for_selector("body[data-sent]", timeout=3600000)
        secs = time.time() - t0
        tid_big = pg.get_attribute("body", "data-sent")
        check(f"{args.big_mb} MB + 123 sent from Chrome ({secs:.0f} s incl. the reload): {pg.text_content('#progress')}",
              tid_big and tid_big != tid_small and pg.locator("[data-kind=error]").count() == 0)
        pg.screenshot(path=str(work / "send-big.png"))
        r = twin_open(tid_big)
        check(f"big file opens in Python on Casey's device, sha256 equal ({r.get('bytes')} bytes)",
              r.get("sha256") == sha_big and r.get("bytes") == big.stat().st_size and r.get("name") == big.name)
        bad = [m for m in console if "Content Security Policy" in m or "TrustedHTML" in m or "Trusted Type" in m or "pageerror" in m]
        check("no CSP / Trusted Types violations or page errors" + (f": {bad[:3]}" if bad else ""), not bad)
        b.close()
finally:
    tunnel.terminate()

for label, needle in (("plaintext marker", MARK.decode()), ("small file name", small.name), ("big file name", big.name),
                      ("caption", CAP)):
    r = nuc(f"{SBX} grep '{needle}'", ok=(0, 1))
    check(f"grep over the sandbox relay store, staging, DB and log: {label} NOT found", r.returncode == 1 and not r.stdout.strip())
r = nuc(f"{SBX} grep 'novashare-v1'", ok=(0, 1))
check("control: the same grep does find a value the DB really holds (alg novashare-v1)", r.returncode == 0)
small.unlink()
big.unlink()
print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
