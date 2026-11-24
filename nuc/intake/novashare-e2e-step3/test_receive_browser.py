"""test_receive_browser.py - Novashare E2E Step 3 in real Chrome (2026-11-24): two people, two devices, round trip.

    python test_receive_browser.py WORK_DIR [--big-mb 2048]   (on the PC; runs receive_sbx.sh up on the NUC first
                                                              = a fresh sandbox, and opens its own ssh tunnel)

Two Chrome profiles (separate IndexedDB keys): Jason (desktop) and Casey (phone-sized, an on-disk profile). Each
registers on /devices (Step 1) and pins the other; Jason sends from /relay/send (Step 2); Casey receives on /relay/inbox.
Save targets exercised for real, nothing in the page code is stubbed:
  - "Use Downloads instead" = the service-worker streamed download: Playwright's download event gets the file.
  - the folder: showDirectoryPicker cannot be clicked headless, so ONLY the native picker dialog is replaced (init
    script) by a folder in the profile's origin-private file system; the page then uses the same File System Access
    calls (permission, getFileHandle, createWritable, write, close / abort + removeEntry) as on Jason's PC.
Checks: names and the note are shown only after the device unlocked them; 50 MB through the download path and the
2 GB file through the folder path come out byte for byte (SHA-256; the folder file is hashed inside the page by a
test-only JS SHA-256 that is first checked against Python's); the folder is asked for once and remembered; the
browser's memory stays bounded during the 2 GB receive; a tampered piece is refused in both paths and nothing is
left (folder: no file; download: the download fails), the delivery stays accepted; decline -> the sender's page shows
declined, the wrap and the ciphertext are gone; the sender's page shows saved; a used wrap cannot be fetched again;
no plaintext marker, file name or note on the server; no CSP / Trusted Types errors. Last line: ALL PASS / SOME FAILED.
The sandbox drop key never leaves the NUC (the proxy adds it).
"""
import argparse
import base64
import hashlib
import json
import os
import pathlib
import subprocess
import threading
import time

from playwright.sync_api import sync_playwright

NUC = "server@100.64.0.10"
PJ, PZ = 18263, 18264          # the sandbox Caddy front door signs in Jason on PJ and Casey on PZ (forward_auth live)
SBX = "bash ~/sandbox/novashare-receive/receive_sbx.sh"
ap = argparse.ArgumentParser()
ap.add_argument("work")
ap.add_argument("--big-mb", type=int, default=2048)
ap.add_argument("--mid-mb", type=int, default=50)
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
    """`size` bytes: pseudo-random 1 MiB blocks with `mark` every 64 KiB; returns sha256."""
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


def sha_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while b := f.read(1 << 22):
            h.update(b)
    return h.hexdigest()


MARK = b"NOVASHARE-RECEIVE-CANARY-" + os.urandom(6).hex().encode()
CAP = "canary-note-" + os.urandom(4).hex()
tag = os.urandom(3).hex()
F = {"mid": work / f"canary-mid-{tag}.bin", "big": work / f"canary-big-{tag}.mkv", "bad1": work / f"canary-bad1-{tag}.pdf",
     "bad2": work / f"canary-bad2-{tag}.zip", "no": work / f"canary-no-{tag}.jpg"}
SIZES = {"mid": args.mid_mb * 1048576 + 3, "big": args.big_mb * 1048576 + 123, "bad1": 12 * 1048576 + 5,
         "bad2": 6 * 1048576 + 7, "no": 1048576 + 1}
SHA = {}
t0 = time.time()
for k, p in F.items():
    SHA[k] = make_file(p, SIZES[k], MARK)
print(f"made test files in {time.time() - t0:.0f} s", flush=True)

print(nuc(f"{SBX} up").stdout.strip().splitlines()[-1], flush=True)   # fresh container + DB every run
tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"{PJ}:127.0.0.1:{PJ}",
                           "-L", f"{PZ}:127.0.0.1:{PZ}", NUC])
time.sleep(3)

