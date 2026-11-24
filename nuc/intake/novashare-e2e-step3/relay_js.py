"""relay_js.py - the Novashare Send page script (E2E Step 2, 2026-11-24), served by relay.py at /relay/static/relay.js.

Kept in a .py because SWAP-PATCH.sh installs *.py files only; the page loads it with an SRI hash relay.py computes
from these bytes. It uses the Step 1 device keys from IndexedDB ("novashare-devices", key "device") and the vendored
ML-KEM bundle (globalThis.NovashareMLKEM, loaded by the page with its own SRI). The wire format is written down in
relay.py's docstring; test_relay_e2e.py has the Python twin of every function here and test_relay_browser.py
proves the two agree (a send made here is opened by the Python decryptor, byte for byte).

Crypto rules kept here (soundcheck cryptographic-failures / crypto-library-misuse, 2026-11-24):
  - every key and nonce prefix from crypto.getRandomValues / WebCrypto generateKey; nothing from Math.random.
  - the content key K is imported NON-extractable for the piece encryption; its raw bytes live only long enough
    to be wrapped for each device, then are zeroed. The X25519 device key stays non-extractable (Step 1).
  - AES-GCM nonces: prefix (8 random bytes per send) + piece number; the manifest uses number 0xFFFFFFFF, which no
    piece can have (at most 2561 pieces). One K per send, so a nonce never repeats under a key...
  - ...except on resume after a reload, which re-encrypts a piece with the SAME nonce. That is safe only if the
    plaintext is the same, so the ciphertext SHA-256 of each piece is saved in IndexedDB BEFORE it is sent and a
    resumed piece that hashes differently is never sent (the send stops: "the file changed").
  - no plaintext ever goes to the network; there is no fallback path to the library upload.
Step 3 (2026-11-24): adds renderSent(), the "Sent" list (GET /relay/api/sent: per device waiting / saving / saved /
declined). Nothing in the send itself changed.
"""

