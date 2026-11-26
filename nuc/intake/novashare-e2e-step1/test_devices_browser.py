"""test_devices_browser.py - Novashare E2E Step 1 in real Chrome (2026-11-24): two people, separate browser profiles.

    bash ~/sandbox/novashare-devices/devices_sbx.sh up        (on the NUC; leaves the sandbox on 127.0.0.1:18260)
    python test_devices_browser.py OUT_DIR                     (on the PC; opens its own ssh tunnel)

Needs a FRESH sandbox each run (`up` again): it expects first sight of each person. 33 checks run.

Each Playwright context is its own browser profile (own IndexedDB), standing in for one device. The sandbox has
no SSO, so each context sends the headers Caddy's forward_auth would set (X-Auth-Email, X-Auth-Level) plus the
sandbox drop key (read over ssh into memory, never printed).
Checks: browser support (X25519 / ML-KEM path), keys made and stored non-extractable, two people register, both
pages show the same safety number and it equals the server's (JS == Python), a re-key is caught as a key change
that must be acknowledged, a new device is caught, revoke removes it, the page has no CSP / Trusted Types errors,
and a tampered ML-KEM bundle is blocked by SRI. Screenshots in OUT_DIR. Last line: ALL PASS / SOME FAILED.
"""
import pathlib
import re
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

NUC = "server@100.64.0.10"
PORT = 18260
BASE = f"http://localhost:{PORT}"
out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else "devices-shots")
out.mkdir(parents=True, exist_ok=True)
fails = 0


def check(name, cond):
    global fails
    fails += not cond
    print(("PASS " if cond else "FAIL ") + name, flush=True)


KEY = subprocess.run(["ssh", NUC, "cat ~/sandbox/novashare-devices/run/key"], capture_output=True, text=True, check=True).stdout.strip()
tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"{PORT}:127.0.0.1:{PORT}", NUC])
time.sleep(3)

IDB_PROBE = """async () => {
  const db = await new Promise((res, rej) => { const r = indexedDB.open('novashare-devices', 1); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
  const d = await new Promise((res) => { const t = db.transaction('kv').objectStore('kv').get('device'); t.onsuccess = () => res(t.result); });
  db.close();
  let exported = 'refused';
  try { await crypto.subtle.exportKey('pkcs8', d.x.privateKey); exported = 'EXPORTED'; } catch (e) { exported = 'refused'; }
  const m = d.mlkem; const keys = Object.keys(d).sort().join(',');
  return { id: d.device_id, xExtractable: d.x.privateKey.extractable, exported, mode: m.mode,
           kwExtractable: m.kw ? m.kw.extractable : null, wrappedLen: m.wrapped ? m.wrapped.byteLength : null,
           nativeExtractable: m.privateKey ? m.privateKey.extractable : null, keys };
}"""
SERVER_SN = """async (p) => { const r = await fetch('/devices/api/people', {headers: {'X-Novashare': '1'}});
  return (await r.json()).people.find((x) => x.id === p).safety_number; }"""

