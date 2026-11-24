"""receive_js.py - the Novashare Inbox page script, style and download service worker (E2E Step 3, 2026-11-24).

Served by receive.py at /relay/in/inbox.js, /relay/in/inbox.css (both with SRI in the page) and /relay/sw.js. Kept in
a .py because SWAP-PATCH.sh installs *.py files only. Uses the Step 1 device keys from IndexedDB
("novashare-devices", key "device"), the Step 1 pins ("pins:<me>") and the vendored ML-KEM bundle.

What the page does (wire format: relay.py docstring; Python twin of the unwrap and decrypt: relay_twin.py):
  1. GET /relay/api/inbox: the deliveries to THIS device (sender, size, time; no names).
  2. For each open one: GET its envelope (this device's wrap only), check the sender device against the pins taken on
     the Devices page (fingerprint recomputed from the directory's raw keys), unwrap the content key
     (X25519 eph + X25519 static + ML-KEM-768 decapsulation -> HKDF-SHA256 with the bound context -> AES-GCM), open the
     manifest and check it agrees with the server's geometry. Only then are the name and note shown (textContent).
  3. Accept: the save target is opened FIRST (the folder permission / file picker need the click), then accept is
     posted, then pieces are fetched (3 ahead), each length-checked and AES-GCM-decrypted IN ORDER with its own nonce
     and AAD (index + last flag), and written straight to disk. After the last piece the total must equal the
     manifest size; only then is the file closed (finalized) and complete posted. Any failure = the file is aborted
     and removed, complete is never called, the delivery stays accepted so Accept can be tried again.
Save targets, best first (feature-detected, never sniffed):
  folder   File System Access: a folder picked once (showDirectoryPicker, e.g. D:\\Novashare\\Inbox) is remembered
           in IndexedDB and later saves go there without asking. createWritable() writes to a hidden swap file that
           only becomes the real file on close(); abort() throws it away (Chrome / Edge desktop).
  picker   showSaveFilePicker per file, same writable rules.
  download a service-worker download (/relay/sw.js answers /relay/dl/<token> from a stream this page feeds, with
           backpressure), opened in a hidden same-origin frame (the inbox CSP adds frame-src 'self' for it; an
           <a download> click is not routed through the worker by Chrome); on tamper the stream errors and the
           browser marks the download failed (Android Chrome).
  blob     last resort for files up to 256 MB on browsers with none of the above.
Never the whole file in memory except the blob fallback: at most 3 fetched pieces (4 MiB each) plus one being written.
Replay: a finished transfer id is remembered ("got:<id>"); the server also deletes the wrap on complete.
Crypto rules (soundcheck cryptographic-failures / crypto-library-misuse, 2026-11-24): AES-GCM only; the content key
is imported non-extractable and its raw bytes zeroed; HKDF input and shared secrets zeroed after use; the noble ML-KEM
secret is unwrapped from AES-KW only for the decapsulation and zeroed; every check failure is one generic "did not
pass the check" (no oracle detail beyond the piece number, which the server already knows).
"""

