"""push_js.py - the Novashare "Notify me on this device" script (E2E Step 4, 2026-11-24).

Served by push.py at /relay/push/push.js (SRI in the Devices and Inbox pages, which both carry <div id=push-box>). Kept in
a .py because SWAP-PATCH.sh installs *.py files only. Uses the Step 1 device record from IndexedDB ("novashare-devices",
key "device") for the device id, and the Step 3 service worker /relay/sw.js (scope /relay/), which now also shows
pushes (receive_js.SW).

What it does:
  - Feature-detects push (service worker + PushManager + Notification). On iPhone / iPad outside a Home Screen web app
    there is no PushManager: the box says how to add Novashare to the Home Screen (iOS / iPadOS 16.4+) instead.
  - Shows the state of THIS device: on, off, blocked in the browser, or "set up this device first".
  - The permission prompt appears ONLY after the tap on "Notify me on this device" (never on page load).
  - Subscribes with the server's public VAPID key (/relay/api/push/key), then PUTs the subscription (endpoint + keys)
    for this device. "Turn off" unsubscribes in the browser and DELETEs it on the server. "Send a test" queues one push.
  - If the server dropped the subscription (revoked, expired at the push service, VAPID key changed), the button comes
    back; a tap makes a fresh subscription (the old one is unsubscribed first).
No outside libraries; DOM via createElement/textContent only (the pages require Trusted Types; the one script URL, the
worker, goes through a policy that allows exactly '/relay/sw.js').
"""

