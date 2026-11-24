"""test_sweep_browser.py - Novashare E2E Step 6 in real Chrome, two people (2026-11-24): cancel, quotas, Relay line.

    python test_sweep_browser.py WORK_DIR      (on the PC; runs sweep_sbx.sh up on the NUC first = a fresh sandbox
                                                with a 40 MB per-person relay cap, opens its own ssh tunnel, and
                                                sweep_sbx.sh down at the end)

Two Chrome profiles: Jason's PC J1 (sender) and Casey's phone Z1 (412 x 915, touch). Only the native folder dialog is
replaced (it cannot be clicked headless; as in Step 3's test): it returns a folder in Casey's origin-private file system.
Checks:
  1 cancel after ready: Jason sends a file, Casey sees it in the inbox, Jason taps "Cancel this send" twice on the Send
    page -> the Sent list says cancelled, Casey's inbox moves it to Earlier as "Cancelled by the sender" ("Withdrawn"),
    the relay store no longer has the folder;
  2 cancel during a download: Casey accepts a 30 MB send; while its pieces are on the way Jason cancels; the next piece
    answers 410, Casey's card says the sender cancelled it and the unfinished file is removed from the folder;
  3 quotas through the real server: 10 sends of 6 MB started at once from Jason's page against the 40 MB cap ->
    exactly 6 go through (ready), 4 are refused quota_bytes, the relay holds 6 folders;
  4 the Relay line on the Devices page: Jason sees "6 transfers live" and a last sweep (the sandbox sweeps 5 s after
    start), Casey sees no Relay line; the inbox service worker knows the 'withdrawn' push text;
  no plaintext marker or file name on the relay; no CSP errors. Last line: ALL PASS / SOME FAILED.
"""
import argparse
import hashlib
import os
import pathlib
import subprocess
import time

from playwright.sync_api import sync_playwright

NUC = "server@100.64.0.10"
PJ, PZ = 18293, 18294
SBX = "bash ~/sandbox/novashare-sweep/sweep_sbx.sh"
ap = argparse.ArgumentParser()
ap.add_argument("work")
ap.add_argument("--keep", action="store_true", help="leave the sandbox up")
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


tag = os.urandom(3).hex()
MARK = b"NOVASHARE-SWEEP-CANARY-" + os.urandom(6).hex().encode()


def make(name, size):
    p = work / name
    seed = os.urandom(16)
    out = bytearray()
    while len(out) < size:
        blk = bytearray(hashlib.shake_256(seed + len(out).to_bytes(8, "big")).digest(65536))
        blk[:len(MARK)] = MARK
        out += blk
    p.write_bytes(bytes(out[:size]))
    return p


F1 = make(f"cancel-me-{tag}.bin", 5 * 1048576 + 7)
F2 = make(f"cut-off-{tag}.bin", 30 * 1048576 + 5)
F3 = make(f"quota-{tag}.bin", 6 * 1048576)

print(nuc(f"{SBX} up").stdout.strip().splitlines()[-1], flush=True)
tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"{PJ}:127.0.0.1:{PJ}",
                           "-L", f"{PZ}:127.0.0.1:{PZ}", NUC])
time.sleep(3)

PICKER = """window.showDirectoryPicker = async () => {
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle('picked', { create: true });
};"""
LS = """async () => { try { const d = await (await navigator.storage.getDirectory()).getDirectoryHandle('picked');
  const out = []; for await (const [n] of d.entries()) out.push(n); return out; } catch (e) { return []; } }"""
# 10 sends of the same file started at once through the page's own send engine (relay.js, globalThis.NovashareSend)
PARALLEL = """async (n) => {
  const N = globalThis.NovashareSend;
  const file = document.getElementById('file').files[0];
  const t = N.S.people.filter((x) => x.p.id === 'casey' && x.t.ok).map((x) => ({ id: x.p.id, devs: x.t.devs }));
  const sts = []; for (let i = 0; i < n; i++) sts.push(await N.newSend(file, '', t));
  const res = await Promise.allSettled(sts.map((st) => N.upload(st, file)));
  return res.map((r, i) => r.status === 'fulfilled' ? 'ok:' + sts[i].id : 'err:' + (r.reason && r.reason.code)); }"""


