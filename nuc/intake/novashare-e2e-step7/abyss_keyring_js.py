"""abyss_keyring_js.py - the Novashare Keyring page script (2026-11-24), served by abyss_keyring.py at /keyring/static/keyring.js.

Kept in a .py because SWAP-PATCH.sh installs *.py files only; the page loads it with an SRI hash. It uses the
Step 1 device keys from IndexedDB ("novashare-devices", key "device"), the TOFU pins ("pins:<me>") and the vendored
ML-KEM bundle (globalThis.NovashareMLKEM). wrapFor is relay_js.py's function unchanged (novashare/wrap/v1);
unwrapOne is its twin (the receiving side relay's step 3 will need too). DOM: textContent / createElement only.
"""

JS = r"""// keyring.js - Novashare Keyring for Abyss albums (keyring_js.py, 2026-11-24).
const ALG = 'novashare-v1';
const enc = new TextEncoder();
const ABYSS = (document.querySelector('meta[name="abyss-origin"]') || {}).content || '';
const ID_RE = /^abyss-album\/[A-Za-z0-9_-]{22}$/;
const S = { local: null, me: null, mine: [], trusted: [], busy: false };
const $ = (id) => document.getElementById(id);
const b64u = {
  enc(u8) { let s = ''; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000)); return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); },
  dec(s) { const t = atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4)); return Uint8Array.from(t, (c) => c.charCodeAt(0)); },
};
const hex = (u8) => Array.from(u8, (b) => b.toString(16).padStart(2, '0')).join('');
function cat(...parts) { let n = 0; for (const p of parts) n += p.length; const o = new Uint8Array(n); let i = 0; for (const p of parts) { o.set(p, i); i += p.length; } return o; }
function lp(u8) { const o = new Uint8Array(4 + u8.length); new DataView(o.buffer).setUint32(0, u8.length); o.set(u8, 4); return o; }
const rand = (n) => crypto.getRandomValues(new Uint8Array(n));
async function digest(alg, u8) { return new Uint8Array(await crypto.subtle.digest(alg, u8)); }
function idb() {
  return new Promise((res, rej) => { const r = indexedDB.open('novashare-devices', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('kv'); r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error); });
}
async function kvGet(k) {
  const db = await idb();
  return new Promise((res, rej) => { const tx = db.transaction('kv', 'readonly'); const rq = tx.objectStore('kv').get(k); let out;
    rq.onsuccess = () => { out = rq.result; }; tx.oncomplete = () => { db.close(); res(out); }; tx.onerror = () => { db.close(); rej(tx.error); }; });
}
async function api(path) {
  const headers = { 'X-Novashare': '1' };
  if (S.local) headers['X-Device-Id'] = S.local.device_id;
  const r = await fetch(path, { headers, credentials: 'same-origin', cache: 'no-store' });
  let data = null; try { data = await r.json(); } catch { data = null; }
  if (!r.ok) throw new Error((data && data.error) || ('The server answered ' + r.status + '.'));
  return data;
}
async function deviceFp(person, id, x, m) {
  return hex(await digest('SHA-256', cat(enc.encode('novashare/device/v1'), lp(enc.encode(person)), lp(enc.encode(id)), lp(x), lp(m))));
}
function post(msg) { if (window.opener && ABYSS) window.opener.postMessage(msg, ABYSS); }
function sayStatus(t) { $('support').textContent = t; }

// ---------- wrap (relay_js.py wrapFor, unchanged) and its twin
async function wrapFor(Kraw, id, d) {
  const M = globalThis.NovashareMLKEM;
  const rx = await crypto.subtle.importKey('raw', d.x, { name: 'X25519' }, false, []);
  const eph = await crypto.subtle.generateKey({ name: 'X25519' }, false, ['deriveBits']);
  const ephPub = new Uint8Array(await crypto.subtle.exportKey('raw', eph.publicKey));
  const ss1 = new Uint8Array(await crypto.subtle.deriveBits({ name: 'X25519', public: rx }, eph.privateKey, 256));
  const ss2 = new Uint8Array(await crypto.subtle.deriveBits({ name: 'X25519', public: rx }, S.local.x.privateKey, 256));
  const kem = M.encapsulate(d.m);
  const ctx = cat(enc.encode('novashare/wrap/v1'), lp(enc.encode(ALG)), lp(enc.encode(id)), lp(enc.encode(S.local.device_id)),
    lp(enc.encode(d.id)), lp(S.local.xpub), lp(d.x), lp(d.m), lp(ephPub), lp(kem.cipherText));
  const ikm = cat(ss1, ss2, kem.sharedSecret);
  const hk = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveKey']);
  ikm.fill(0); ss1.fill(0); ss2.fill(0); kem.sharedSecret.fill(0);
  const wk = await crypto.subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(32), info: ctx }, hk, { name: 'AES-GCM', length: 256 }, false, ['encrypt']);
  const iv = rand(12);
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: ctx, tagLength: 128 }, wk, Kraw));
  return { device_id: d.id, device_fp: d.fp, eph_pub: b64u.enc(ephPub), kem_ct: b64u.enc(kem.cipherText), wrapped_key: b64u.enc(cat(iv, ct)) };
}
async function decaps(ct) {
  const m = S.local.mlkem;
  if (m.mode === 'native') return new Uint8Array(await crypto.subtle.decapsulateBits({ name: 'ML-KEM-768' }, m.privateKey, ct));
  // Step 7: 'noble2' = sealed by a non-extractable AES-GCM key (devices.js sealSecret); 'noble' = the legacy AES-KW
  // record, read until the Devices page re-seals it.
  let sk;
  if (m.mode === 'noble2') sk = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: m.iv, additionalData: enc.encode('novashare/mlkem-secret/v2') }, m.kek, m.ct));
  else sk = new Uint8Array(await crypto.subtle.exportKey('raw', await crypto.subtle.unwrapKey('raw', m.wrapped, m.kw, 'AES-KW', { name: 'HMAC', hash: 'SHA-256' }, true, ['sign'])));
  try { return globalThis.NovashareMLKEM.decapsulate(ct, sk); } finally { sk.fill(0); }
}
async function unwrapOne(id, w) {
  const sender = S.trustedAll.get(w.sender_id);
  if (!sender) throw new Error('untrusted sender');
  const eph = b64u.dec(w.eph_pub), kemCt = b64u.dec(w.kem_ct), box = b64u.dec(w.wrapped_key);
  if (eph.length !== 32 || kemCt.length !== 1088 || box.length !== 60) throw new Error('bad wrap');
  const ephK = await crypto.subtle.importKey('raw', eph, { name: 'X25519' }, false, []);
  const sK = await crypto.subtle.importKey('raw', sender.x, { name: 'X25519' }, false, []);
  const ss1 = new Uint8Array(await crypto.subtle.deriveBits({ name: 'X25519', public: ephK }, S.local.x.privateKey, 256));
  const ss2 = new Uint8Array(await crypto.subtle.deriveBits({ name: 'X25519', public: sK }, S.local.x.privateKey, 256));
  const ss3 = await decaps(kemCt);
  const ctx = cat(enc.encode('novashare/wrap/v1'), lp(enc.encode(ALG)), lp(enc.encode(id)), lp(enc.encode(w.sender_id)),
    lp(enc.encode(S.local.device_id)), lp(sender.x), lp(S.local.xpub), lp(S.local.mpub), lp(eph), lp(kemCt));
  const ikm = cat(ss1, ss2, ss3);
  const hk = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveKey']);
  ikm.fill(0); ss1.fill(0); ss2.fill(0); ss3.fill(0);
  const wk = await crypto.subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(32), info: ctx }, hk, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
  return new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: box.subarray(0, 12), additionalData: ctx, tagLength: 128 }, wk, box.subarray(12)));
}

// ---------- the requests
async function doWrap(req) {
  if (!ID_RE.test(String(req.id)) || !(req.key instanceof Uint8Array) || req.key.length !== 32) throw new Error('bad request');
  const wraps = [];
  for (const d of S.trusted) { const w = await wrapFor(req.key, req.id, d); w.sender_id = S.local.device_id; wraps.push(w); }
  req.key.fill(0);
  return { type: 'keyring-wrapped', id: req.id, wraps };
}
async function doUnwrap(req) {
  if (!Array.isArray(req.jobs) || req.jobs.length > 200) throw new Error('bad request');
  const keys = [];
  for (const j of req.jobs) {
    let key = null;
    try {
      if (!ID_RE.test(String(j.id)) || !Array.isArray(j.wraps)) throw new Error('bad job');
      const w = j.wraps.find((x) => x && x.device_id === S.local.device_id);
      if (w) key = await unwrapOne(j.id, w);
    } catch { key = null; }
    keys.push({ id: String(j.id), key });
  }
  return { type: 'keyring-keys', keys };
}
function ask(req) {
  const n = req.type === 'keyring-wrap' ? S.trusted.length : (req.jobs || []).length;
  $('askText').textContent = req.type === 'keyring-wrap'
    ? 'Abyss asks to lock a new album key to your ' + n + (n === 1 ? ' device.' : ' devices.')
    : 'Abyss asks to open ' + n + (n === 1 ? ' album' : ' albums') + ' with this device.';
  $('ask').hidden = false;
  $('allow').onclick = async () => {
    if (S.busy) return; S.busy = true; $('allow').disabled = true; sayStatus('Working...');
    try { post(req.type === 'keyring-wrap' ? await doWrap(req) : await doUnwrap(req)); sayStatus('Done. This window closes now.'); setTimeout(() => window.close(), 400); }
    catch (e) { post({ type: 'keyring-error', why: 'The keyring could not do that: ' + (e.message || e) }); sayStatus('That did not work.'); }
    S.busy = false;
  };
  $('deny').onclick = () => { post({ type: 'keyring-error', why: 'Not allowed on the keyring.' }); window.close(); };
}

async function main() {
  if (!window.opener || !ABYSS) { sayStatus('Open this from an Abyss album page.'); return; }
  try {
    if (!globalThis.NovashareMLKEM) throw new Error('The ML-KEM code did not load.');
    S.local = (await kvGet('device')) || null;
    if (!S.local || !S.local.x) throw new Error('This browser is not one of your Novashare devices yet. Set it up on the Devices page first.');
    const mine = await api('/devices/api/mine');
    S.me = mine.person;
    const row = mine.devices.find((d) => d.id === S.local.device_id);
    if (!row || row.revoked != null) throw new Error('This device was removed. Set it up again on the Devices page.');
    const pins = ((await kvGet('pins:' + S.me)) || { persons: {} }).persons[S.me] || { devices: {} };
    S.trusted = []; S.trustedAll = new Map();
    for (const d of mine.devices) {
      const x = b64u.dec(d.x25519_pub), m = b64u.dec(d.mlkem_pub);
      const fp = await deviceFp(S.me, d.id, x, m);
      const ok = fp === d.fingerprint && (d.id === S.local.device_id || pins.devices[d.id] === fp);
      if (!ok) { $('skipped').append(Object.assign(document.createElement('li'), { textContent: 'Skipped ' + (d.label || d.id.slice(0, 6)) + ': open Devices and check its key first.' })); continue; }
      S.trustedAll.set(d.id, { id: d.id, fp, x, m });                 // senders: revoked devices still open old wraps
      if (d.revoked == null) S.trusted.push({ id: d.id, fp, x, m });
    }
    sayStatus('This device: ' + S.local.label + '. ' + S.trusted.length + (S.trusted.length === 1 ? ' device' : ' devices') + ' can hold album keys.');
    addEventListener('message', (e) => {
      if (e.origin !== ABYSS || e.source !== window.opener || !e.data || typeof e.data !== 'object') return;
      if (e.data.type === 'keyring-wrap' || e.data.type === 'keyring-unwrap') ask(e.data);
    });
    post({ type: 'keyring-ready', device: { id: S.local.device_id, label: S.local.label }, devices: S.trusted.length });
  } catch (e) {
    sayStatus(e.message || String(e));
    post({ type: 'keyring-error', why: e.message || String(e) });
  }
}
main();
"""
