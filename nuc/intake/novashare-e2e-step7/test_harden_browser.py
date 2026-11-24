"""test_harden_browser.py - Novashare step 7 in real Chrome, two people (2026-11-24): strict CSP on every page,
device keys, verified people, send/receive with the content commitment, re-key proof, the Abyss album keyring.

    python test_harden_browser.py WORK_DIR      (on the PC; runs harden_sbx.sh up on the NUC first, opens its own
                                                 ssh tunnel, and harden_sbx.sh down at the end unless --keep)

Two Chrome profiles: Jason's PC J1 and Casey's phone Z1 (412 x 915, touch). Only the native folder dialog is replaced
(headless cannot click it): it returns a folder in Casey's origin-private file system. Checks:
  1 every drop page (/, /sending, /routes, /devices, /relay/send, /relay/inbox, /relay/share) answers with a strict
    CSP (script-src 'self', no unsafe-inline, object-src/base-uri/frame-ancestors none) and causes NO CSP violation;
  2 the stored device record holds no AES-KW/extractable carrier: ML-KEM is 'native' or 'noble2' (sealed);
  3 Casey shows "not verified" on Jason's Devices page until he marks her; with "Only send to people I have verified"
    on, Casey's Send page refuses Jason (not verified there), and accepts him after she marks him;
  4 Jason sends a 9 MB file to Casey; Casey accepts; the saved file's SHA-256 equals the original (v2 wraps + root);
  5 two-tap Cancel announces its armed state in the status line;
  6 Abyss album keyring via drop: the stand-in Abyss origin opens /keyring as a popup, gets a key wrapped to Jason's
    device and opened again (same 32 bytes);
  7 re-key: "Make new keys" works in the browser (challenge answered with the old key), Casey gets a key alert, the
    album key wrapped to the OLD key no longer opens (an album locked before needs "Lock again" / its recovery code),
    and a new wrap opens.
Last line: ALL PASS / SOME FAILED.
"""
import argparse
import hashlib
import os
import pathlib
import subprocess
import time

from playwright.sync_api import sync_playwright

NUC = "server@100.64.0.10"
PJ, PZ, PA = 18293, 18294, 18296
SBX = "bash ~/sandbox/novashare-harden/harden_sbx.sh"
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
F1 = work / f"to-casey-{tag}.bin"
F1.write_bytes(os.urandom(9 * 1048576 + 123))
F2 = work / f"cancel-{tag}.bin"
F2.write_bytes(os.urandom(70000))
SHA1 = hashlib.sha256(F1.read_bytes()).hexdigest()

print(nuc(f"{SBX} up").stdout.strip().splitlines()[-1], flush=True)
tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"{PJ}:127.0.0.1:{PJ}",
                           "-L", f"{PZ}:127.0.0.1:{PZ}", "-L", f"{PA}:127.0.0.1:{PA}", NUC])
time.sleep(3)

PICKER = """window.showDirectoryPicker = async () => {
  const root = await navigator.storage.getDirectory();
  return root.getDirectoryHandle('picked', { create: true });
};"""
CSPWATCH = """document.addEventListener('securitypolicyviolation', (e) => {
  try { const k = 'csp-violations'; const v = JSON.parse(sessionStorage.getItem(k) || '[]');
    v.push(location.pathname + ' ' + e.violatedDirective + ' ' + e.blockedURI); sessionStorage.setItem(k, JSON.stringify(v)); } catch (x) {}
});"""
FILESHA = """async (name) => { const d = await (await navigator.storage.getDirectory()).getDirectoryHandle('picked');
  const f = await (await d.getFileHandle(name)).getFile();
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', await f.arrayBuffer()));
  return Array.from(h, (b) => b.toString(16).padStart(2, '0')).join(''); }"""
DEVREC = """() => new Promise((ok) => { const r = indexedDB.open('novashare-devices', 1);
  r.onsuccess = () => { const q = r.result.transaction('kv').objectStore('kv').get('device');
    q.onsuccess = () => { const d = q.result || {}; const m = d.mlkem || {};
      ok({ mode: m.mode || null, kw: 'kw' in m, wrapped: 'wrapped' in m,
           kekExtractable: m.kek ? m.kek.extractable : null, xExtractable: d.x ? d.x.privateKey.extractable : null }); }; }; })"""
need = ("script-src 'self'", "object-src 'none'", "base-uri 'none'", "frame-ancestors 'none'")


def strict(h):
    c = h.get("content-security-policy", "")
    return all(n in c for n in need) and "unsafe" not in c and h.get("cross-origin-resource-policy") == "same-origin"