# Only the native folder dialog is replaced (it cannot be clicked headless): it returns a folder in this profile's
# origin-private file system, and counts how often it was asked for.
PICKER = """window.showDirectoryPicker = async () => {
  localStorage.setItem('pickCount', String(+(localStorage.getItem('pickCount') || 0) + 1));
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle('picked', { create: true });
};"""

# test-only streaming SHA-256 (FIPS 180-4) over a file in the picked folder; WebCrypto cannot hash a stream.
SHA_JS = """async ([name, b64]) => {
  const K = new Uint32Array([0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2]);
  const H = new Uint32Array([0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]);
  const W = new Uint32Array(64);
  const r = (x, n) => (x >>> n) | (x << (32 - n));
  function blocks(u8, off, end) {
    const v = new DataView(u8.buffer, u8.byteOffset);
    for (; off + 64 <= end; off += 64) {
      for (let i = 0; i < 16; i++) W[i] = v.getUint32(off + i * 4);
      for (let i = 16; i < 64; i++) { const a = W[i - 15], b = W[i - 2]; W[i] = (W[i - 16] + (r(a, 7) ^ r(a, 18) ^ (a >>> 3)) + W[i - 7] + (r(b, 17) ^ r(b, 19) ^ (b >>> 10))) | 0; }
      let a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (let i = 0; i < 64; i++) {
        const t1 = (h + (r(e, 6) ^ r(e, 11) ^ r(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + W[i]) | 0;
        const t2 = ((r(a, 2) ^ r(a, 13) ^ r(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
        h = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
      }
      H[0] += a; H[1] += b; H[2] += c; H[3] += d; H[4] += e; H[5] += f; H[6] += g; H[7] += h;
    }
    return off;
  }
  let carry = new Uint8Array(0), total = 0;
  async function feed(u8) {
    total += u8.length;
    let buf = u8;
    if (carry.length) { buf = new Uint8Array(carry.length + u8.length); buf.set(carry); buf.set(u8, carry.length); }
    const used = blocks(buf, 0, buf.length);
    carry = buf.slice(used);
  }
  if (b64) { await feed(Uint8Array.from(atob(b64), (c) => c.charCodeAt(0))); }
  else {
    const dir = await (await navigator.storage.getDirectory()).getDirectoryHandle('picked');
    const rd = (await (await dir.getFileHandle(name)).getFile()).stream().getReader();
    for (;;) { const { done, value } = await rd.read(); if (done) break; await feed(value); }
  }
  const pad = new Uint8Array(((carry.length + 9 + 63) >> 6) << 6); pad.set(carry); pad[carry.length] = 0x80;
  const bits = total * 8, v = new DataView(pad.buffer);
  v.setUint32(pad.length - 8, Math.floor(bits / 4294967296)); v.setUint32(pad.length - 4, bits >>> 0);
  blocks(pad, 0, pad.length);
  return { bytes: total, sha: Array.from(H, (x) => x.toString(16).padStart(8, '0')).join('') };
}"""
LS_JS = """async () => { try { const dir = await (await navigator.storage.getDirectory()).getDirectoryHandle('picked');
  const out = []; for await (const [n] of dir.entries()) out.push(n); return out; } catch (e) { return ['ERR ' + e.name]; } }"""
FETCH_JS = """async ([path, dev]) => (await fetch(path, { headers: { 'X-Novashare': '1', 'X-Device-Id': dev } })).status"""

MARKER = "--novashare-receive-test=" + tag
peak = {"rss": 0, "run": False}
out_mid = work / ("got-" + F["mid"].name)


def mem_sampler():
    """Largest working set of any process of this test's Chrome (the browser marked by MARKER + its children)."""
    ps = ("$all = Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\"; "
          f"$b = ($all | Where-Object {{ $_.CommandLine -like '*{MARKER}*' }}).ProcessId; "
          "($all | Where-Object { ($b -contains $_.ProcessId) -or ($b -contains $_.ParentProcessId) } | "
          "Measure-Object WorkingSetSize -Maximum).Maximum")
    while peak["run"]:
        try:
            v = int(float(subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True,
                                         timeout=60).stdout.strip() or 0))
            peak["rss"] = max(peak["rss"], v)
        except (ValueError, subprocess.SubprocessError):
            pass
        time.sleep(5)