JS = r"""// inbox.js - Novashare Inbox (receive_js.py, E2E Step 3, 2026-11-24). No outside libraries besides the vendored ML-KEM.
const ALG = 'novashare-v1';
const PLAIN = 4 * 1048576, CT = PLAIN + 16, MANIFEST_IDX = 0xFFFFFFFF, AHEAD = 3, TRIES = 6, BLOB_MAX = 256 * 1048576;
const enc = new TextEncoder(), dec = new TextDecoder('utf-8', { fatal: true });
const S = { local: null, me: null, people: [], opened: new Map(), busy: false, dir: null, mode: 'auto', sw: null };
const $ = (id) => document.getElementById(id);

// ---------- helpers (the same encodings as relay.js and relay.py)
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
  dec(s) { const t = atob(String(s).replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((String(s).length + 3) % 4)); return Uint8Array.from(t, (c) => c.charCodeAt(0)); },
};
const hex = (u8) => Array.from(u8, (b) => b.toString(16).padStart(2, '0')).join('');
function cat(...parts) { let n = 0; for (const p of parts) n += p.length; const o = new Uint8Array(n); let i = 0; for (const p of parts) { o.set(p, i); i += p.length; } return o; }
function lp(u8) { const o = new Uint8Array(4 + u8.length); new DataView(o.buffer).setUint32(0, u8.length); o.set(u8, 4); return o; }
function u32(n) { const o = new Uint8Array(4); new DataView(o.buffer).setUint32(0, n); return o; }
const rand = (n) => crypto.getRandomValues(new Uint8Array(n));
async function digest(alg, u8) { return new Uint8Array(await crypto.subtle.digest(alg, u8)); }
function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }
function size(n) { return n >= 1073741824 ? (n / 1073741824).toFixed(2) + ' GB' : n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n >= 1024 ? (n / 1024).toFixed(0) + ' KB' : n + ' bytes'; }
function when(t) { const d = new Date(t * 1000); return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' }) + ', ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }); }
function tamper(msg) { return Object.assign(new Error(msg), { code: 'tampered' }); }

// ---------- IndexedDB (Step 1's store "kv": device, pins:<me>; this page adds savedir, savemode, got:<id>)
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

// ---------- API
async function call(path, opts = {}) {
  const headers = { 'X-Novashare': '1' };
  if (S.local) headers['X-Device-Id'] = S.local.device_id;
  return fetch(path, { method: opts.method || 'GET', headers, credentials: 'same-origin', cache: 'no-store' });
}
async function fail_(r) {
  let data = null; try { data = await r.json(); } catch (e) { data = null; }
  const e = new Error((data && data.error) || ('The server answered ' + r.status + '.')); e.code = data && data.code; e.status = r.status; return e;
}
async function api(path, opts) { const r = await call(path, opts); if (!r.ok) throw await fail_(r); return r.json(); }
async function apiBytes(path) { const r = await call(path); if (!r.ok) throw await fail_(r); return new Uint8Array(await r.arrayBuffer()); }

// ---------- trust: the sender device must be the one this browser pinned on the Devices page
async function deviceFp(person, id, x, m) {
  return hex(await digest('SHA-256', cat(enc.encode('novashare/device/v1'), lp(enc.encode(person)), lp(enc.encode(id)), lp(x), lp(m))));
}
async function senderProblem(env, item) {
  const who = item.sender.person === S.me ? 'your own device' : item.sender.name;
  const p = S.people.find((x) => x.id === env.sender.person);
  const d = p && p.devices.find((x) => x.id === env.sender.device_id);
  if (!d) return 'The device that sent this (' + who + ') has been removed, so it cannot be checked. Ask for it again.';
  if (d.x25519_pub !== env.sender.x25519_pub || d.fingerprint !== env.sender.fingerprint
      || (await deviceFp(p.id, d.id, b64u.dec(d.x25519_pub), b64u.dec(d.mlkem_pub))) !== env.sender.fingerprint)
    return 'The server sent a key for the sender that does not match its record. Do not accept it; tell Jason.';
  const pins = (await kvGet('pins:' + S.me)) || { persons: {} };
  const pin = pins.persons[env.sender.person];
  if (!pin || pin.devices[env.sender.device_id] !== env.sender.fingerprint)
    return 'Open Devices and check ' + (item.sender.person === S.me ? 'your' : item.sender.name + "'s") + ' keys first (the sending device is new or changed).';
  return null;
}

// ---------- the crypto (wire format in relay.py; Python twin relay_twin.unwrap / open_manifest / decrypt_to)
function pieceCount(n) { return Math.max(1, Math.ceil(n / PLAIN)); }
function pieceAad(id, i, last) { return cat(enc.encode('novashare/piece/v1'), lp(enc.encode(id)), u32(i), new Uint8Array([last ? 1 : 0])); }
async function decaps(ct) {
  const m = S.local.mlkem;
  if (m.mode === 'native') return new Uint8Array(await crypto.subtle.decapsulateBits({ name: 'ML-KEM-768' }, m.privateKey, ct));
  const carrier = await crypto.subtle.unwrapKey('raw', m.wrapped, m.kw, 'AES-KW', { name: 'HMAC', hash: 'SHA-256' }, true, ['sign']);
  const sk = new Uint8Array(await crypto.subtle.exportKey('raw', carrier));
  try { return globalThis.NovashareMLKEM.decapsulate(ct, sk); } finally { sk.fill(0); }
}
async function openEnvelope(env) {
  const id = env.id, w = env.wrap;
  if (env.alg !== ALG || w.device_id !== S.local.device_id) throw tamper('This was not locked for this device.');
  const sx = b64u.dec(env.sender.x25519_pub), eph = b64u.dec(w.eph_pub), kct = b64u.dec(w.kem_ct), wk = b64u.dec(w.wrapped_key);
  if (sx.length !== 32 || eph.length !== 32 || kct.length !== 1088 || wk.length !== 60) throw tamper('The key for this device has the wrong shape.');
  let K;
  try {
    const ephK = await crypto.subtle.importKey('raw', eph, { name: 'X25519' }, false, []);
    const sxK = await crypto.subtle.importKey('raw', sx, { name: 'X25519' }, false, []);
    const ss1 = new Uint8Array(await crypto.subtle.deriveBits({ name: 'X25519', public: ephK }, S.local.x.privateKey, 256));
    const ss2 = new Uint8Array(await crypto.subtle.deriveBits({ name: 'X25519', public: sxK }, S.local.x.privateKey, 256));
    const ss3 = await decaps(kct);
    const ctx = cat(enc.encode('novashare/wrap/v1'), lp(enc.encode(ALG)), lp(enc.encode(id)), lp(enc.encode(env.sender.device_id)),
      lp(enc.encode(S.local.device_id)), lp(sx), lp(S.local.xpub), lp(S.local.mpub), lp(eph), lp(kct));
    const ikm = cat(ss1, ss2, ss3); ss1.fill(0); ss2.fill(0); ss3.fill(0);
    const hk = await crypto.subtle.importKey('raw', ikm, 'HKDF', false, ['deriveKey']); ikm.fill(0);
    const wkey = await crypto.subtle.deriveKey({ name: 'HKDF', hash: 'SHA-256', salt: new Uint8Array(32), info: ctx }, hk, { name: 'AES-GCM', length: 256 }, false, ['decrypt']);
    const Kraw = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: wk.subarray(0, 12), additionalData: ctx, tagLength: 128 }, wkey, wk.subarray(12)));
    try { K = await crypto.subtle.importKey('raw', Kraw, { name: 'AES-GCM' }, false, ['decrypt']); } finally { Kraw.fill(0); }
  } catch (e) { throw tamper('The key for this device did not pass the check, so it cannot be opened.'); }
  const blob = b64u.dec(env.manifest);
  if (blob.length < 28 + 256 || (blob.length - 28) % 256 || hex(blob.subarray(8, 12)) !== 'ffffffff') throw tamper('The file details have the wrong shape.');
  let pt;
  try { pt = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: blob.subarray(0, 12), additionalData: cat(enc.encode('novashare/manifest/v1'), lp(enc.encode(id))), tagLength: 128 }, K, blob.subarray(12))); }
  catch (e) { throw tamper('The file details did not pass the check.'); }
  const n = new DataView(pt.buffer, pt.byteOffset).getUint32(0);
  if (4 + n > pt.length || pt.subarray(4 + n).some((b) => b !== 0)) throw tamper('The file details are padded wrongly.');
  let man; try { man = JSON.parse(dec.decode(pt.subarray(4, 4 + n))); } catch (e) { throw tamper('The file details are not readable.'); }
  if (!man || man.v !== 1 || typeof man.name !== 'string' || !Number.isSafeInteger(man.size) || man.size < 0 || man.pieces !== pieceCount(man.size)
      || man.pieces !== env.pieces || env.size_ct !== man.size + 16 * man.pieces) throw tamper('The file details do not agree with what the server holds.');
  return { K, prefix: blob.slice(0, 8), man, env };
}

// ---------- where the file goes
function safeName(n) {
  n = String(n || '').normalize('NFC').replace(/[\u0000-\u001f\u007f<>:"/\\|?*]/g, '_').replace(/^[\s.]+|[\s.]+$/g, '');
  if (!n) n = 'novashare-file';
  if (/^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$/i.test(n)) n = '_' + n;
  if (n.length > 180) { const dot = n.lastIndexOf('.'); const ext = dot > 0 && n.length - dot <= 16 ? n.slice(dot) : ''; n = n.slice(0, 180 - ext.length) + ext; }
  return n;
}
function variant(name, i) { if (!i) return name; const dot = name.lastIndexOf('.'); return dot > 0 ? name.slice(0, dot) + ' (' + i + ')' + name.slice(dot) : name + ' (' + i + ')'; }
async function freeName(dir, name) {
  for (let i = 0; i < 1000; i++) {
    const c = variant(name, i);
    try { await dir.getFileHandle(c); } catch (e) { if (e.name === 'NotFoundError') return c; if (e.name !== 'TypeMismatchError') throw e; }
  }
  throw new Error('That folder already has too many files with this name.');
}
const canFolder = () => typeof globalThis.showDirectoryPicker === 'function';
const canPicker = () => typeof globalThis.showSaveFilePicker === 'function';
const canSW = () => !!(navigator.serviceWorker && globalThis.TransformStream);

async function folderSink(dir, name) {
  let perm = await dir.queryPermission({ mode: 'readwrite' });
  if (perm !== 'granted') perm = await dir.requestPermission({ mode: 'readwrite' });
  if (perm !== 'granted') throw new Error('Novashare may not save into "' + dir.name + '". Choose the folder again.');
  const final = await freeName(dir, name);
  const fh = await dir.getFileHandle(final, { create: true });
  const w = await fh.createWritable({ keepExistingData: false });
  return { kind: 'folder', savedAs: final, where: dir.name, write: (u8) => w.write(u8), close: () => w.close(),
    abort: async () => { try { await w.abort(); } catch (e) { /* already closed */ } try { await dir.removeEntry(final); } catch (e) { /* gone */ } } };
}
async function pickerSink(name) {
  const fh = await globalThis.showSaveFilePicker({ suggestedName: name, id: 'novashare-inbox' });
  const w = await fh.createWritable({ keepExistingData: false });
  return { kind: 'picker', savedAs: fh.name, where: 'the place you picked', write: (u8) => w.write(u8), close: () => w.close(),
    abort: async () => { try { await w.abort(); } catch (e) { /* closed */ } try { if (fh.remove) await fh.remove(); } catch (e) { /* gone */ } } };
}
async function swReady() {
  if (S.sw) return S.sw;
  // Trusted Types (the page CSP requires them for script URLs): a policy that allows exactly this one worker URL
  const tt = globalThis.trustedTypes ? (S.tt = S.tt || trustedTypes.createPolicy('novashare-sw', { createScriptURL: (u) => { if (u !== '/relay/sw.js') throw new Error('blocked'); return u; } })) : null;
  await navigator.serviceWorker.register(tt ? tt.createScriptURL('/relay/sw.js') : '/relay/sw.js', { scope: '/relay/' });
  const reg = await Promise.race([navigator.serviceWorker.ready, sleep(10000).then(() => { throw new Error('The download helper did not start.'); })]);
  // the download link is only answered by the worker once it controls this page (clients.claim on first install)
  if (!navigator.serviceWorker.controller) await Promise.race([new Promise((r) => navigator.serviceWorker.addEventListener('controllerchange', r, { once: true })), sleep(5000)]);
  S.sw = navigator.serviceWorker.controller || reg.active;
  return S.sw;
}
async function swSink(name, total) {
  const sw = await swReady();
  const ts = new TransformStream({}, new CountQueuingStrategy({ highWaterMark: 2 }), new CountQueuingStrategy({ highWaterMark: 2 }));
  const writer = ts.writable.getWriter();
  const token = b64u.enc(rand(16));
  const ch = new MessageChannel();
  let queued, started;
  const q = new Promise((res) => { queued = res; }), st = new Promise((res) => { started = res; });
  ch.port1.onmessage = (m) => { if (m.data === 'queued') queued(); else if (m.data === 'started') started(); };
  const late = (what) => sleep(20000).then(() => { throw new Error('The download ' + what + '. Allow downloads for this site and try again.'); });
  sw.postMessage({ type: 'novashare-dl', token, name, size: total, stream: ts.readable, port: ch.port2 }, [ts.readable, ch.port2]);
  await Promise.race([q, late('helper did not answer')]);     // the worker holds the stream BEFORE the link is opened
  // a hidden same-origin frame navigates to the link: navigations always reach the worker (tested 2026-11-24:
  // Chrome sent an <a download> click straight to the network instead, which 404s)
  const fr = el('iframe', { src: '/relay/dl/' + token, hidden: '', title: 'download', tabindex: '-1' }); document.body.append(fr);
  const unframe = () => setTimeout(() => fr.remove(), 30000);
  try { await Promise.race([st, late('did not start')]); } catch (e) { fr.remove(); throw e; }
  const ping = setInterval(() => { try { sw.postMessage({ type: 'novashare-ping' }); } catch (e) { /* gone */ } }, 10000);
  return { kind: 'download', savedAs: name, where: 'Downloads', write: (u8) => writer.write(u8),
    close: async () => { clearInterval(ping); await writer.close(); unframe(); },
    abort: async () => { clearInterval(ping); try { await writer.abort(new Error('did not pass the check')); } catch (e) { /* errored */ } unframe(); } };
}
function blobSink(name, total) {
  if (total > BLOB_MAX) throw new Error('This browser cannot save big files straight to disk. Open the inbox in Chrome or Edge to save files over ' + size(BLOB_MAX) + '.');
  let parts = [];
  return { kind: 'blob', savedAs: name, where: 'Downloads', write: async (u8) => { parts.push(u8); },
    close: async () => { const url = URL.createObjectURL(new Blob(parts, { type: 'application/octet-stream' })); parts = [];
      const a = el('a', { href: url, download: name, hidden: '' }); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000); },
    abort: async () => { parts = []; } };
}
async function openSink(man) {
  const name = safeName(man.name);
  if (S.mode !== 'downloads') {
    if (S.dir) return folderSink(S.dir, name);
    if (canFolder()) { await chooseFolder(); return folderSink(S.dir, name); }
    if (canPicker()) return pickerSink(name);
  }
  if (canSW()) return swSink(name, man.size);
  return blobSink(name, man.size);
}
async function chooseFolder() {
  const dir = await globalThis.showDirectoryPicker({ id: 'novashare-inbox', mode: 'readwrite', startIn: 'downloads' });
  S.dir = dir; S.mode = 'folder'; await kvSet('savedir', dir); await kvSet('savemode', 'folder');
  renderSaveTo();
}

// ---------- receive one transfer
async function pump(o, sink, onProgress) {
  const id = o.env.id, n = o.env.pieces;
  const want = (i) => (i < n - 1 ? CT : o.env.size_ct - (n - 1) * CT);
  const q = new Map();
  async function fetchOne(i) {
    for (let k = 0; ; k++) {
      try {
        const ct = await apiBytes('/relay/api/transfers/' + id + '/pieces/' + i);
        if (ct.length !== want(i)) throw tamper('Piece ' + (i + 1) + ' of ' + n + ' has the wrong length.');
        return ct;
      } catch (e) {
        const again = !e.status || e.status >= 500 || e.status === 429;
        if (e.code === 'tampered' || !again || k >= TRIES - 1) throw e;
        await sleep(Math.min(15000, 500 * 2 ** k));
      }
    }
  }
  const start = (i) => { if (i < n && !q.has(i)) { const p = fetchOne(i); p.catch(() => {}); q.set(i, p); } };
  for (let i = 0; i < Math.min(AHEAD, n); i++) start(i);
  let total = 0;
  for (let i = 0; i < n; i++) {
    const ct = await q.get(i); q.delete(i); start(i + AHEAD);
    let pt;
    try { pt = new Uint8Array(await crypto.subtle.decrypt({ name: 'AES-GCM', iv: cat(o.prefix, u32(i)), additionalData: pieceAad(id, i, i === n - 1), tagLength: 128 }, o.K, ct)); }
    catch (e) { throw tamper('Piece ' + (i + 1) + ' of ' + n + ' did not pass the check (changed, swapped or cut short).'); }
    await sink.write(pt); total += pt.length;
    onProgress(total, i + 1, n);
  }
  if (total !== o.man.size) throw tamper('The file came out a different size than the sender said.');
}

async function accept(item, ui) {
  if (S.busy) return;
  const o = S.opened.get(item.id); if (!o) return;
  S.busy = true; document.body.dataset.ready = '0'; ui.setBusy(true); ui.say('Getting ready...');
  let sink = null;
  try {
    sink = await openSink(o.man);                       // first: the folder permission / picker need this click
    await api('/relay/api/transfers/' + item.id + '/accept', { method: 'POST' });
    ui.say('Unlocking and saving to ' + sink.where + '...');
    const t0 = performance.now();
    await pump(o, sink, (done, k, n) => ui.progress(done, o.man.size, 'Piece ' + k + ' of ' + n + ', ' + size(done) + ' of ' + size(o.man.size) + ' in ' + ((performance.now() - t0) / 1000).toFixed(0) + ' s'));
    await sink.close();                                 // finalized only now, after every check passed
    const got = { at: Date.now(), name: sink.savedAs, where: sink.where };
    await kvSet('got:' + item.id, got); SAVED.set(item.id, got);
    sink = null;
    await api('/relay/api/transfers/' + item.id + '/complete', { method: 'POST' });
    ui.saved(o.man.size);
  } catch (e) {
    if (sink) { try { await sink.abort(); } catch (e2) { /* best effort */ } }
    ui.error(e.code === 'tampered' ? 'Not saved: ' + e.message + ' The unfinished file was removed. Ask the sender to send it again.' : (e.name === 'AbortError' ? 'Not saved: you closed the picker.' : 'Not saved: ' + e.message), e.code || e.name || '');
  } finally { S.busy = false; document.body.dataset.ready = '1'; ui.setBusy(false); }
}
async function decline(item, ui) {
  if (S.busy) return;
  S.busy = true; ui.setBusy(true);
  try { await api('/relay/api/transfers/' + item.id + '/decline', { method: 'POST' }); S.opened.delete(item.id); await refresh(); }
  catch (e) { ui.error('Could not decline: ' + e.message, e.code || ''); }
  finally { S.busy = false; ui.setBusy(false); }
}

// ---------- page
function alertBox(msg, code) { $('alerts').replaceChildren(el('div', { class: 'card alert', 'data-kind': 'error', 'data-code': code || '' }, el('strong', { text: 'Problem. ' }), msg)); }

function renderSaveTo() {
  const box = $('saveto'); box.replaceChildren();
  let line;
  if (S.mode !== 'downloads' && S.dir) line = el('p', {}, 'Saving into the folder ', el('strong', { 'data-folder': S.dir.name, text: S.dir.name }), ' without asking each time.' + (/Windows/.test(navigator.userAgent) ? ' Windows Defender checks each file as it is saved.' : ''));
  else if (S.mode !== 'downloads' && canFolder()) line = el('p', {}, 'Pick a folder once (for example ', el('span', { class: 'mono', text: 'D:\\Novashare\\Inbox' }), '); later saves go there without asking.');
  else if (S.mode !== 'downloads' && canPicker()) line = el('p', { text: 'Each file asks where to save it.' });
  else if (canSW()) line = el('p', { text: "Files are saved to this browser's Downloads as they are unlocked." });
  else line = el('p', { text: 'This browser can only save files up to ' + size(BLOB_MAX) + '.' });
  box.append(line);
  const row = el('div', { class: 'row start' });
  if (canFolder()) row.append(el('button', { type: 'button', id: 'pick-folder', class: S.dir && S.mode !== 'downloads' ? 'quiet' : '', onclick: async () => { try { await chooseFolder(); } catch (e) { if (e.name !== 'AbortError') alertBox(e.message); } } }, S.dir ? 'Change folder' : 'Choose folder'));
  if ((canFolder() || canPicker()) && canSW()) row.append(el('button', { type: 'button', id: 'use-downloads', class: 'quiet', 'aria-pressed': String(S.mode === 'downloads'),
    onclick: async () => { S.mode = S.mode === 'downloads' ? 'folder' : 'downloads'; await kvSet('savemode', S.mode); renderSaveTo(); } }, S.mode === 'downloads' ? 'Use the folder again' : 'Use Downloads instead'));
  if (row.childNodes.length) box.append(row);
}

const WORD = { pending: 'New', accepted: 'Accepted, not saved yet', downloaded: 'Saved', declined: 'Declined', gone: 'No longer on the server' };

function itemCard(item) {
  const li = el('li', { class: 'card item', 'data-tid': item.id, 'data-state': item.state });
  const title = el('p', { class: 'fname', text: 'Unlocking the name...' });
  const meta = el('p', { class: 'dim' }, 'From ', el('strong', { text: item.sender.person === S.me ? 'you' : item.sender.name }), ' (' + item.sender.label + '), ' + size(item.size) + ', ' + when(item.created));
  const note = el('p', { class: 'note', hidden: '' });
  const bar = el('progress', { max: '1', value: '0', hidden: '', 'aria-label': 'Saving progress' });
  const status = el('p', { class: 'status', role: 'status', 'data-status': '' });
  const btnA = el('button', { type: 'button', 'data-act': 'accept', disabled: '' }, 'Accept and save');
  const btnD = el('button', { type: 'button', class: 'quiet', 'data-act': 'decline' }, 'Decline');
  let armed = null;
  const ui = {
    say: (t) => { status.textContent = t; status.className = 'status'; },
    setBusy: (b) => { btnA.disabled = b || !S.opened.has(item.id); btnD.disabled = b; },
    progress: (done, all, t) => { bar.hidden = false; bar.max = Math.max(all, 1); bar.value = done; status.textContent = t; },
    saved: () => { li.dataset.state = 'downloaded'; bar.value = bar.max; li.classList.add('done');
      btnA.remove(); btnD.remove(); status.className = 'status ok'; status.dataset.saved = '1'; status.textContent = 'Saved. ' + savedLine(item.id); },
    error: (t, code) => { status.className = 'status bad'; status.dataset.code = code; status.textContent = t; bar.hidden = true; },
  };
  btnA.addEventListener('click', () => accept(item, ui));
  btnD.addEventListener('click', () => {
    if (!armed) { btnD.textContent = 'Yes, decline'; btnD.classList.add('warn'); armed = setTimeout(() => { armed = null; btnD.textContent = 'Decline'; btnD.classList.remove('warn'); }, 6000); return; }
    clearTimeout(armed); armed = null; decline(item, ui);
  });
  li.append(title, meta, note, bar, status, el('div', { class: 'row start' }, btnA, btnD));
  return { li, title, note, ui, btnA };
}
const SAVED = new Map();
function savedLine(id) { const g = SAVED.get(id); return g ? 'It is "' + g.name + '" in ' + g.where + '.' : ''; }

async function unlockInto(item, card) {
  try {
    let o = S.opened.get(item.id);
    if (!o) {
      const env = await api('/relay/api/transfers/' + item.id + '/envelope');
      const why = await senderProblem(env, item);
      if (why) { card.title.textContent = 'Locked'; card.ui.error(why, 'untrusted_sender'); return; }
      o = await openEnvelope(env); S.opened.set(item.id, o);
    }
    card.title.textContent = o.man.name || '(no name)';
    card.title.title = o.man.type || '';
    if (o.man.caption) { card.note.hidden = false; card.note.textContent = o.man.caption; }
    card.btnA.disabled = S.busy;
    if (item.state === 'accepted') card.ui.say('You accepted this but it is not saved yet. Accept again to save it.');
  } catch (e) {
    card.title.textContent = 'Locked';
    card.ui.error(e.code === 'tampered' ? e.message + ' Decline it and ask the sender to send it again.' : 'Could not unlock: ' + e.message, e.code || '');
  }
}

async function refresh() {
  const r = await api('/relay/api/inbox');
  const open = r.items.filter((x) => x.open), old = r.items.filter((x) => !x.open);
  const ul = $('inbox'); ul.replaceChildren();
  if (!open.length) ul.append(el('li', { class: 'dim empty', text: 'Nothing waiting. When someone sends you something it shows up here.' }));
  for (const item of open) {
    const g = await kvGet('got:' + item.id);
    if (g) {                                            // saved before, but "complete" did not reach the server
      try { await api('/relay/api/transfers/' + item.id + '/complete', { method: 'POST' }); } catch (e) { /* retried next time */ }
      continue;
    }
    const card = itemCard(item); ul.append(card.li); await unlockInto(item, card);
  }
  const dl = $('done'); dl.replaceChildren();
  for (const item of old) {
    const g = await kvGet('got:' + item.id); if (g) SAVED.set(item.id, g);
    dl.append(el('li', { class: 'card item done', 'data-tid': item.id, 'data-state': item.state },
      el('p', { class: 'fname', text: g ? g.name : (item.state === 'declined' ? 'Declined' : 'Locked') }),
      el('p', { class: 'dim' }, 'From ', el('strong', { text: item.sender.person === S.me ? 'you' : item.sender.name }), ', ' + size(item.size) + ', ' + when(item.created) + '. ' + (WORD[item.state] || item.state) + '.')));
  }
  if (!old.length) dl.append(el('li', { class: 'dim empty', text: 'Nothing yet.' }));
}

async function main() {
  document.body.dataset.ready = '0';
  try {
    S.local = (await kvGet('device')) || null;
    if (!S.local || !S.local.x) { $('support').textContent = 'This browser is not one of your devices yet. Set it up on the Devices page (/devices) first.'; return; }
    if (S.local.mlkem && S.local.mlkem.mode !== 'native' && !globalThis.NovashareMLKEM) throw new Error('The ML-KEM code did not load (integrity check failed or blocked), so this device cannot unlock anything.');
    const mine = await api('/devices/api/mine');
    S.me = mine.person;
    const row = mine.devices.find((d) => d.id === S.local.device_id);
    if (!row || row.revoked != null) { $('support').textContent = 'This device was removed. Set it up again on the Devices page.'; return; }
    S.people = (await api('/devices/api/people')).people;
    S.dir = (await kvGet('savedir')) || null; S.mode = (await kvGet('savemode')) || 'auto';
    $('support').textContent = 'This device: ' + S.local.label + '. Only this device can unlock what was sent to it.';
    renderSaveTo();
    if (canSW() && !canFolder() && !canPicker()) swReady().catch(() => {});
    await refresh();
    setInterval(() => { if (!S.busy) refresh().catch(() => {}); }, 30000);
  } catch (e) { alertBox(e.message || String(e), e.code); } finally { document.body.dataset.ready = '1'; }
}
main();
"""

