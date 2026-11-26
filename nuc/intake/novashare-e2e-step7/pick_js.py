"""pick_js.py - the Novashare share picker's script and style (E2E Step 5, 2026-11-24), served by pick.py.

Kept in a .py because SWAP-PATCH.sh installs *.py files only; the page loads both with SRI hashes pick.py computes.
The page loads, in order: the vendored ML-KEM bundle, relay.js (Step 2's send engine, exported as
globalThis.NovashareSend since Step 5) and this script. No crypto lives here: People and My devices call
NovashareSend.newSend / upload exactly like the Send page, so the wire format and every crypto rule stay Step 2's.

Where a share lives (IndexedDB, on the phone only):
  "novashare-pick" / pending   the undecided share, written by the drop's service worker (atomic_ui.SW, Step 5).
  "atomic-share" / pending     the library queue that /sending sends. Only "Library" copies a record there, so
                               /sending (unchanged) never sees a share meant for a person.
A send to people records which files are done (rec.sent) after each one, so a reload or a lost signal carries on
with the rest (relay.js resumes the unfinished file itself); the record is deleted once every file is sent.
All text from the phone or the server goes in with textContent; the page makes no HTML from strings (Trusted Types).
Choices: People are checkboxes (several), the rest are radios (one); picking one kind clears the other.
OUTCOME: test_pick_browser.py (2 files shared -> person -> received byte for byte; -> my device; -> library).
"""