try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])
        console = []
        zprof = work / f"casey-profile-{tag}"

        def ctx(email, port, persistent=False, **kw):
            common = dict(color_scheme="dark", accept_downloads=True, base_url=f"http://localhost:{port}", **kw)
            if persistent:   # Casey: an on-disk profile (an in-memory one caps origin storage near 2 GB, tested 11-24)
                c = pw.chromium.launch_persistent_context(str(zprof), channel="chrome", headless=True,
                                                          args=["--disable-gpu", MARKER], **common)
            else:
                c = b.new_context(**common)
            p = c.pages[0] if c.pages else c.new_page()
            p.on("console", lambda m: console.append(f"{email[:4]} {m.type}: {m.text}"))
            p.on("pageerror", lambda e: console.append(f"{email[:4]} pageerror: {e}"))
            if os.environ.get("RX_DEBUG"):
                p.on("response", lambda r: r.status >= 400 and console.append(f"{email[:4]} HTTP {r.status} {r.url}"))
                c.on("serviceworker", lambda w: console.append(f"{email[:4]} sw {w.url}"))
            return c, p

        cj, J = ctx("jason@example.invalid", PJ, viewport={"width": 1280, "height": 900})
        cz, Z = ctx("casey@example.invalid", PZ, persistent=True, viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        cz.add_init_script(PICKER)

        def register(pg):
            pg.goto("/devices")
            pg.wait_for_selector('body[data-ready="1"]', timeout=60000)
            pg.click("#register")
            pg.wait_for_selector('body[data-ready="1"] #this-fp', timeout=60000)

        register(J)
        register(Z)                 # Casey pins Jason's device (first sight)
        J.goto("/devices")   # Jason pins Casey's device (first sight)
        J.wait_for_selector('body[data-ready="1"]', timeout=60000)
        check("two people registered a device each and pinned each other",
              J.locator("[data-safety=casey]").count() == 1 and Z.locator("[data-safety=jason]").count() == 1)
        ZDEV = Z.evaluate("""async () => { const db = await new Promise((r) => { const q = indexedDB.open('novashare-devices', 1); q.onsuccess = () => r(q.result); });
          return await new Promise((r) => { const t = db.transaction('kv').objectStore('kv').get('device'); t.onsuccess = () => r(t.result.device_id); }); }""")

        def send(key, caption=CAP):
            J.goto("/relay/send")
            J.wait_for_selector('body[data-ready="1"]', timeout=60000)
            J.set_input_files("#file", str(F[key]))
            J.check("#to-casey")
            J.fill("#caption", caption)
            t = time.time()
            J.click("#send")
            J.wait_for_selector("body[data-sent]", timeout=3600000)
            tid = J.get_attribute("body", "data-sent")
            print(f"    sent {key} ({SIZES[key]} bytes) in {time.time() - t:.0f} s: t={tid[:8]}", flush=True)
            return tid

        def inbox():
            Z.goto("/relay/inbox")
            Z.wait_for_selector('body[data-ready="1"]', timeout=60000)

        def card(tid):
            return Z.locator(f"#inbox li[data-tid='{tid}']")

        def state(tid):
            return json.loads(nuc(f"{SBX} state {tid}").stdout.strip().splitlines()[-1])

        def sender_sees(tid, want):
            J.goto("/relay/send")
            J.wait_for_selector('body[data-ready="1"]', timeout=60000)
            return J.locator(f"li[data-sent-id='{tid}'] [data-delivery='{want}']").count() == 1

        # ---- 1. 50 MB through the service-worker download (the phone path)
        tid_mid = send("mid")
        inbox()
        c = card(tid_mid)
        c.locator(".fname", has_text=F["mid"].name).wait_for(timeout=60000)
        check("inbox: the name and note appear only after this device unlocked them",
              c.locator(".fname").text_content() == F["mid"].name and c.locator(".note").text_content() == CAP
              and "Jason" in c.text_content() and f"{args.mid_mb}.0 MB" in c.text_content())
        Z.click("#use-downloads")
        Z.wait_for_selector("#use-downloads[aria-pressed=true]", timeout=10000)
        check("save target switched to Downloads (service-worker stream)", Z.get_attribute("#use-downloads", "aria-pressed") == "true")
        t = time.time()
        try:
            with Z.expect_download(timeout=int(os.environ.get("DL_WAIT", "120000"))) as dl_info:
                c.locator("[data-act=accept]").click()
        except Exception:
            print("DEBUG status:", c.locator("[data-status]").text_content(), "console:", console[-15:])
            raise
        dl = dl_info.value
        try:
            dl.save_as(out_mid)
        except Exception:
            time.sleep(3)
            print("DEBUG status:", c.locator("[data-status]").text_content(), dl.failure(), dl.url, "ctl:",
                  Z.evaluate("!!navigator.serviceWorker.controller"), "console:", console[-15:])
            raise
        c.locator("[data-status][data-saved]").wait_for(timeout=600000)
        check(f"50 MB via download: suggested name kept, byte for byte ({time.time() - t:.0f} s)",
              dl.failure() is None and dl.suggested_filename == F["mid"].name and sha_file(out_mid) == SHA["mid"])
        s = state(tid_mid)
        check(f"after the save: delivery downloaded, wrap gone, transfer deleted, ciphertext gone {s}",
              s["transfer"] == "deleted" and s["wraps"] == 0 and not s["file"] and list(s["deliveries"].values()) == ["downloaded"])
        check("replay: the used wrap (envelope) cannot be fetched again -> 404",
              Z.evaluate(FETCH_JS, [f"/relay/api/transfers/{tid_mid}/envelope", ZDEV]) == 404)
        check("replay: its pieces cannot be fetched again -> 410",
              Z.evaluate(FETCH_JS, [f"/relay/api/transfers/{tid_mid}/pieces/0", ZDEV]) == 410)
        check("the sender's page shows it saved", sender_sees(tid_mid, "downloaded"))
        Z.screenshot(path=str(work / "inbox-saved-download.png"), full_page=True)

        # ---- 2. the big file through the remembered folder (the PC path), with the memory watched
        probe = os.urandom(3 * 1048576 + 17)
        r = Z.evaluate(SHA_JS, ["", base64.b64encode(probe).decode()])
        check("test-only JS SHA-256 agrees with Python's", r["sha"] == hashlib.sha256(probe).hexdigest())
        tid_big = send("big")
        inbox()
        Z.click("#use-downloads")   # back to the folder
        c = card(tid_big)
        c.locator(".fname", has_text=F["big"].name).wait_for(timeout=60000)
        peak["run"] = True
        th = threading.Thread(target=mem_sampler, daemon=True)
        th.start()
        t = time.time()
        c.locator("[data-act=accept]").click()
        c.locator("[data-status][data-saved], [data-status].bad").wait_for(timeout=3600000)
        secs = time.time() - t
        print("    big status:", c.locator("[data-status]").text_content()[:160], flush=True)
        peak["run"] = False
        th.join(70)
        r = Z.evaluate(SHA_JS, [F["big"].name, ""])
        check(f"{args.big_mb} MB via the folder: byte for byte ({r['bytes']} bytes, {secs:.0f} s, "
              f"{SIZES['big'] / 1048576 / max(secs, 1):.0f} MB/s)", r["sha"] == SHA["big"] and r["bytes"] == SIZES["big"])
        check(f"memory stayed bounded while receiving: largest Chrome process {peak['rss'] / 1048576:.0f} MB (< 1500 MB)",
              0 < peak["rss"] < 1500 * 1048576)
        check("the folder was asked for once", Z.evaluate("localStorage.getItem('pickCount')") == "1")
        check("the save-to card names the remembered folder", Z.locator("[data-folder=picked]").count() == 1)
        check("the sender's page shows the big file saved", sender_sees(tid_big, "downloaded"))
        Z.screenshot(path=str(work / "inbox-saved-folder.png"), full_page=True)

        # ---- 3. tamper: folder path
        tid_b1 = send("bad1")
        print("    " + nuc(f"{SBX} tamper {tid_b1} 2").stdout.strip())
        inbox()
        c = card(tid_b1)
        c.locator(".fname", has_text=F["bad1"].name).wait_for(timeout=60000)
        c.locator("[data-act=accept]").click()
        c.locator("[data-status][data-code=tampered]").wait_for(timeout=120000)
        msg = c.locator("[data-status]").text_content()
        names = Z.evaluate(LS_JS)
        check(f"tampered piece 3 (folder): refused with a plain message: {msg[:90]}...", "Piece 3 of 4" in msg and "removed" in msg)
        check(f"tampered (folder): no partial file left, the folder still asked for once {names}",
              names == [F["big"].name] and Z.evaluate("localStorage.getItem('pickCount')") == "1")
        s = state(tid_b1)
        check(f"tampered (folder): delivery stays accepted, not marked received, ciphertext kept {s}",
              list(s["deliveries"].values()) == ["accepted"] and s["wraps"] == 1 and s["file"])
        Z.screenshot(path=str(work / "inbox-tampered.png"), full_page=True)

        # ---- 4. tamper: download path
        tid_b2 = send("bad2")
        nuc(f"{SBX} tamper {tid_b2} 1")
        inbox()
        Z.click("#use-downloads")
        c = card(tid_b2)
        c.locator(".fname", has_text=F["bad2"].name).wait_for(timeout=60000)
        with Z.expect_download(timeout=120000) as dl_info:
            c.locator("[data-act=accept]").click()
        dl = dl_info.value
        c.locator("[data-status][data-code=tampered]").wait_for(timeout=120000)
        fail = dl.failure()
        check(f"tampered piece 2 (download): refused, and the browser marks the download failed ({fail})", fail is not None)
        s = state(tid_b2)
        check("tampered (download): delivery stays accepted", list(s["deliveries"].values()) == ["accepted"])
        Z.click("#use-downloads")   # folder again for later runs

        # ---- 5. decline
        tid_no = send("no")
        inbox()
        c = card(tid_no)
        c.locator(".fname", has_text=F["no"].name).wait_for(timeout=60000)
        c.locator("[data-act=decline]").click()
        check("decline asks once more (the button turns into 'Yes, decline')", c.locator("[data-act=decline]").text_content() == "Yes, decline")
        c.locator("[data-act=decline]").click()
        Z.locator(f"#done li[data-tid='{tid_no}'][data-state=declined]").wait_for(timeout=30000)
        check("declined: it moves to Earlier as Declined", card(tid_no).count() == 0)
        s = state(tid_no)
        check(f"declined: wrap and ciphertext gone, transfer deleted {s}", s["wraps"] == 0 and not s["file"] and s["transfer"] == "deleted")
        check("declined: the envelope is gone for this device -> 404",
              Z.evaluate(FETCH_JS, [f"/relay/api/transfers/{tid_no}/envelope", ZDEV]) == 404)
        check("the sender's page shows declined", sender_sees(tid_no, "declined"))
        J.screenshot(path=str(work / "send-status.png"), full_page=True)

        bad = [m for m in console if "Content Security Policy" in m or "TrustedHTML" in m or "Trusted Type" in m or "pageerror" in m]
        check("no CSP / Trusted Types violations or page errors" + (f": {bad[:3]}" if bad else ""), not bad)
        cz.close()
        b.close()
finally:
    tunnel.terminate()
    import shutil
    shutil.rmtree(work / f"casey-profile-{tag}", ignore_errors=True)

for label, needle in [("plaintext marker", MARK.decode()), ("note", CAP)] + [(f"name {k}", p.name) for k, p in F.items()]:
    r = nuc(f"{SBX} grep '{needle}'", ok=(0, 1))
    check(f"grep over the sandbox relay store, DB and log: {label} NOT found", r.returncode == 1 and not r.stdout.strip())
r = nuc(f"{SBX} grep 'novashare-v1'", ok=(0, 1))
check("control: the same grep does find a value the DB really holds (alg novashare-v1)", r.returncode == 0)
for p in list(F.values()) + [out_mid]:
    p.unlink(missing_ok=True)
print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