JS = r"""// relay.js - Novashare Send page (relay_js.py, E2E Step 2, 2026-11-24). No outside libraries besides the vendored ML-KEM.
const ALG = 'novashare-v1';
const PLAIN = 4 * 1048576, CT = PLAIN + 16, MANIFEST_IDX = 0xFFFFFFFF, PARALLEL = 3, TRIES = 8;
const enc = new TextEncoder();
const S = { local: null, me: null, people: [], busy: false };
const $ = (id) => document.getElementById(id);

// ---------- helpers (the same encodings as devices.js and relay.py)
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
  enc(u8) { let s = ''; for (let i = 0; i < u8.length; i += 0x8000) s += String.fromCharCode.apply(null, u8.subarray(i, i + 0x8000)); return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); },
  dec(s) { const t = atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4)); return Uint8Array.from(t, (c) => c.charCodeAt(0)); },
};
const hex = (u8) => Array.from(u8, (b) => b.toString(16).padStart(2, '0')).join('');
function cat(...parts) { let n = 0; for (const p of parts) n += p.length; const o = new Uint8Array(n); let i = 0; for (const p of parts) { o.set(p, i); i += p.length; } return o; }
function lp(u8) { const o = new Uint8Array(4 + u8.length); new DataView(o.buffer).setUint32(0, u8.length); o.set(u8, 4); return o; }
function u32(n) { const o = new Uint8Array(4); new DataView(o.buffer).setUint32(0, n); return o; }
const rand = (n) => crypto.getRandomValues(new Uint8Array(n));
async function digest(alg, u8) { return new Uint8Array(await crypto.subtle.digest(alg, u8)); }
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function size(n) { return n >= 1073741824 ? (n / 1073741824).toFixed(2) + ' GB' : n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n + ' bytes'; }

// ---------- IndexedDB (Step 1's store "kv": device, pins:<me>; this page adds send:<id> and sha:<id>:<n>)
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
const kvKeys = (prefix) => kv('readonly', (s) => s.getAllKeys(IDBKeyRange.bound(prefix, prefix + '\uffff')));
async function kvDelPrefix(prefix) { await kv('readwrite', (s) => { s.delete(IDBKeyRange.bound(prefix, prefix + '\uffff')); }); }

// ---------- API
async function api(path, opts = {}) {
  const headers = { 'X-Novashare': '1', ...(opts.headers || {}) };
  if (opts.json) headers['Content-Type'] = 'application/json';
  if (S.local) headers['X-Device-Id'] = S.local.device_id;
  const r = await fetch(path, { method: opts.method || 'GET', headers, credentials: 'same-origin', cache: 'no-store',
    body: opts.json ? JSON.stringify(opts.json) : opts.body });
  let data = null; try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) { const e = new Error((data && data.error) || ('The server answered ' + r.status + '.')); e.code = data && data.code; e.status = r.status; throw e; }
  return data;
}

// ---------- fingerprint (twin of devices.py device_fp) and the pin check
async function deviceFp(person, id, x, m) {
  return hex(await digest('SHA-256', cat(enc.encode('novashare/device/v1'), lp(enc.encode(person)), lp(enc.encode(id)), lp(x), lp(m))));
}
async function trusted(p, pins) {
  // Every device key recomputed from its raw bytes and equal to what this browser pinned on the Devices page.
  const pin = pins.persons[p.id]; const devs = [];
  for (const d of p.devices) {
    if (S.local && d.id === S.local.device_id) continue;
    const x = b64u.dec(d.x25519_pub), m = b64u.dec(d.mlkem_pub);
    const fp = await deviceFp(p.id, d.id, x, m);
    if (fp !== d.fingerprint) return { ok: false, why: 'The server sent a key that does not match its record. Tell Jason.' };
    if (!pin || pin.devices[d.id] !== fp) return { ok: false, why: 'Open Devices and check ' + (p.me ? 'your' : p.name + "'s") + ' keys first (a device is new or changed).' };
    devs.push({ id: d.id, fp, x, m });
  }
  return devs.length ? { ok: true, devs } : { ok: false, why: p.me ? 'You have no other device.' : p.name + ' has no device set up yet.' };
}

// ---------- the crypto (wire format in relay.py)
function pieceCount(n) { return Math.max(1, Math.ceil(n / PLAIN)); }
function pieceAad(id, i, last) { return cat(enc.encode('novashare/piece/v1'), lp(enc.encode(id)), u32(i), new Uint8Array([last ? 1 : 0])); }
async function encPiece(K, prefix, id, i, last, plain) {
  return new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: cat(prefix, u32(i)), additionalData: pieceAad(id, i, last), tagLength: 128 }, K, plain));
}
async function encManifest(K, prefix, id, meta) {
  const json = enc.encode(JSON.stringify(meta));
  const padded = new Uint8Array(Math.ceil((4 + json.length) / 256) * 256);
  padded.set(u32(json.length), 0); padded.set(json, 4);              // the rest stays zero: the name length is hidden
  if (padded.length > 65536) throw new Error('The name and note are too long.');
  const iv = cat(prefix, u32(MANIFEST_IDX));
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv, additionalData: cat(enc.encode('novashare/manifest/v1'), lp(enc.encode(id))), tagLength: 128 }, K, padded));
  return cat(iv, ct);
}
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

// ---------- send and resume
function progress(t) { $('progress').textContent = t; }

async function newSend(file, caption, targets) {
  const id = b64u.enc(rand(16));                                     // 22 characters; the AADs and wraps bind it
  const prefix = rand(8);
  const Kraw = rand(32);
  const K = await crypto.subtle.importKey('raw', Kraw, { name: 'AES-GCM' }, false, ['encrypt']);
  const pieces = pieceCount(file.size);
  let body;
  try {
    const manifest = await encManifest(K, prefix, id, { v: 1, name: file.name, type: file.type || '', size: file.size,
      last_modified: file.lastModified, caption: caption || '', pieces });
    const wraps = [];
    for (const t of targets) for (const d of t.devs) wraps.push(await wrapFor(Kraw, id, d));
    body = { id, alg: ALG, to: targets.map((t) => t.id), size_ct: file.size + 16 * pieces, pieces, manifest: b64u.enc(manifest), wraps };
  } finally { Kraw.fill(0); }
  const st = { id, body, K, prefix, file: { name: file.name, size: file.size, lastModified: file.lastModified }, at: Date.now() };
  await kvSet('send:' + id, st);                                     // before create: a reload can still resume it
  return st;
}

async function upload(st, file) {
  let r;
  try { r = await api('/relay/api/transfers', { method: 'POST', json: st.body }); } catch (e) {
    // a send the server will never take as it is: forget it, so the next press starts fresh (new id, new keys)
    if (['devices_changed', 'no_devices', 'id_taken', 'not_uploading', 'too_big', 'bad_request', 'bad_key'].includes(e.code)) {
      await kvDelPrefix('sha:' + st.id + ':'); await kv('readwrite', (s) => s.delete('send:' + st.id));
    }
    throw e;
  }
  const todo = [...r.missing]; const pieces = st.body.pieces; let done = pieces - todo.length; let sent = 0;
  const t0 = performance.now();
  document.body.dataset.sending = st.id;
  progress('Sending ' + done + ' of ' + pieces + ' pieces' + (r.resumed ? ' (resumed)' : '') + '...');
  let stop = null;
  async function one(i) {
    const last = i === pieces - 1;
    const plain = new Uint8Array(await file.slice(i * PLAIN, Math.min(file.size, (i + 1) * PLAIN)).arrayBuffer());
    const ct = await encPiece(st.K, st.prefix, st.id, i, last, plain);
    const sha = hex(await digest('SHA-256', ct));
    const key = 'sha:' + st.id + ':' + i; const before = await kvGet(key);
    if (before && before !== sha) throw Object.assign(new Error('The file changed since this send started. Start a new send.'), { code: 'file_changed' });
    if (!before) await kvSet(key, sha);                              // saved BEFORE the bytes leave (nonce-reuse guard)
    for (let k = 0; ; k++) {
      try {
        await api('/relay/api/transfers/' + st.id + '/pieces/' + i, { method: 'PUT', body: ct, headers: { 'X-Piece-Sha256': sha, 'Content-Type': 'application/octet-stream' } });
        return ct.length;
      } catch (e) {
        const again = !e.status || e.status >= 500 || e.code === 'busy' || e.code === 'checksum_mismatch' || e.code === 'piece_short' || e.status === 429;
        if (!again || k >= TRIES - 1) throw e;
        await sleep(Math.min(30000, 500 * 2 ** k));
      }
    }
  }
  async function worker() {
    while (todo.length && !stop) {
      const i = todo.shift();
      try { sent += await one(i); done++; } catch (e) { stop = stop || e; return; }
      const s = (performance.now() - t0) / 1000;
      progress('Sending ' + done + ' of ' + pieces + ' pieces, ' + size(sent) + ' in ' + s.toFixed(0) + ' s...');
    }
  }
  await Promise.all(Array.from({ length: PARALLEL }, worker));
  if (stop) throw stop;
  const fin = await api('/relay/api/transfers/' + st.id);
  if (fin.state !== 'ready') throw new Error('The server has not got every piece yet (' + fin.missing.length + ' missing). Press send again to resume.');
  await kvDelPrefix('sha:' + st.id + ':'); await kv('readwrite', (s) => s.delete('send:' + st.id));
  document.body.dataset.sent = st.id;
  progress('Sent: ' + size(file.size) + ' locked for ' + st.body.wraps.length + ' device' + (st.body.wraps.length === 1 ? '' : 's') + '. The server holds only scrambled pieces.');
}

async function pendingFor(file) {
  for (const k of await kvKeys('send:')) {
    const st = await kvGet(k);
    if (st && st.file.name === file.name && st.file.size === file.size && st.file.lastModified === file.lastModified) return st;
  }
  return null;
}

// ---------- page
function fail(e) { $('alerts').replaceChildren(el('div', { class: 'card alert', 'data-kind': 'error', 'data-code': (e && e.code) || '' }, el('strong', { text: 'Not sent. ' }), (e && e.message) || String(e))); }

async function renderPending() {
  const ul = $('pending'); ul.replaceChildren();
  for (const k of await kvKeys('send:')) {
    const st = await kvGet(k);
    if (st) ul.append(el('li', { class: 'card', 'data-pending': st.id }, el('strong', { text: 'Unfinished: ' + st.file.name }), ' (' + size(st.file.size) + '). Pick the same file and press send to carry on.'));
  }
}

// ---------- Step 3: what each receiving device did (GET /relay/api/sent; names stay on this device only)
const WORD = { pending: 'waiting', accepted: 'accepted, not saved yet', downloaded: 'saved', declined: 'declined' };
function when(t) { const d = new Date(t * 1000); return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }); }
async function renderSent() {
  const ul = $('sent'); if (!ul) return;
  let r; try { r = await api('/relay/api/sent'); } catch (e) { ul.replaceChildren(el('li', { class: 'dim', text: 'Could not load your sends: ' + e.message })); return; }
  ul.replaceChildren();
  if (!r.items.length) { ul.append(el('li', { class: 'dim', text: 'Nothing sent yet.' })); return; }
  for (const t of r.items) {
    const li = el('li', { class: 'card', 'data-sent-id': t.id, 'data-state': t.state },
      el('div', { class: 'row' }, el('strong', { text: size(t.size) + ' sent ' + when(t.created) }),
        el('span', { class: 'tag', text: t.state === 'deleted' ? 'finished' : t.state === 'uploading' ? 'still sending' : 'on the server' })));
    const dl = el('ul', { class: 'plain' });
    for (const d of t.deliveries) dl.append(el('li', { 'data-device': d.device_id, 'data-delivery': d.state }, d.name + ', ' + d.label + ': ', el('strong', { class: d.state === 'declined' ? 'bad' : d.state === 'downloaded' ? 'ok' : '', text: WORD[d.state] || d.state })));
    li.append(dl); ul.append(li);
  }
}

async function main() {
  document.body.dataset.ready = '0';
  try {
    if (!globalThis.NovashareMLKEM) throw new Error('The ML-KEM code did not load (integrity check failed or blocked), so this device cannot send.');
    S.local = (await kvGet('device')) || null;
    if (!S.local || !S.local.x) { $('support').textContent = 'This browser is not one of your devices yet. Set it up on the Devices page (/devices) first.'; return; }
    const mine = await api('/devices/api/mine');
    S.me = mine.person;
    const row = mine.devices.find((d) => d.id === S.local.device_id);
    if (!row || row.revoked != null) { $('support').textContent = 'This device was removed. Set it up again on the Devices page.'; return; }
    $('support').textContent = 'This device: ' + S.local.label + '. Keys: X25519 + ML-KEM-768 (' + globalThis.NovashareMLKEM.version + ').';
    const pins = (await kvGet('pins:' + S.me)) || { persons: {} };
    const ul = $('people'); ul.replaceChildren();
    for (const p of (await api('/devices/api/people')).people) {
      const t = await trusted(p, pins); S.people.push({ p, t });
      const id = 'to-' + p.id;
      const box = el('input', { type: 'checkbox', id, 'data-person': p.id });
      if (!t.ok) box.disabled = true;
      ul.append(el('li', { class: 'card' }, el('div', { class: 'row' }, el('label', { for: id }, box, ' ' + (p.me ? 'My other devices' : p.name)),
        el('span', { class: 'tag', text: t.ok ? t.devs.length + (t.devs.length === 1 ? ' device' : ' devices') : 'not ready' })),
        t.ok ? null : el('p', { class: 'dim', text: t.why })));
    }
    await renderPending();
    await renderSent();
    $('send').disabled = false;
    $('send').addEventListener('click', async () => {
      if (S.busy) return;
      const file = $('file').files[0];
      const targets = S.people.filter((x) => x.t.ok && $('to-' + x.p.id).checked).map((x) => ({ id: x.p.id, devs: x.t.devs }));
      if (!file) return fail(new Error('Pick a file first.'));
      if (!targets.length) return fail(new Error('Pick at least one person.'));
      S.busy = true; $('send').disabled = true; $('alerts').replaceChildren(); document.body.dataset.ready = '0';
      try {
        let st = await pendingFor(file);
        if (st) progress('Carrying on with the unfinished send of this file...');
        else { progress('Locking the file for ' + targets.length + (targets.length === 1 ? ' person' : ' people') + '...'); st = await newSend(file, $('caption').value, targets); }
        await upload(st, file);
      } catch (e) { fail(e); } finally { S.busy = false; $('send').disabled = false; document.body.dataset.ready = '1'; await renderPending(); await renderSent(); }
    });
  } catch (e) { fail(e); } finally { if (!S.busy) document.body.dataset.ready = '1'; }
}
main();
"""