try:
    with sync_playwright() as pw:
        b = pw.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])
        console = []

        def ctx(who, port, **kw):
            c = b.new_context(color_scheme="dark", accept_downloads=True, base_url=f"http://localhost:{port}", **kw)
            c.add_init_script(CSPWATCH)
            p = c.new_page()
            p.on("console", lambda m: console.append(f"{who} {m.type}: {m.text}"))
            p.on("pageerror", lambda e: console.append(f"{who} pageerror: {e}"))
            return c, p

        cj, J = ctx("J1", PJ, viewport={"width": 1280, "height": 900})
        cz, Z = ctx("Z1", PZ, viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        cz.add_init_script(PICKER)

        def ready(pg, path):
            resp = pg.goto(path)
            pg.wait_for_selector('body[data-ready="1"]', timeout=60000)
            return resp

        def register(pg):
            ready(pg, "/devices")
            pg.click("#register")
            pg.wait_for_selector('body[data-ready="1"] #this-fp', timeout=60000)

        def ack_all(pg):
            ready(pg, "/devices")
            for _ in range(5):
                ack = pg.locator("[data-ack]")
                if not ack.count():
                    break
                ack.first.click()
                pg.wait_for_timeout(1500)
                pg.wait_for_selector('body[data-ready="1"]', timeout=60000)

        # ---- 1. headers and CSP on every page (library pages included)
        bad = []
        for who, pg, paths in (("J1", J, ["/", "/sending", "/routes", "/devices", "/relay/send", "/relay/inbox", "/relay/share"]),
                               ("Z1", Z, ["/", "/sending", "/devices"])):
            for path in paths:
                r = pg.goto(path)
                pg.wait_for_timeout(1200)
                if not r or r.status != 200 or not strict(r.headers):
                    bad.append(f"{who} {path} {r.status if r else '-'}")
        check("1: every drop page answers 200 with the strict CSP + CORP header set" + (f" ({bad})" if bad else ""), not bad)

        register(J)
        register(Z)
        ack_all(J)
        ack_all(Z)

        # ---- 2. what the device record holds
        for who, pg in (("J1", J), ("Z1", Z)):
            rec = pg.evaluate(DEVREC)
            check(f"2: {who} device record: ML-KEM {rec['mode']}, no AES-KW carrier, sealing key and X25519 not extractable",
                  rec["mode"] in ("native", "noble2") and not rec["kw"] and not rec["wrapped"]
                  and rec["kekExtractable"] in (False, None) and rec["xExtractable"] is False)

        # 2b: force the vendored ML-KEM path (this Chrome has native ML-KEM) in a second profile for Casey
        cn, N = ctx("Z2", PZ, viewport={"width": 412, "height": 915})
        cn.add_init_script("(() => { const g = crypto.subtle.generateKey.bind(crypto.subtle);"
                           " crypto.subtle.generateKey = (a, ...r) => (a && (a.name || a) === 'ML-KEM-768')"
                           " ? Promise.reject(new Error('no native ML-KEM')) : g(a, ...r); })();")
        register(N)
        rec = N.evaluate(DEVREC)
        check(f"2b: a vendored-ML-KEM device registers (self-test opens the sealed secret): mode {rec['mode']}, "
              "sealing key not extractable, no AES-KW carrier",
              rec["mode"] == "noble2" and rec["kekExtractable"] is False and not rec["kw"] and not rec["wrapped"])
        ack_all(J)
        ack_all(Z)

        # ---- 3. verified people
        ready(J, "/devices")
        tagz = J.locator("li[data-person=casey] [data-verified]")
        check("3: Jason's Devices page shows Casey as 'not verified' at first (trust on first use)", tagz.get_attribute("data-verified") == "0")
        J.click("[data-verify=casey]")
        J.wait_for_timeout(1500)
        J.wait_for_selector('body[data-ready="1"]', timeout=60000)
        check("3: after 'The numbers match', Casey shows 'verified'",
              J.locator("li[data-person=casey] [data-verified]").get_attribute("data-verified") == "1")
        ready(Z, "/devices")
        Z.check("#strict")
        Z.wait_for_timeout(800)
        ready(Z, "/relay/send")
        jbox = Z.locator("#to-jason")
        check("3: Casey with 'Only send to people I have verified' on: Jason cannot be picked (not verified on her phone)",
              jbox.is_disabled() and "Not verified yet" in Z.locator("#people").inner_text())
        ready(Z, "/devices")
        Z.click("[data-verify=jason]")
        Z.wait_for_timeout(1500)
        ready(Z, "/relay/send")
        check("3: after Casey marks Jason verified, he can be picked", not Z.locator("#to-jason").is_disabled())
        ready(Z, "/devices")
        Z.uncheck("#strict")
        Z.wait_for_timeout(800)

        # ---- 4. send and receive
        def send(path):
            ready(J, "/relay/send")
            J.set_input_files("#file", str(path))
            J.check("#to-casey")
            J.evaluate("delete document.body.dataset.sent")
            J.click("#send")
            J.wait_for_selector('body[data-sent][data-ready="1"]', timeout=180000)
            return J.evaluate("document.body.dataset.sent")

        t1 = send(F1)
        ready(Z, "/relay/inbox")
        card = Z.locator(f"#inbox li[data-tid='{t1}']")
        card.locator(".fname", has_text=F1.name).wait_for(timeout=60000)
        check("4: Casey's inbox unlocks the name (v2 wrap with the manifest hash opens)", True)
        card.locator("[data-act=accept]").click()
        Z.wait_for_selector(f"#inbox li[data-tid='{t1}'] [data-saved='1']", timeout=180000)
        got = Z.evaluate(FILESHA, F1.name)
        check("4: the saved file has the sender's SHA-256 (pieces match the committed root)", got == SHA1)
        check("4: the inbox count line says how many are waiting (polite live region)",
              Z.locator("#count").get_attribute("aria-live") == "polite" and Z.locator("#inbox").get_attribute("aria-live") is None)

        # ---- 5. two-tap cancel is announced
        t2 = send(F2)
        ready(J, "/relay/send")
        btn = J.locator(f"li[data-sent-id='{t2}'] [data-cancel]")
        btn.click()
        msg = btn.locator("xpath=following-sibling::p[@role='status']").text_content()
        check(f"5: the first tap on Cancel is announced ('{msg}')", "within 6 seconds" in (msg or ""))
        btn.click()
        J.wait_for_selector(f"li[data-sent-id='{t2}'][data-state=cancelled]", timeout=30000)

        # ---- 6. the Abyss album keyring through drop
        A = cj.new_page()
        A.goto(f"http://127.0.0.1:{PA}/")
        aid = "abyss-album/" + "K" * 22

        key = list(os.urandom(32))
        with cj.expect_page() as pop:
            A.evaluate("""([d, id, k]) => { window._w = window.keyring(d, {type: 'keyring-wrap', id, key: new Uint8Array(k)})
              .then((r) => (window._wr = r)).catch((e) => (window._we = String(e))); }""", [f"http://localhost:{PJ}", aid, key])
        p = pop.value
        p.wait_for_selector("#ask:not([hidden])", timeout=60000)
        p.click("#allow")
        A.wait_for_function("window._wr || window._we", timeout=60000)
        if not p.is_closed():
            p.wait_for_event("close", timeout=10000)        # the keyring closes itself after "Done"
        wrapped = A.evaluate("window._wr && window._wr.wraps")
        check("6: keyring popup (COOP unsafe-none, opener kept) wraps an album key to Jason's device", bool(wrapped))

        def unwrap(wraps):
            with cj.expect_page() as pop:
                A.evaluate("""([d, id, w]) => { window._u = null; window._ue = null;
                  window.keyring(d, {type: 'keyring-unwrap', jobs: [{id, wraps: w}]})
                  .then((r) => (window._u = r)).catch((e) => (window._ue = String(e))); }""", [f"http://localhost:{PJ}", aid, wraps])
            q = pop.value
            q.wait_for_selector("#ask:not([hidden])", timeout=60000)
            q.click("#allow")
            A.wait_for_function("window._u || window._ue", timeout=60000)
            if not q.is_closed():
                q.wait_for_event("close", timeout=10000)
            k = A.evaluate("window._u && window._u.keys[0].key && Array.from(window._u.keys[0].key)")
            return k

        check("6: the same popup opens it again: the same 32 bytes", unwrap(wrapped) == key)

        # ---- 7. re-key with the challenge proof
        ready(J, "/devices")
        fp0 = J.locator("#this-fp").text_content()
        J.click("#rekey")
        J.wait_for_timeout(2000)
        J.wait_for_selector('body[data-ready="1"]', timeout=60000)
        fp1 = J.locator("#this-fp").text_content()
        errs = J.locator("#alerts [data-kind=error]").count()
        check("7: 'Make new keys' in the browser: the server accepted the challenge proof (new fingerprint, no error)",
              fp0 != fp1 and errs == 0)
        ready(Z, "/devices")
        check("7: Casey gets a key alert for Jason", Z.locator("[data-kind=changed][data-person=jason]").count() == 1)
        check("7: an album key wrapped to Jason's OLD key does not open any more (needs 'Lock again' / recovery code)",
              unwrap(wrapped) is None)

        csp = []
        for pg in (J, Z):
            csp += pg.evaluate("JSON.parse(sessionStorage.getItem('csp-violations') || '[]')")
        bad_console = [c for c in console if "Content Security Policy" in c or "Trusted Type" in c or "pageerror" in c]
        check(f"no CSP / Trusted Types violation and no page error on any page ({len(csp)} / {len(bad_console)})",
              not csp and not bad_console)
        for c in (csp + bad_console)[:8]:
            print("    " + c)
        b.close()
finally:
    tunnel.terminate()
    if not args.keep:
        nuc(f"{SBX} down", ok=(0, 1))

print("ALL PASS" if not fails else "SOME FAILED")