def relay_ids():
    return set(nuc(f"{SBX} relay").stdout.split())


try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])
        console = []

        def ctx(who, port, **kw):
            c = b.new_context(color_scheme="dark", accept_downloads=True, base_url=f"http://localhost:{port}", **kw)
            p = c.new_page()
            p.on("console", lambda m: console.append(f"{who} {m.type}: {m.text}"))
            p.on("pageerror", lambda e: console.append(f"{who} pageerror: {e}"))
            return c, p

        cj, J = ctx("J1", PJ, viewport={"width": 1280, "height": 900})
        cz, Z = ctx("Z1", PZ, viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        cz.add_init_script(PICKER)

        def register(pg):
            pg.goto("/devices")
            pg.wait_for_selector('body[data-ready="1"]', timeout=60000)
            pg.click("#register")
            pg.wait_for_selector('body[data-ready="1"] #this-fp', timeout=60000)

        def revisit(pg):
            pg.goto("/devices")
            pg.wait_for_selector('body[data-ready="1"]', timeout=60000)
            for _ in range(5):
                ack = pg.locator("[data-ack]")
                if not ack.count():
                    break
                ack.first.click()
                pg.wait_for_timeout(1500)
                pg.wait_for_selector('body[data-ready="1"]', timeout=60000)

        register(J)
        register(Z)
        revisit(J)
        revisit(Z)

        def send_page():
            J.goto("/relay/send")
            J.wait_for_selector('body[data-ready="1"]', timeout=60000)

        def send(path):
            send_page()
            J.set_input_files("#file", str(path))
            J.check("#to-casey")
            J.evaluate("delete document.body.dataset.sent")
            J.click("#send")
            J.wait_for_selector('body[data-sent][data-ready="1"]', timeout=180000)   # the page CSP forbids eval
            return J.evaluate("document.body.dataset.sent")

        def inbox():
            Z.goto("/relay/inbox")
            Z.wait_for_selector('body[data-ready="1"]', timeout=60000)

        def cancel(tid):
            send_page()
            btn = J.locator(f"li[data-sent-id='{tid}'] [data-cancel]")
            btn.click()
            armed = btn.text_content()
            btn.click()
            J.wait_for_selector(f"li[data-sent-id='{tid}'][data-state=cancelled]", timeout=30000)
            return armed

        # ---- 1. cancel after ready
        t1 = send(F1)
        inbox()
        Z.locator(f"#inbox li[data-tid='{t1}'] .fname", has_text=F1.name).wait_for(timeout=60000)
        check("1: Casey's inbox shows the new send, name unlocked on her phone", True)
        check("1: the relay holds its folder before the cancel", t1 in relay_ids())
        armed = cancel(t1)
        check(f"1: Cancel needs two taps (the first arms it: '{armed}')", armed == "Yes, cancel it")
        li = J.locator(f"li[data-sent-id='{t1}']")
        check("1: the Sent list says cancelled, no cancel button left",
              "cancelled" in li.locator(".tag").text_content() and li.locator("[data-cancel]").count() == 0)
        check("1: Casey's device shows 'never got it' in the Sent list", "never got it" in li.inner_text())
        check("1: the relay store no longer has the folder", t1 not in relay_ids())
        inbox()
        old = Z.locator(f"#done li[data-tid='{t1}']")
        check("1: Casey's inbox moved it to Earlier as cancelled ('Withdrawn', 'Cancelled by the sender')",
              old.count() == 1 and old.get_attribute("data-state") == "cancelled"
              and "Withdrawn" in old.inner_text() and "Cancelled by the sender" in old.inner_text()
              and Z.locator(f"#inbox li[data-tid='{t1}']").count() == 0)

        # ---- 2. cancel while Casey is downloading
        t2 = send(F2)
        inbox()
        card = Z.locator(f"#inbox li[data-tid='{t2}']")
        card.locator(".fname", has_text=F2.name).wait_for(timeout=60000)
        held = []

        def hold(route):
            req = route.request
            if req.method == "GET" and int(req.url.rsplit("/", 1)[1]) >= 2:
                held.append(route)
            else:
                route.continue_()
        Z.route("**/relay/api/transfers/*/pieces/*", hold)
        card.locator("[data-act=accept]").click()
        for _ in range(120):
            if held:
                break
            Z.wait_for_timeout(500)
        check(f"2: Casey's download started and reached piece 3 ({len(held)} held)", bool(held))
        cancel(t2)
        for r in held:
            r.continue_()
        Z.unroute("**/relay/api/transfers/*/pieces/*")
        st = card.locator("[data-status]")
        Z.wait_for_selector(f"#inbox li[data-tid='{t2}'] [data-status][data-code]:not([data-code=''])", timeout=60000)
        check(f"2: the next piece answered 410: Casey's card says the sender cancelled ({st.get_attribute('data-code')})",
              st.get_attribute("data-code") == "gone" and "sender cancelled" in st.text_content())
        Z.wait_for_timeout(1500)
        left = Z.evaluate(LS)
        check(f"2: the unfinished file was removed from Casey's folder {left}", F2.name not in left
              and not any(n.startswith(F2.name) for n in left))
        check("2: the relay store no longer has it", t2 not in relay_ids())

        # ---- 3. 10 sends at once against the 40 MB per-person cap
        send_page()
        J.set_input_files("#file", str(F3))
        res = J.evaluate(PARALLEL, 10)
        okids = [x[3:] for x in res if x.startswith("ok:")]
        errs = sorted(x[4:] for x in res if x.startswith("err:"))
        check(f"3: 10 parallel 6 MB sends against 40 MB: exactly 6 ready, 4 quota_bytes ({len(okids)}, {errs})",
              len(okids) == 6 and errs == ["quota_bytes"] * 4)
        check("3: the relay holds exactly those 6 folders", relay_ids() == set(okids))
        inbox()
        n_in = sum(Z.locator(f"#inbox li[data-tid='{t}']").count() for t in okids)
        check(f"3: all 6 reached Casey's inbox ({n_in})", n_in == 6)

        # ---- 4. Jason's Relay line, Casey none; the worker text
        J.goto("/devices")
        J.wait_for_selector('body[data-ready="1"]', timeout=60000)
        line = J.locator("#relay-line").text_content() if J.locator("#relay-line").count() else ""
        check(f"4: Jason's Devices page: '{line}'", "6 transfers live" in line and "MB held" in line
              and "last sweep" in line and "not yet" not in line)
        Z.goto("/devices")
        Z.wait_for_selector('body[data-ready="1"]', timeout=60000)
        check("4: Casey's Devices page has no Relay line", Z.locator("#relay-line").count() == 0)
        sw = Z.evaluate("fetch('/relay/sw.js', {credentials: 'same-origin'}).then((r) => r.text())")
        check("4: the inbox service worker shows the fixed 'withdrawn' text", "A Novashare item was withdrawn" in sw
              and "d.kind === 'withdrawn'" in sw)
        check("4: the server log has the sweep runs", int(nuc(f"{SBX} log 'relay sweep expired='").stdout.strip() or 0) >= 1)

        g1 = nuc(f"{SBX} grep {MARK.decode()}", ok=(0, 1)).returncode
        g2 = nuc(f"{SBX} grep {F2.name}", ok=(0, 1)).returncode
        check("relay store, DB, audit log and server log: no plaintext marker and no file name", g1 == 1 and g2 == 1)
        bad = [x for x in console if "Content Security Policy" in x or "pageerror" in x or "TrustedScript" in x]
        check(f"no CSP / Trusted Types / page errors {bad[:3]}", not bad)
        b.close()
finally:
    tunnel.terminate()
    if not args.keep:
        print("    " + nuc(f"{SBX} down").stdout.strip().splitlines()[-1], flush=True)

print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