JS = r"""// push.js - Novashare notifications for this device (push_js.py, E2E Step 4, 2026-11-24). No outside libraries.
const P = { local: null, key: null, reg: null, busy: false, tt: null };
const box = () => document.getElementById('push-box');
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
  enc(u8) { let s = ''; for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]); return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); },
  dec(s) { const t = atob(String(s).replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((String(s).length + 3) % 4)); return Uint8Array.from(t, (c) => c.charCodeAt(0)); },
};
function idbGet(k) {
  return new Promise((res, rej) => {
    const r = indexedDB.open('novashare-devices', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('kv');
    r.onerror = () => rej(r.error);
    r.onsuccess = () => { const db = r.result; const q = db.transaction('kv', 'readonly').objectStore('kv').get(k);
      q.onsuccess = () => { db.close(); res(q.result); }; q.onerror = () => { db.close(); rej(q.error); }; };
  });
}
async function api(path, method, body) {
  const headers = { 'X-Novashare': '1' };
  if (P.local) headers['X-Device-Id'] = P.local.device_id;
  if (body) headers['Content-Type'] = 'application/json';
  const r = await fetch(path, { method: method || 'GET', headers, credentials: 'same-origin', cache: 'no-store', body: body ? JSON.stringify(body) : undefined });
  let data = null; try { data = await r.json(); } catch (e) { data = null; }
  if (!r.ok) { const e = new Error((data && data.error) || ('The server answered ' + r.status + '.')); e.code = data && data.code; throw e; }
  return data;
}
const ios = () => /iP(hone|ad|od)/.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
const standalone = () => navigator.standalone === true || (globalThis.matchMedia && matchMedia('(display-mode: standalone)').matches);
const canPush = () => !!(navigator.serviceWorker && globalThis.PushManager && globalThis.Notification);

function show(lines, buttons, kind) {
  const b = box(); if (!b) return;
  b.replaceChildren(); b.dataset.push = kind || '';
  for (const l of lines) b.append(typeof l === 'string' ? el('p', { text: l }) : l);
  if (buttons && buttons.length) b.append(el('div', { class: 'row start' }, ...buttons));
}
function btn(text, fn, cls) {
  const b = el('button', { type: 'button', class: cls || '' }, text);
  b.addEventListener('click', async () => {
    if (P.busy) return; P.busy = true; b.disabled = true;
    try { await fn(); } catch (e) { show([el('p', { class: 'bad', text: e.message || String(e) })], [btn('Try again', render)], 'error'); }
    finally { P.busy = false; b.disabled = false; }
  });
  return b;
}

async function registration() {
  if (P.reg) return P.reg;
  const tt = globalThis.trustedTypes ? (P.tt = P.tt || trustedTypes.createPolicy('novashare-push', { createScriptURL: (u) => { if (u !== '/relay/sw.js') throw new Error('blocked'); return u; } })) : null;
  const reg = await navigator.serviceWorker.register(tt ? tt.createScriptURL('/relay/sw.js') : '/relay/sw.js', { scope: '/relay/' });
  // navigator.serviceWorker.ready only covers THIS page's scope (/devices is outside /relay/): wait on the worker itself
  const w = reg.installing || reg.waiting || reg.active;
  if (w && w.state !== 'activated') await Promise.race([new Promise((r) => w.addEventListener('statechange', () => { if (w.state === 'activated') r(); })), new Promise((r) => setTimeout(r, 10000))]);
  P.reg = reg; return reg;
}
async function browserSub() {
  const reg = await navigator.serviceWorker.getRegistration('/relay/');
  return reg ? reg.pushManager.getSubscription() : null;
}
function sameKey(sub) {
  try { const k = sub.options && sub.options.applicationServerKey; return !k || b64u.enc(new Uint8Array(k)) === P.key.public_key; } catch (e) { return true; }
}

async function turnOn() {
  const perm = await Notification.requestPermission();    // only here: after the tap
  if (perm !== 'granted') { await render(); return; }
  const reg = await registration();
  let sub = await reg.pushManager.getSubscription();
  if (sub && !sameKey(sub)) { await sub.unsubscribe(); sub = null; }
  if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64u.dec(P.key.public_key) });
  const j = sub.toJSON();
  await api('/relay/api/push/sub', 'PUT', { endpoint: j.endpoint, keys: { p256dh: j.keys.p256dh, auth: j.keys.auth } });
  await render('Notifications are on. Send a test to check.');
}
async function turnOff() {
  const sub = await browserSub();
  if (sub) { try { await sub.unsubscribe(); } catch (e) { /* the server row goes anyway */ } }
  await api('/relay/api/push/sub', 'DELETE');
  await render('Notifications are off for this device.');
}
async function test() {
  await api('/relay/api/push/test', 'POST');
  await render('A test notification is on its way. If nothing shows within a minute, check the system notification settings for this browser.');
}

async function render(note) {
  if (!box()) return;
  const notes = note ? [el('p', { class: 'ok', text: note })] : [];
  if (!canPush()) {
    if (ios() && !standalone()) return show(['On iPhone and iPad, notifications only work when Novashare is on the Home Screen (iOS 16.4 or later).', 'In Safari: tap Share, then "Add to Home Screen". Open Novashare from that icon, then come back here.'], [], 'ios');
    return show(['This browser cannot show notifications from Novashare.'], [], 'unsupported');
  }
  P.local = P.local || (await idbGet('device')) || null;
  if (!P.local || !P.local.device_id) return show(['Set up this device on the Devices page first; then it can be notified.'], [], 'no-device');
  P.key = P.key || (await api('/relay/api/push/key'));
  if (!P.key.enabled) return show(['Notifications are switched off on the server.'], [], 'off-server');
  const st = await api('/relay/api/push/sub');
  const sub = await browserSub();
  const perm = Notification.permission;
  if (perm === 'denied') return show(['Notifications are blocked for this site in the browser settings. Allow them there, then reload.'], [], 'denied');
  if (st.subscribed && st.current_key && sub && perm === 'granted')
    return show([...notes, 'This device is notified when something is sent to you, and when something you sent is saved or declined. The notification never shows names or file names.'],
      [btn('Send a test', test, 'quiet'), btn('Turn off', turnOff, 'quiet')], 'on');
  show([...notes, 'Get a notification on this device when someone sends you something. It never shows names or file names; you open it here to see what it is.'],
    [btn('Notify me on this device', turnOn)], 'ready');
}

render().catch((e) => show([el('p', { class: 'bad', text: 'Notifications: ' + (e.message || String(e)) })], [], 'error'));
"""