JS = r"""// pick.js - Novashare share picker (pick_js.py, E2E Step 5, 2026-11-24).
'use strict';
const NS = globalThis.NovashareSend;
const $ = (id) => document.getElementById(id);
const P = { rec: null, info: null, people: [], me: null, mine: [], meT: null, ready: false, busy: false, e2e: '' };
const SUMMARY_MAX = 80;

function el(tag, attrs, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v; else e.setAttribute(k, v === true ? '' : v);
  }
  for (const k of kids) if (k != null) e.append(k instanceof Node ? k : document.createTextNode(String(k)));
  return e;
}
const size = (n) => n >= 1073741824 ? (n / 1073741824).toFixed(2).replace(/\.?0+$/, '') + ' GB' : n >= 1048576 ? (n / 1048576).toFixed(1) + ' MB' : n >= 1024 ? Math.ceil(n / 1024) + ' KB' : n + ' bytes';
const plural = (n, one, many) => n + ' ' + (n === 1 ? one : many);
function say(t) { $('status').textContent = t; }
function alertBox(t, code) {
  $('alerts').replaceChildren(t ? el('div', { class: 'card alert', 'data-code': code || '' }, el('strong', { text: 'Not sent. ' }), t) : '');
}

// ---------- the phone's own copy of the share (IndexedDB)
function openDb(name) {
  return new Promise((ok, no) => {
    const r = indexedDB.open(name, 1);
    r.onupgradeneeded = () => r.result.createObjectStore('pending', { keyPath: 'key' });
    r.onsuccess = () => ok(r.result); r.onerror = () => no(r.error);
  });
}
async function tx(name, mode, fn) {
  const d = await openDb(name);
  return new Promise((ok, no) => {
    const t = d.transaction('pending', mode); const rq = fn(t.objectStore('pending'));
    t.oncomplete = () => { d.close(); ok(rq ? rq.result : undefined); }; t.onerror = () => { d.close(); no(t.error); };
  });
}
const PICK = 'novashare-pick', LIB = 'atomic-share';
async function loadRec() {
  const k = new URLSearchParams(location.search).get('k');
  if (k) { const r = await tx(PICK, 'readonly', (s) => s.get(k)); if (r) return r; }
  const all = (await tx(PICK, 'readonly', (s) => s.getAll())) || [];
  all.sort((a, b) => b.created - a.created);
  return all[0] || null;
}
const saveRec = (r) => tx(PICK, 'readwrite', (s) => s.put(r));
const dropRec = (k) => tx(PICK, 'readwrite', (s) => s.delete(k));

// What gets sent: the shared files, or (a link or text alone) one small text file made here.
function asFile(f, created) {
  const b = f.blob;
  return b instanceof File ? b : new File([b], f.name || 'shared', { type: f.type || (b && b.type) || '', lastModified: created });
}
function sharedText(r) { return [r.title, r.text, r.url].map((x) => String(x || '').trim()).filter(Boolean).filter((x, i, a) => a.indexOf(x) === i).join('\n'); }
function files(r) {
  if (r.files && r.files.length) return r.files.map((f) => asFile(f, r.created));
  const t = sharedText(r);
  const name = (r.url || /https?:\/\//.test(t) ? 'Shared link' : 'Shared text') + '.txt';
  return [new File([t + '\n'], name, { type: 'text/plain', lastModified: r.created })];
}
const totalBytes = (r) => files(r).reduce((n, f) => n + f.size, 0);
const media = (r) => (r.files || []).filter((f) => /^(image|video)\//.test(String(f.type || (f.blob && f.blob.type) || '')));

// ---------- what is being shared
function renderItems() {
  const r = P.rec, ul = $('items'); ul.replaceChildren();
  if (r.files && r.files.length) {
    r.files.forEach((f, i) => ul.append(el('li', { class: 'item', 'data-i': String(i) },
      el('span', { class: 'name', text: f.name || 'shared' }), el('span', { class: 'sz', text: size(f.size || (f.blob && f.blob.size) || 0) }),
      el('div', { class: 'bar', hidden: true, role: 'progressbar', 'aria-label': 'Sent so far: ' + (f.name || 'file'), 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': '0' }, el('i')))));
  } else {
    const t = sharedText(r);
    ul.append(el('li', { class: 'item', 'data-i': '0' }, el('span', { class: 'name', text: t.length > SUMMARY_MAX ? t.slice(0, SUMMARY_MAX) + '...' : t || 'Shared text' }),
      el('span', { class: 'sz', text: r.url || /https?:\/\//.test(t) ? 'link' : 'text' }),
      el('div', { class: 'bar', hidden: true, role: 'progressbar', 'aria-label': 'Sent so far', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': '0' }, el('i'))));
  }
  const n = r.files && r.files.length ? r.files.length : 0;
  $('total').textContent = n ? plural(n, 'file', 'files') + ', ' + size(totalBytes(r)) + ' in all' : (r.url ? 'A link' : 'Some text');
}

// ---------- the choices
function option(type, name, id, value, title, note, disabled, data) {
  const nid = id + '-n';
  const input = el('input', { type, name, id, value, disabled, 'aria-describedby': note ? nid : null, ...data });
  return el('label', { class: 'opt', for: id }, input,
    el('span', {}, el('span', { class: 't', text: title }), note ? el('small', { id: nid, text: note }) : null));
}
function e2eBlock() {
  // Why People / My devices cannot be used right now (null = they can).
  const L = P.info.limits, big = files(P.rec).find((f) => f.size > L.max_bytes);
  if (!P.e2eOK) return P.e2eWhy;
  if (big) return big.name + ' is bigger than the ' + size(L.max_bytes) + ' limit for a locked send.';
  if (totalBytes(P.rec) > L.open_left_bytes) return 'Too much of yours is still waiting to be picked up (' + size(L.open_left_bytes) + ' room left). Let some sends finish first.';
  if (files(P.rec).length > L.open_left_sends) return 'Too many of your sends are still open. Let some finish first.';
  return null;
}
function renderChoices() {
  const block = e2eBlock();
  const ppl = $('people'); ppl.replaceChildren();
  const others = P.people.filter((x) => !x.p.me);
  if (!others.length) ppl.append(el('p', { class: 'dim', text: 'No one else in the household yet.' }));
  for (const { p, t } of others) {
    const why = block || (t.ok ? null : t.why);
    ppl.append(option('checkbox', 'person', 'p-' + p.id, p.id, p.name,
      why || plural(t.devs.length, 'device', 'devices') + (t.verified ? '' : ', not verified'), !!why, { 'data-person': p.id }));
  }
  const mine = $('mine'); mine.replaceChildren();
  if (!P.mine.length) mine.append(el('p', { class: 'dim', text: P.e2eOK ? 'You have no other device set up. Set one up on its Devices page.' : (block || '') }));
  for (const d of P.mine) {
    const why = block || (P.meT && P.meT.ok ? null : (P.meT ? P.meT.why : 'Not ready.'));
    mine.append(option('radio', 'solo', 'd-' + d.id, 'device', d.label || 'My device', why || (d.platform || 'Device'), !!why, { 'data-device': d.id }));
  }
  if (block && !P.e2eOK && P.e2eLink) $('n-people').replaceChildren(el('span', { text: block + ' ' }), el('a', { href: '/devices', text: 'Set up this device' }));
  const lib = $('d-library'), bigLib = files(P.rec).find((f) => f.size > P.info.limits.max_bytes);
  lib.disabled = !!bigLib;
  if (bigLib) $('n-lib').textContent = bigLib.name + ' is bigger than the ' + size(P.info.limits.max_bytes) + ' limit.';
  const ab = P.info.abyss;
  if (ab) {
    const m = media(P.rec), sum = m.reduce((n, f) => n + (f.size || 0), 0);
    const why = !m.length ? 'Photos and videos only.' : sum > ab.max_bytes ? 'Too big for Abyss: ' + size(ab.max_bytes) + ' at most in all.' : null;
    $('abyss-slot').replaceChildren(option('radio', 'solo', 'd-abyss', 'abyss', 'Send as Abyss',
      why || 'Opens Abyss with ' + plural(m.length, 'photo or video', 'photos and videos') + '; Abyss locks them before upload.', !!why));
  }
  $('limits').textContent = 'Limits: ' + size(P.info.limits.max_bytes) + ' a file. Up to ' + P.info.limits.max_people + ' people at once. Room left for your locked sends: ' + size(P.info.limits.open_left_bytes) + '.';
}
function choice() {
  const ppl = [...document.querySelectorAll('input[name=person]:checked')].map((i) => i.value);
  if (ppl.length) return { kind: 'people', people: ppl };
  const r = document.querySelector('input[name=solo]:checked');
  if (!r) return null;
  if (r.value === 'device') return { kind: 'device', device: r.dataset.device };
  return { kind: r.value };
}
function nameOf(id) { const x = P.people.find((y) => y.p.id === id); return x ? x.p.name : id; }
function label(c) {
  if (!c) return 'Pick where to send it';
  if (c.kind === 'people') return c.people.length > 2 ? 'Send to ' + c.people.length + ' people' : 'Send to ' + c.people.map(nameOf).join(' and ');
  if (c.kind === 'device') { const d = P.mine.find((x) => x.id === c.device); return 'Send to ' + (d ? d.label : 'my device'); }
  if (c.kind === 'library') return 'File in my library';
  return 'Send as Abyss';
}
function update(ev) {
  const t = ev && ev.target;
  if (t && t.name === 'person' && t.checked) document.querySelectorAll('input[name=solo]').forEach((i) => { i.checked = false; });
  if (t && t.name === 'solo' && t.checked) document.querySelectorAll('input[name=person]').forEach((i) => { i.checked = false; });
  const c = choice();
  const n = c && c.kind === 'people' ? c.people.length : 0;
  if (n > P.info.limits.max_people && t && t.name === 'person') { t.checked = false; say('Up to ' + P.info.limits.max_people + ' people at once.'); return update(); }
  $('send').textContent = label(c);
  $('send').disabled = P.busy;                               // enabled with no choice: go() says what is missing
  $('send').dataset.kind = c ? c.kind : '';
}
function preselect(last) {
  if (!last) return;
  const ok = (i) => i && !i.disabled;
  if (last.kind === 'people') { for (const id of last.people) { const i = $('p-' + id); if (ok(i)) i.checked = true; } }
  else if (last.kind === 'device') { const i = $('d-' + last.device); if (ok(i)) i.checked = true; }
  else { const i = $('d-' + last.kind); if (ok(i)) i.checked = true; }
}

// ---------- sending
function bar(i, frac) {
  const b = document.querySelector('#items li[data-i="' + i + '"] .bar'); if (!b) return;
  b.hidden = false; b.firstElementChild.style.transform = 'scaleX(' + Math.max(0, Math.min(1, frac)).toFixed(4) + ')';
  b.setAttribute('aria-valuenow', String(Math.round(frac * 100)));
}
function same(body, to, only) {
  const a = (x) => JSON.stringify([...(x || [])].sort());
  return a(body.to) === a(to) && a(body.only) === a(only);
}
async function sendLocked(c) {
  let targets, only = null;
  if (c.kind === 'people') targets = c.people.map((id) => { const x = P.people.find((y) => y.p.id === id); return { id, devs: x.t.devs }; });
  else { targets = [{ id: P.me, devs: P.meT.devs.filter((d) => d.id === c.device) }]; only = [c.device]; }
  if (targets.some((t) => !t.devs.length)) throw new Error('That device is not ready. Reload and try again.');
  const r = P.rec, list = files(r), caption = r.files && r.files.length ? sharedText(r).slice(0, 500) : (r.title || '').slice(0, 500);
  r.sent = r.sent || {};
  const to = targets.map((t) => t.id);
  for (let i = 0; i < list.length; i++) {
    const f = list[i];
    if (r.sent[i]) { bar(i, 1); continue; }
    say('Locking and sending ' + f.name + ' (' + (i + 1) + ' of ' + list.length + ')...');
    NS.S.onPiece = (done, n) => bar(i, done / n);
    let st = await NS.pendingFor(f);
    if (st && !same(st.body, to, only)) st = null;           // an unfinished send of this file to someone else
    if (!st) st = await NS.newSend(f, caption, targets, only);
    await NS.upload(st, f);
    bar(i, 1);
    r.sent[i] = st.id; await saveRec(r);
  }
  await dropRec(r.key);
  return list.length;
}
async function toLibrary() {
  const r = { ...P.rec }; delete r.sent;
  await tx(LIB, 'readwrite', (s) => s.put(r));              // the library queue /sending sends, as before
  await dropRec(r.key);
  document.body.dataset.done = 'library';
  location.assign('/sending?k=' + encodeURIComponent(r.key));
}
function toAbyss() {
  // As /sending's "Send as Abyss" (Abyss step 5): Abyss says {type:'abyss-ready'} first; only a message from Abyss's
  // exact origin AND that window counts; the bytes go as transferable ArrayBuffers to that origin only.
  const to = new URL(P.info.abyss.url).origin, m = media(P.rec);
  const w = window.open(to + '/new?from=novashare', 'abyss');
  if (!w) { say('The Abyss window was blocked: allow pop-ups for this site and tap again.'); return; }
  let handed = false;
  const onMsg = async (e) => {
    if (e.origin !== to || e.source !== w || !e.data || typeof e.data !== 'object') return;
    if (e.data.type === 'abyss-ready' && !handed) {
      handed = true;
      try {
        const out = [];
        for (const f of m) out.push({ name: f.name, type: f.type || f.blob.type, buffer: await f.blob.arrayBuffer() });
        w.postMessage({ type: 'abyss-files', files: out }, to, out.map((f) => f.buffer));
        say('Handed to Abyss: finish the link in its window.');
      } catch { say('This phone could not read the files again. Share them again to send as Abyss.'); }
    } else if (e.data.type === 'abyss-received') {
      removeEventListener('message', onMsg); await dropRec(P.rec.key); document.body.dataset.done = 'abyss'; finish('Handed to Abyss.');
    }
  };
  addEventListener('message', onMsg);
}
function finish(t) {
  say(t); $('send').hidden = true; $('done').hidden = false; $('done').focus();
  document.querySelectorAll('#dest input').forEach((i) => { i.disabled = true; });
}
async function go() {
  const c = choice(); if (P.busy) return;
  if (!c) {                                                 // validate on tap (better-accessibility): say it, focus the first choice
    say('Pick where to send it first.');
    const first = document.querySelector('#dest input:not(:disabled)'); if (first) first.focus();
    return;
  }
  P.busy = true; $('send').disabled = true; alertBox('');
  NS.api('/relay/api/pick/last', { method: 'PUT', json: c }).catch(() => {});   // remembering is best effort
  try {
    if (c.kind === 'library') { say('Opening the library send...'); await toLibrary(); return; }
    if (c.kind === 'abyss') { toAbyss(); return; }
    document.body.dataset.sending = '1';
    const n = await sendLocked(c);
    document.body.dataset.done = 'e2e';
    finish('Sent: ' + plural(n, 'item', 'items') + ' locked for ' + (c.kind === 'people' ? c.people.map(nameOf).join(', ') : label(c).replace(/^Send to /, '')) + '. The server holds only scrambled pieces.');
  } catch (e) {
    alertBox((e && e.message) || String(e), e && e.code);
    say('Nothing is lost: the share is still on this phone. Tap to try again.');
  } finally { P.busy = false; update(); }
}

// ---------- start
async function main() {
  document.body.dataset.ready = '0';
  try {
    P.rec = await loadRec();
    if (!P.rec) {
      $('items').replaceChildren(el('li', { class: 'dim', text: 'Nothing waiting to send. Share something to Novashare from any app.' }));
      $('dest').hidden = true; $('send').hidden = true; $('done').hidden = false; return;
    }
    renderItems();
    P.info = await NS.api('/relay/api/pick');
    P.me = P.info.me;
    P.e2eOK = false; P.e2eWhy = 'Set up this device on the Devices page to send to people.'; P.e2eLink = true;
    if (!NS || !globalThis.NovashareMLKEM) { P.e2eWhy = 'The lock code did not load on this device, so it cannot send to people. Library still works.'; P.e2eLink = false; }
    else {
      NS.S.local = (await NS.kvGet('device')) || null;
      if (NS.S.local && NS.S.local.x) {
        const mine = await NS.api('/devices/api/mine');
        const row = mine.devices.find((d) => d.id === NS.S.local.device_id);
        if (row && row.revoked == null) { P.e2eOK = true; P.e2eLink = false; }
        else P.e2eWhy = 'This device was removed. Set it up again on the Devices page.';
      }
    }
    const pins = P.e2eOK ? await NS.loadPins(P.me) : null;   // step 7: pins + the "only verified" setting
    for (const p of (await NS.api('/devices/api/people')).people) {
      // not set up here: list the people anyway (greyed, with the reason), but check no keys
      const t = P.e2eOK ? await NS.trusted(p, pins) : { ok: false, why: P.e2eWhy, devs: [] };
      P.people.push({ p, t });
      if (p.me && P.e2eOK) { P.meT = t; P.mine = p.devices.filter((d) => d.id !== NS.S.local.device_id); }
    }
    renderChoices();
    preselect(P.info.last);
    $('dest').addEventListener('change', update);
    $('send').addEventListener('click', go);
    update();
    say(P.info.last ? 'Your last choice is picked. Change it or send.' : 'Pick where this goes.');
  } catch (e) {
    alertBox((e && e.message) || String(e), e && e.code);
    $('send').disabled = true;
  } finally { document.body.dataset.ready = '1'; }
}
main();
"""