CSS = r"""/* inbox.css - Novashare Inbox (receive_js.py, E2E Step 3). On top of devices.css (horizon tokens). */
.row.start{justify-content:flex-start}
.item .fname{font-size:1.12rem;font-weight:650;margin:0 0 2px;overflow-wrap:anywhere}
.item .note{border-left:3px solid var(--accent);padding:2px 0 2px 10px;margin:8px 0;overflow-wrap:anywhere;white-space:pre-wrap}
.item .status{margin:8px 0 4px;min-height:1.5em}
.item .status:empty{display:none}
.item.done{opacity:.85}
.item.done .fname{font-size:1rem}
.item[data-state=pending]{border-color:var(--accent)}
progress[hidden]{display:none}
progress{display:block;width:100%;height:10px;margin:12px 0 2px;border:0;border-radius:999px;overflow:hidden;
  background:var(--line);appearance:none;-webkit-appearance:none}
progress::-webkit-progress-bar{background:var(--line);border-radius:999px}
progress::-webkit-progress-value{background:var(--accent);border-radius:999px;transition:width .2s linear}
progress::-moz-progress-bar{background:var(--accent);border-radius:999px}
.item.done progress::-webkit-progress-value{background:var(--ok)}
.saveto p{margin:4px 0 10px}
.empty{padding:6px 2px}
a{color:var(--accent)}
a:focus-visible{outline:3px solid var(--warn);outline-offset:2px}
@media (prefers-reduced-motion:reduce){progress::-webkit-progress-value{transition:none}}
"""

