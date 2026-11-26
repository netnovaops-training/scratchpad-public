"""devices_js.py - the Novashare Devices page script (E2E Step 1, 2026-11-24), served by devices.py.

Kept in a .py (JS below) because SWAP-PATCH.sh installs *.py files only. The page loads it as a module script
with an SRI hash that devices.py computes from these exact bytes. Its fingerprint and safety-number functions
are twins of the Python ones in devices.py; test_devices_browser.py checks both sides give the same numbers.

Browser support it relies on (feature-detected at run time, never assumed):
  X25519 in WebCrypto: Chrome/Edge 133+, Firefox 130+, Safari 17+. Without it, registration is switched off with
    a plain message (no non-native fallback: the point is a private key script can use but not read).
  ML-KEM-768: native WebCrypto ("ML-KEM-768", WICG modern-algorithms draft) when generateKey + export
    "raw-public" + an encapsulate/decapsulate self-test all work; otherwise the vendored @noble/post-quantum
    bundle (globalThis.NovashareMLKEM). Its 2400-byte secret is wrapped with a non-extractable AES-KW key
    (wrapKey of a throw-away HMAC "carrier" key, since AES-KW only wraps CryptoKeys) and only the wrapped bytes
    are stored. A self-test (encapsulate to the new key, unwrap, decapsulate, compare) runs before registering.
  IndexedDB for the CryptoKeys (structured clone keeps them non-extractable), TextEncoder, SHA-256/512 digests.
DOM writes use textContent / createElement only (the page CSP requires Trusted Types; no HTML strings exist).
"""