CSS = r"""/* pick.css - Novashare share picker (pick_js.py, E2E Step 5). On top of devices.css (its tokens: --bg #07050d,
   --accent #b388ff purple, --warn #ff8a3d orange). Purple = what you can tap; orange = what you picked. */
[hidden]{display:none!important}
.sr{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
main.pick{max-width:560px;padding:20px 16px 8px}
.head{display:flex;gap:12px;align-items:center;margin:4px 0 12px}
.head .mark{flex:none;width:44px;height:44px}
.head h1{font-size:1.7rem;letter-spacing:-.01em;margin:0}
.head p{margin:0}
.what{background:var(--card);border:1px solid var(--line);border-radius:18px;padding:6px 16px 10px}
.item{display:grid;grid-template-columns:1fr auto;gap:2px 12px;align-items:baseline;padding:10px 0;border-bottom:1px solid var(--line)}
.item:last-child{border-bottom:0}
.item .name{overflow-wrap:anywhere;font-weight:600}
.item .sz{color:var(--dim);font-variant-numeric:tabular-nums;white-space:nowrap}
.item .bar{grid-column:1/-1;height:6px;border-radius:3px;background:#3a2c5c;overflow:hidden;margin-top:6px}
.item .bar>i{display:block;height:100%;background:linear-gradient(90deg,var(--accent),var(--warn));transform:scaleX(0);transform-origin:left}
#total{margin:4px 0 0}
fieldset{border:0;padding:0;margin:22px 0 0;min-width:0}
legend{font-size:1.1rem;font-weight:650;padding:0;margin:0 0 4px}
legend .dim{font-weight:400;font-size:.95rem}
.note{margin:0 0 8px;color:var(--dim);font-size:.95rem}
.note.lock::before{content:"";display:inline-block;width:.7em;height:.7em;margin-right:.45em;border-radius:50%;border:2px solid var(--warn);vertical-align:-.05em}
.opt{display:flex;align-items:center;gap:14px;min-height:64px;padding:12px 16px;margin:8px 0;border-radius:16px;
  background:var(--card);border:2px solid var(--line);cursor:pointer;-webkit-tap-highlight-color:transparent}
.opt input{flex:none;width:26px;height:26px;min-height:0;margin:0;padding:0;accent-color:var(--warn);cursor:inherit}
.opt .t{display:block;font-weight:650;font-size:1.05rem}
.opt small{display:block;color:var(--dim);font-size:.92rem;line-height:1.35}
.opt:has(input:checked){border-color:var(--warn);background:var(--warn-soft)}
.opt:has(input:disabled){opacity:.62;cursor:not-allowed}
.opt:has(input:focus-visible){outline:3px solid var(--warn);outline-offset:2px}
.opt input:focus-visible{outline:none}
.small{font-size:.9rem}
a{color:var(--accent)}
a:focus-visible{outline:3px solid var(--warn);outline-offset:2px;border-radius:4px}
footer.go{position:sticky;bottom:0;max-width:560px;margin:0 auto;padding:18px 16px calc(14px + env(safe-area-inset-bottom));
  background:linear-gradient(to bottom,transparent,var(--bg) 22%)}
#status{min-height:1.5em;margin:0 0 8px;color:var(--ink)}
#send,a.done{display:flex;align-items:center;justify-content:center;width:100%;min-height:58px;font-size:1.12rem;font-weight:650;border-radius:16px}
#send[data-kind=library]{background:var(--card);color:var(--ink);border-color:var(--accent)}
#send:disabled{opacity:.55;cursor:not-allowed}
a.done{border:1px solid var(--accent);color:var(--accent);text-decoration:none}
@media (prefers-reduced-motion:no-preference){.item .bar>i{transition:transform .25s ease-out}}
/* WCAG 2.4.11: a focused option never sits under the sticky send bar */
html{scroll-padding-bottom:150px}
@media (min-width:700px){main.pick{padding-top:40px}}
"""