SW = r"""// sw.js - Novashare download helper (receive_js.py, E2E Step 3, 2026-11-24). Scope /relay/.
// It only answers GET /relay/dl/<token>, from a stream the Inbox page handed it a moment before (already decrypted
// and checked piece by piece on the page). Everything else goes to the network untouched. No caching, no imports.
const PENDING = new Map();
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
function ascii(n) { return n.replace(/[^\x20-\x7e]|["\\]/g, '_'); }
self.addEventListener('message', (e) => {
  const d = e.data || {};
  if (d.type !== 'novashare-dl' || typeof d.token !== 'string' || !/^[A-Za-z0-9_-]{22}$/.test(d.token) || !(d.stream instanceof ReadableStream)) return;
  PENDING.set(d.token, { stream: d.stream, name: String(d.name || 'novashare-file'), size: Number(d.size), port: d.port });
  setTimeout(() => PENDING.delete(d.token), 60000);        // never picked up: forget it
  if (d.port) d.port.postMessage('queued');
});
self.addEventListener('fetch', (e) => {
  const u = new URL(e.request.url);
  if (u.origin !== self.location.origin || !u.pathname.startsWith('/relay/dl/')) return;
  const t = u.pathname.slice('/relay/dl/'.length), p = PENDING.get(t);
  PENDING.delete(t);
  if (!p) { e.respondWith(new Response('This download link has expired. Go back to the Inbox and press Accept again.', { status: 404, headers: { 'Content-Type': 'text/plain; charset=utf-8' } })); return; }
  if (p.port) p.port.postMessage('started');
  const h = { 'Content-Type': 'application/octet-stream', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
    'Content-Disposition': "attachment; filename=\"" + ascii(p.name) + "\"; filename*=UTF-8''" + encodeURIComponent(p.name) };
  if (Number.isSafeInteger(p.size) && p.size >= 0) h['Content-Length'] = String(p.size);
  e.respondWith(new Response(p.stream, { headers: h }));
});
"""