JS = r"""// devices.js - Novashare Devices page (devices_js.py, E2E Step 1, 2026-11-24). No outside libraries.
const API = '/devices/api';
const enc = new TextEncoder();
const S = { local: null, me: null, support: null, armed: null };
const $ = (id) => document.getElementById(id);

// ---------- small helpers
function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v;
    else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
const b64u = {
  enc(u8) { let s = ''; for (const b of u8) s += String.fromCharCode(b); return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); },
  dec(s) { const t = atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4)); return Uint8Array.from(t, (c) => c.charCodeAt(0)); },
};
const hex = (u8) => Array.from(u8, (b) => b.toString(16).padStart(2, '0')).join('');
const unhex = (h) => Uint8Array.from(h.match(/../g) || [], (x) => parseInt(x, 16));
function cat(...parts) { let n = 0; for (const p of parts) n += p.length; const o = new Uint8Array(n); let i = 0; for (const p of parts) { o.set(p, i); i += p.length; } return o; }
function lp(u8) { const o = new Uint8Array(4 + u8.length); new DataView(o.buffer).setUint32(0, u8.length); o.set(u8, 4); return o; }
async function digest(alg, u8) { return new Uint8Array(await crypto.subtle.digest(alg, u8)); }
function same(a, b) { if (a.length !== b.length) return false; let d = 0; for (let i = 0; i < a.length; i++) d |= a[i] ^ b[i]; return d === 0; }

// ---------- IndexedDB key-value store (one object store "kv")
function idb() {
  return new Promise((res, rej) => {
    const r = indexedDB.open('novashare-devices', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('kv');
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
}
async function kv(mode, fn) {
  const db = await idb();
  return new Promise((res, rej) => {
    const tx = db.transaction('kv', mode); const st = tx.objectStore('kv'); let out;
    const rq = fn(st); if (rq) rq.onsuccess = () => { out = rq.result; };
    tx.oncomplete = () => { db.close(); res(out); }; tx.onerror = () => { db.close(); rej(tx.error); };
  });
}
const kvGet = (k) => kv('readonly', (s) => s.get(k));
const kvSet = (k, v) => kv('readwrite', (s) => s.put(v, k));
const kvDel = (k) => kv('readwrite', (s) => s.delete(k));

// ---------- fingerprints and safety numbers (twins of devices.py)
async function deviceFp(person, id, x, m) {
  return hex(await digest('SHA-256', cat(enc.encode('novashare/device/v1'), lp(enc.encode(person)), lp(enc.encode(id)), lp(x), lp(m))));
}
async function personFp(person, devices) {
  if (!devices.length) return null;
  const parts = [enc.encode('novashare/person/v1'), lp(enc.encode(person))];
  for (const d of [...devices].sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))) parts.push(lp(unhex(d.fingerprint)));
  return hex(await digest('SHA-256', cat(...parts)));
}
const HALF = new Map();
async function half(person, pfp) {
  const key = person + ':' + pfp; if (HALF.has(key)) return HALF.get(key);
  const raw = unhex(pfp); let h = cat(enc.encode('novashare/safety/v1'), lp(enc.encode(person)), raw);
  for (let i = 0; i < 5200; i++) h = await digest('SHA-512', cat(h, raw));
  let n = 0; for (let i = 0; i < 5; i++) n = n * 256 + h[i];
  const d = String(n % 1000000).padStart(6, '0'); HALF.set(key, d); return d;
}
async function safetyNumber(a, afp, b, bfp) {
  if (!afp || !bfp) return null;
  const [p1, p2] = [[a, afp], [b, bfp]].sort((x, y) => (x[0] < y[0] ? -1 : 1));
  const d = (await half(p1[0], p1[1])) + (await half(p2[0], p2[1]));
  return d.slice(0, 4) + ' ' + d.slice(4, 8) + ' ' + d.slice(8);
}

// ---------- feature detection and key generation
async function detect() {
  const out = { x25519: false, mlkem: 'none', native: null };
  try {
    const k = await crypto.subtle.generateKey({ name: 'X25519' }, false, ['deriveBits']);
    out.x25519 = !!(k && k.privateKey && k.privateKey.extractable === false);
  } catch (e) { out.x25519 = false; }
  out.native = await nativeMlkem();
  out.mlkem = out.native ? 'native' : (globalThis.NovashareMLKEM ? 'noble' : 'none');
  return out;
}
async function nativeMlkem() {
  try {
    const alg = { name: 'ML-KEM-768' };
    const kp = await crypto.subtle.generateKey(alg, false, ['encapsulateBits', 'decapsulateBits']);
    const pub = new Uint8Array(await crypto.subtle.exportKey('raw-public', kp.publicKey));
    if (pub.length !== 1184 || kp.privateKey.extractable !== false) return null;
    const r = await crypto.subtle.encapsulateBits(alg, kp.publicKey);
    const ss = new Uint8Array(await crypto.subtle.decapsulateBits(alg, kp.privateKey, r.ciphertext));
    return same(ss, new Uint8Array(r.sharedKey)) ? { kp, pub } : null;
  } catch (e) { return null; }
}
async function unwrapSecret(m) {         // the noble ML-KEM secret, only for the moment it is needed
  const carrier = await crypto.subtle.unwrapKey('raw', m.wrapped, m.kw, 'AES-KW', { name: 'HMAC', hash: 'SHA-256' }, true, ['sign']);
  return new Uint8Array(await crypto.subtle.exportKey('raw', carrier));
}
async function makeKeys() {
  const x = await crypto.subtle.generateKey({ name: 'X25519' }, false, ['deriveBits']);
  if (x.privateKey.extractable !== false) throw new Error('X25519 private key came out extractable');
  const xpub = new Uint8Array(await crypto.subtle.exportKey('raw', x.publicKey));
  const nat = await nativeMlkem();
  if (nat) return { x, xpub, mlkem: { mode: 'native', privateKey: nat.kp.privateKey }, mpub: nat.pub };
  const M = globalThis.NovashareMLKEM;
  if (!M) throw new Error('The ML-KEM code did not load (integrity check failed or blocked).');
  const { publicKey, secretKey } = M.keygen();
  const kw = await crypto.subtle.generateKey({ name: 'AES-KW', length: 256 }, false, ['wrapKey', 'unwrapKey']);
  const carrier = await crypto.subtle.importKey('raw', secretKey, { name: 'HMAC', hash: 'SHA-256' }, true, ['sign']);
  const wrapped = await crypto.subtle.wrapKey('raw', carrier, kw, 'AES-KW');
  secretKey.fill(0);
  const mlkem = { mode: 'noble', kw, wrapped };
  const t = M.encapsulate(publicKey);       // self-test: the stored secret really opens this public key
  const sk = await unwrapSecret(mlkem); const ss = M.decapsulate(t.cipherText, sk); sk.fill(0);
  if (!same(ss, t.sharedSecret)) throw new Error('ML-KEM self-test failed');
  return { x, xpub, mlkem, mpub: publicKey };
}
function platformGuess() {
  const ua = navigator.userAgent || '';
  return (navigator.userAgentData && navigator.userAgentData.platform) || (/Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS'
    : /Windows/.test(ua) ? 'Windows' : /Mac/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : '');
}

// ---------- API
async function api(path, opts = {}) {
  const headers = { 'X-Novashare': '1' };
  if (opts.body) headers['Content-Type'] = 'application/json';
  if (S.local) headers['X-Device-Id'] = S.local.device_id;
  const r = await fetch(API + path, { method: opts.method || 'GET', headers, credentials: 'same-origin', cache: 'no-store',
    body: opts.body ? JSON.stringify(opts.body) : undefined });
  let data = null; try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) { const e = new Error((data && data.error) || ('The server answered ' + r.status + '.')); e.code = data && data.code; throw e; }
  return data;
}

// ---------- pins (trust on first use), per signed-in person
const pinKey = () => 'pins:' + S.me;
async function getPins() { return (await kvGet(pinKey())) || { persons: {} }; }
async function pinPerson(pid, devices) {
  const p = await getPins(); p.persons[pid] = { devices: Object.fromEntries(devices.map((d) => [d.id, d.fingerprint])), at: Date.now() };
  await kvSet(pinKey(), p);
}

// ---------- actions
async function register(label, deviceId) {
  const k = await makeKeys();
  const body = { label, platform: platformGuess(), x25519_pub: b64u.enc(k.xpub), mlkem_pub: b64u.enc(k.mpub) };
  if (deviceId) body.device_id = deviceId;
  const r = await api('/register', { method: 'POST', body });
  const mine = await deviceFp(S.me, r.device_id, k.xpub, k.mpub);
  if (mine !== r.fingerprint) throw new Error('The server returned a fingerprint that does not match these keys. Not saved.');
  S.local = { device_id: r.device_id, person: S.me, label, fingerprint: mine, x: k.x, xpub: k.xpub, mlkem: k.mlkem, mpub: k.mpub, at: Date.now() };
  await kvSet('device', S.local);
  const p = await getPins(); const mp = p.persons[S.me] || { devices: {} };   // my own new key is expected: no alert
  mp.devices[r.device_id] = mine; p.persons[S.me] = mp; await kvSet(pinKey(), p);
  return r;
}

// ---------- rendering
function busy(on) { document.body.dataset.ready = on ? '0' : '1'; }
function fail(e) { $('alerts').append(el('div', { class: 'card alert', 'data-kind': 'error' }, el('strong', { text: 'Something went wrong. ' }), e.message || String(e))); }

function renderThis(mine) {
  const box = $('this'); box.replaceChildren();
  if (!S.support.x25519) {
    box.append(el('p', { class: 'bad', text: 'This browser cannot make X25519 keys in WebCrypto, so it cannot be a Novashare device. Use Chrome or Edge 133+, Firefox 130+ or Safari 17+.' }));
    return;
  }
  if (S.support.mlkem === 'none') {
    box.append(el('p', { class: 'bad', text: 'The ML-KEM code did not load (its integrity check failed or it was blocked), so this device cannot be set up.' }));
    return;
  }
  const row = S.local && mine.devices.find((d) => d.id === S.local.device_id);
  if (S.local && row && row.revoked == null) {
    box.append(el('p', {}, 'This device is ', el('strong', { text: S.local.label }), '.'),
      el('p', { class: 'mono dim', id: 'this-fp', text: 'Fingerprint ' + S.local.fingerprint }),
      el('div', { class: 'row' }, el('button', { class: 'quiet', id: 'rekey', type: 'button', onclick: () => run(async () => { await register(S.local.label, S.local.device_id); }) }, 'Make new keys for this device')));
    return;
  }
  if (S.local) {
    box.append(el('p', { class: 'bad', text: row ? 'This device was removed from your list.' : 'This device is not on your list any more.' }));
    box.append(el('button', { type: 'button', onclick: () => run(async () => { await kvDel('device'); S.local = null; }) }, 'Set it up again'));
    return;
  }
  const input = el('input', { id: 'label', name: 'label', maxlength: '40', autocomplete: 'off', value: platformGuess() ? platformGuess() + ' browser' : 'My device' });
  box.append(el('p', { text: 'This browser is not one of your devices yet.' }),
    el('label', { for: 'label', text: 'Name it so you know which one it is' }), input,
    el('div', { class: 'row' }, el('button', { id: 'register', type: 'button', onclick: () => run(async () => { await register(input.value.trim() || 'My device'); }) }, 'Make keys and add this device')));
}

function renderMine(mine) {
  const ul = $('mine'); ul.replaceChildren();
  if (!mine.devices.length) { ul.append(el('li', { class: 'dim', text: 'None yet.' })); return; }
  for (const d of mine.devices) {
    const isThis = S.local && d.id === S.local.device_id;
    const li = el('li', { class: 'card', 'data-device': d.id },
      el('div', { class: 'row' }, el('strong', { text: d.label }),
        el('span', { class: 'tag', text: d.revoked != null ? 'removed' : isThis ? 'this device' : (d.platform || 'device') })),
      el('p', { class: 'dim', text: 'Added ' + new Date(d.created * 1000).toLocaleDateString() + ', last seen ' + new Date(d.last_seen * 1000).toLocaleString() }),
      el('p', { class: 'mono dim', text: d.fingerprint }));
    if (d.revoked == null) {
      const b = el('button', { class: 'quiet', type: 'button', 'data-revoke': d.id }, 'Remove');
      b.addEventListener('click', () => {
        if (S.armed !== d.id) { S.armed = d.id; b.textContent = 'Tap again to remove'; b.className = 'warn'; setTimeout(() => { if (S.armed === d.id) { S.armed = null; b.textContent = 'Remove'; b.className = 'quiet'; } }, 6000); return; }
        S.armed = null;
        run(async () => { await api('/mine/' + encodeURIComponent(d.id) + '/revoke', { method: 'POST' }); if (isThis) { await kvDel('device'); S.local = null; } });
      });
      li.append(b);
    }
    ul.append(li);
  }
}

async function check(people) {
  // Recompute every fingerprint from the raw keys; compare with what this browser pinned before.
  const pins = await getPins(); const alerts = []; const shown = [];
  let myFp = null; const verified = {};
  for (const p of people) {
    const good = []; const bad = [];
    for (const d of p.devices) {
      const fp = await deviceFp(p.id, d.id, b64u.dec(d.x25519_pub), b64u.dec(d.mlkem_pub));
      (fp === d.fingerprint ? good : bad).push(d);
    }
    verified[p.id] = { good, bad, pfp: await personFp(p.id, good) };
    if (p.me) myFp = verified[p.id].pfp;
  }
  for (const p of people) {
    const v = verified[p.id]; const pin = pins.persons[p.id];
    const changed = []; const added = [];
    if (pin) for (const d of v.good) { if (!(d.id in pin.devices)) added.push(d); else if (pin.devices[d.id] !== d.fingerprint) changed.push(d); }
    if (v.bad.length) alerts.push({ kind: 'mismatch', p, list: v.bad });
    if (changed.length) alerts.push({ kind: 'changed', p, list: changed });
    if (added.length) alerts.push({ kind: 'new', p, list: added });
    const firstSeen = !pin && v.good.length > 0;
    if (firstSeen || (pin && !changed.length && !added.length)) await pinPerson(p.id, v.good);   // TOFU, or drop removed ones
    const sn = p.me ? null : await safetyNumber(S.me, myFp, p.id, v.pfp);
    shown.push({ p, v, sn, firstSeen, pending: changed.length + added.length > 0 });
  }
  return { alerts, shown };
}

function renderAlerts(alerts) {
  const box = $('alerts'); box.replaceChildren(); $('h-alerts').hidden = !alerts.length;
  for (const a of alerts) {
    const who = a.p.me ? 'your own devices' : a.p.name;
    const head = a.kind === 'changed' ? 'A key changed for ' + who + '.' : a.kind === 'new' ? 'A new device appeared for ' + who + '.'
      : 'The server sent a key record for ' + who + ' that does not match its own keys. It is ignored.';
    const card = el('div', { class: 'card alert', 'data-kind': a.kind, 'data-person': a.p.id },
      el('strong', { text: head }),
      el('p', { text: a.kind === 'mismatch' ? 'This should never happen. Tell Jason.' :
        (a.p.me ? 'If you did not just add or re-key a device yourself, remove it below.' :
          'This is normal after ' + a.p.name + ' sets up or re-keys a device. If they did not, do not send them anything until you have compared the safety number in person.') }),
      el('ul', {}, ...a.list.map((d) => el('li', { class: 'mono', text: d.label + ': ' + d.fingerprint }))));
    if (a.kind !== 'mismatch') {
      card.append(el('button', { type: 'button', 'data-ack': a.p.id, onclick: () => run(async () => {
        const people = (await api('/people/' + encodeURIComponent(a.p.id))).people[0];
        const good = [];
        for (const d of people.devices) if ((await deviceFp(people.id, d.id, b64u.dec(d.x25519_pub), b64u.dec(d.mlkem_pub))) === d.fingerprint) good.push(d);
        await pinPerson(people.id, good);
      }) }, 'I understand, trust the new key' + (a.list.length > 1 ? 's' : '')));
    }
    box.append(card);
  }
}

function renderPeople(shown, serverPeople) {
  const ul = $('people'); ul.replaceChildren();
  for (const { p, v, sn, firstSeen, pending } of shown) {
    if (p.me) continue;
    const server = serverPeople.find((x) => x.id === p.id);
    const li = el('li', { class: 'card', 'data-person': p.id },
      el('div', { class: 'row' }, el('strong', { text: p.name }),
        el('span', { class: 'tag', text: v.good.length + (v.good.length === 1 ? ' device' : ' devices') })));
    if (sn) {
      li.append(el('p', { class: 'dim', text: 'Safety number for you and ' + p.name + ':' }),
        el('p', { class: 'safety', id: 'safety-' + p.id, 'data-safety': p.id, text: sn }));
      if (server && server.safety_number && server.safety_number !== sn) li.append(el('p', { class: 'bad', text: 'The server computed a different number. Trust the one above, and tell Jason.' }));
      if (pending) li.append(el('p', { class: 'bad', text: 'Not settled: a key alert above is waiting for you.' }));
      else if (firstSeen) li.append(el('p', { class: 'dim', text: 'First time this browser sees ' + p.name + "'s keys. Compare the number once to be sure." }));
    } else {
      li.append(el('p', { class: 'dim', text: !v.good.length ? p.name + ' has no devices set up yet.' : 'Set up this device to see a safety number.' }));
    }
    for (const d of v.good) li.append(el('p', { class: 'mono dim', text: d.label + (d.platform ? ' (' + d.platform + ')' : '') + ' ' + d.fingerprint.slice(0, 16) }));
    ul.append(li);
  }
}

async function refresh() {
  const mine = await api('/mine');
  S.me = mine.person;
  renderThis(mine); renderMine(mine);
  const ppl = (await api('/people')).people;
  const { alerts, shown } = await check(ppl);
  renderAlerts(alerts); renderPeople(shown, ppl);
}
async function run(fn) {
  busy(true);
  try { await fn(); $('alerts').querySelectorAll('[data-kind=error]').forEach((n) => n.remove()); await refresh(); }
  catch (e) { try { await refresh(); } catch (e2) { /* the first error is the useful one */ } fail(e); }
  finally { busy(false); }
}

async function main() {
  busy(true);
  S.support = await detect();
  document.body.dataset.x25519 = S.support.x25519 ? 'webcrypto' : 'none';
  document.body.dataset.mlkem = S.support.mlkem;
  $('support').textContent = 'This browser: X25519 ' + (S.support.x25519 ? 'in WebCrypto (private key not extractable)' : 'NOT available')
    + '; ML-KEM-768 ' + (S.support.mlkem === 'native' ? 'native WebCrypto' : S.support.mlkem === 'noble' ? 'vendored @noble/post-quantum ' + globalThis.NovashareMLKEM.version + ' (secret wrapped by a non-extractable AES-KW key)' : 'NOT available') + '.';
  try { S.local = (await kvGet('device')) || null; } catch (e) { S.local = null; }
  await run(async () => {});
}
main();
"""