with sync_playwright() as pw:
    b = pw.chromium.launch(channel="chrome", headless=True, args=["--disable-gpu"])
    console = []

    def ctx(person):
        c = b.new_context(viewport={"width": 390, "height": 844}, color_scheme="dark", extra_http_headers={
            "X-Drop-Key": KEY, "X-Auth-Email": f"{person}@example.invalid", "X-Auth-Level": "full"})
        pg = c.new_page()
        pg.on("console", lambda m: console.append(f"{person}: {m.type}: {m.text}"))
        pg.on("pageerror", lambda e: console.append(f"{person}: pageerror: {e}"))
        return c, pg

    def load(pg):
        pg.goto(BASE + "/devices")
        pg.wait_for_selector('body[data-ready="1"]', timeout=60000)

    def act(pg, sel):
        pg.click(sel)
        pg.wait_for_selector('body[data-ready="1"]', timeout=60000)

    def alerts(pg):
        return pg.eval_on_selector_all("#alerts [data-kind]", "els => els.map(e => e.dataset.kind + ':' + (e.dataset.person || ''))")

    ca, pa = ctx("jason")
    load(pa)
    ver = b.version
    sup = pa.evaluate("() => [document.body.dataset.x25519, document.body.dataset.mlkem, document.getElementById('support').textContent]")
    print(f"INFO Chrome {ver}: x25519={sup[0]} mlkem={sup[1]} | {sup[2]}")
    check("X25519 available in WebCrypto", sup[0] == "webcrypto")
    check("an ML-KEM-768 path is available (native or vendored)", sup[1] in ("native", "noble"))

    pa.fill("#label", "Jason laptop")
    act(pa, "#register")
    check("jason's device registered", pa.is_visible("#this-fp"))
    ia = pa.evaluate(IDB_PROBE)
    check("X25519 private key stored non-extractable, export refused", ia["xExtractable"] is False and ia["exported"] == "refused")
    if ia["mode"] == "noble":
        check("ML-KEM secret stored only AES-KW-wrapped (2408 bytes) under a non-extractable key",
              ia["kwExtractable"] is False and ia["wrappedLen"] == 2408 and "secretKey" not in ia["keys"])
    else:
        check("native ML-KEM private key non-extractable", ia["nativeExtractable"] is False)

    cb, pb = ctx("casey")
    load(pb)
    pb.fill("#label", "Casey phone")
    act(pb, "#register")
    check("casey's device registered", pb.is_visible("#this-fp"))

    load(pa)
    load(pb)
    sa, sb = pa.text_content("#safety-casey"), pb.text_content("#safety-jason")
    print(f"INFO safety numbers: jason sees {sa}, casey sees {sb}")
    check("safety number shown as 12 digits in 3 groups", bool(re.fullmatch(r"\d{4} \d{4} \d{4}", sa or "")))
    check("both people see the same safety number", sa == sb)
    check("browser (JS) number == server (Python) number", sa == pa.evaluate(SERVER_SN, "casey") == pb.evaluate(SERVER_SN, "jason"))
    check("first sight: no alerts (trust on first use)", alerts(pa) == [] and alerts(pb) == [])
    pa.screenshot(path=str(out / "jason-devices.png"), full_page=True)

    # --- key change on an existing device id
    act(pb, "#rekey")
    load(pa)
    al = alerts(pa)
    check(f"casey re-keyed: jason's page raises a key-change alert ({al})", "changed:casey" in al)
    sa2 = pa.text_content("#safety-casey")
    check("safety number changed after the re-key", sa2 != sa)
    check("casey's own page sees no alert for its own re-key", alerts(pb) == [])
    load(pb)
    check("both sides agree on the new number", pb.text_content("#safety-jason") == sa2)
    pa.screenshot(path=str(out / "jason-key-changed.png"), full_page=True)
    load(pa)
    check("the alert stays until acknowledged (after a reload)", "changed:casey" in alerts(pa))
    act(pa, '[data-ack="casey"]')
    check("acknowledged: alert gone", alerts(pa) == [])
    load(pa)
    check("acknowledged: still gone after a reload", alerts(pa) == [])

    # --- a new device for a contact
    cb2, pb2 = ctx("casey")
    load(pb2)
    pb2.fill("#label", "Casey tablet")
    act(pb2, "#register")
    b2id = pb2.evaluate(IDB_PROBE)["id"]
    load(pa)
    check("casey added a device: jason's page raises a new-device alert", "new:casey" in alerts(pa))
    load(pb)
    check("casey's first device also sees the new device of its own person", "new:casey" in alerts(pb))
    act(pa, '[data-ack="casey"]')
    act(pb, '[data-ack="casey"]')
    check("new-device alerts acknowledged", alerts(pa) == [] and alerts(pb) == [])

    # --- revoke (two taps), from casey's other device
    pb.click(f'[data-revoke="{b2id}"]')
    act(pb, f'[data-revoke="{b2id}"]')
    load(pa)
    tag = pa.text_content('#people [data-person="casey"] .tag')
    check(f"revoked device left jason's view of casey ({tag})", tag == "1 device" and alerts(pa) == [])
    load(pb2)
    check("the revoked browser says it was removed", "was removed" in (pb2.text_content("#this") or ""))

    # --- page hygiene and SRI
    bad = [c for c in console if re.search(r"Content Security Policy|Trusted Type|pageerror|integrity", c, re.I)]
    check(f"no CSP / Trusted Types / script errors on the pages ({len(bad)})", not bad)
    if bad:
        print("\n".join("    " + x[:200] for x in bad))
    # --- the vendored path: a browser WITHOUT native ML-KEM (native hidden by an init script)
    NO_NATIVE = """(() => { const g = crypto.subtle.generateKey.bind(crypto.subtle);
      crypto.subtle.generateKey = (a, ...r) => ((a && (a.name || a) + '').toUpperCase().startsWith('ML-KEM')
        ? Promise.reject(new DOMException('hidden by test', 'NotSupportedError')) : g(a, ...r)); })()"""
    cn, pn = ctx("jason")
    pn.add_init_script(NO_NATIVE)
    load(pn)
    check("native ML-KEM hidden: the page picks the vendored bundle", pn.evaluate("() => document.body.dataset.mlkem") == "noble")
    pn.fill("#label", "Jason old browser")
    act(pn, "#register")
    check("vendored path: device registered (its encapsulate/unwrap/decapsulate self-test passed)", pn.is_visible("#this-fp"))
    inn = pn.evaluate(IDB_PROBE)
    check("vendored path: ML-KEM secret stored only AES-KW-wrapped (2408 bytes), wrapping key non-extractable",
          inn["mode"] == "noble" and inn["kwExtractable"] is False and inn["wrappedLen"] == 2408 and "secretKey" not in inn["keys"])
    check("vendored path: X25519 private key non-extractable", inn["xExtractable"] is False and inn["exported"] == "refused")
    load(pa)
    check("jason's own other device sees the new device of its person", "new:jason" in alerts(pa))
    load(pb)
    check("casey sees jason's new device", "new:jason" in alerts(pb))
    check("and the safety number agrees again on both sides",
          pb.text_content("#safety-jason") == pa.text_content("#safety-casey") == pa.evaluate(SERVER_SN, "casey"))

    cc, pc = ctx("jason")
    pc.add_init_script(NO_NATIVE)
    sup = [None, "noble"]

    def tamper(route):                   # one extra byte: the file still runs, but its hash no longer matches
        resp = route.fetch()
        route.fulfill(response=resp, body=resp.body() + b";")
    pc.route("**/devices/static/noble-pq-*.js", tamper)
    console.clear()
    load(pc)
    blocked = pc.evaluate("() => typeof globalThis.NovashareMLKEM === 'undefined'")
    check("tampered ML-KEM bundle is blocked by SRI (NovashareMLKEM undefined)", blocked)
    check("the browser reported the integrity failure", any("integrity" in c.lower() for c in console))
    if sup[1] == "noble":
        check("with the bundle blocked, setting up a device is switched off", "did not load" in (pc.text_content("#this") or ""))
    b.close()

tunnel.terminate()
print("ALL PASS" if not fails else f"SOME FAILED ({fails})")
