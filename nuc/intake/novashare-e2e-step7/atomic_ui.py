#!/usr/bin/env python3
"""Atomic Share on the phone: the service worker and the "sending" page (T0884 step 1, 2026-11-18).

Jason, 2026-11-18, after the first share worked on his phone: "can the icon spin with energetic protons
zooming around the atom and a progress bar and a resume feature of bandwidth loss or signal drop".

FLOW
1. Android share sheet -> Atomic Share -> the browser POSTs the files to /share (manifest share_target).
2. SW (below) catches that POST before it leaves the phone, keeps the files in the phone's IndexedDB and
   answers with a redirect to /sending?k=<key>. Nothing is uploaded yet, so a dead signal loses nothing.
3. /sending (PAGE below) shows the spinning atom, uploads each file in 4 MB pieces through resumable.py
   (/up/*) with a progress bar, speed and time left. Signal lost -> "waiting for signal", then it asks the
   server where it stopped and carries on. Closing the app is fine too: opening /sending again resumes
   every unfinished share. Sign-in expired -> it says so; after signing in the share continues.
4. Links and text (no file) still go to /share, sent directly (header X-Atomic-Direct so the SW lets
   them through), and the page shows the same verdicts as before.
If the service worker is missing or old, the plain POST /share in share.py still works as before.
Results are shown with textContent only (file names come from the phone; never HTML-injected).
OUTCOME: see the T0884 Review line (sandbox + emulator test).
"""
from __future__ import annotations

import themes  # T0942: the drop's look
import html
import os
import re

from fastapi import APIRouter, Header
from fastapi.responses import HTMLResponse

router = APIRouter()

# --- service worker -------------------------------------------------------------------------------------
SW = r"""// Atomic Share service worker (atomic_ui.py, T0884, 2026-11-18). Catches the share-sheet POST and keeps
// the files on the phone (IndexedDB) until /sending has uploaded them; everything else passes straight
// through to the network (nothing is cached, so pages are never stale).
const DB = 'atomic-share', STORE = 'pending';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
function openDb() {
  return new Promise((ok, no) => {
    const r = indexedDB.open(DB, 1);
    r.onupgradeneeded = () => r.result.createObjectStore(STORE, {keyPath: 'key'});
    r.onsuccess = () => ok(r.result); r.onerror = () => no(r.error);
  });
}
async function save(rec) {
  const d = await openDb();
  return new Promise((ok, no) => {
    const t = d.transaction(STORE, 'readwrite'); t.objectStore(STORE).put(rec);
    t.oncomplete = ok; t.onerror = () => no(t.error);
  });
}
self.addEventListener('fetch', e => {
  const req = e.request, u = new URL(req.url);
  if (req.method === 'POST' && u.pathname.endsWith('/share') && !req.headers.get('X-Atomic-Direct')) {
    const spare = req.clone();   // if keeping the files fails, send the share the old way
    e.respondWith((async () => {
      try {
        const fd = await req.formData();
        const files = fd.getAll('files').filter(f => typeof f === 'object' && f.name);
        const key = Date.now() + '-' + Math.random().toString(36).slice(2, 8);
        await save({key, created: Date.now(), title: String(fd.get('title') || ''),
                    text: String(fd.get('text') || ''), url: String(fd.get('url') || ''),
                    files: files.map(f => ({name: f.name, type: f.type, size: f.size, blob: f}))});
        return Response.redirect(new URL('sending?k=' + encodeURIComponent(key), self.registration.scope).href, 303);
      } catch {
        return fetch(spare);
      }
    })());
    return;
  }
  e.respondWith(fetch(req));
});
"""

# --- Novashare E2E step 5 (2026-11-24): the share picker ------------------------------------------------
# With the horizon (Novashare) theme, a share no longer goes straight to /sending (= the library). The worker keeps
# the share in its OWN store ("novashare-pick", same record shape) and opens /relay/share (pick.py), where the person
# picks People, My devices, Library or Send as Abyss. Only "Library" copies the record into "atomic-share" and opens
# /sending?k=..., so the library path from there on is byte-for-byte today's, and /sending (which sends EVERY record
# in "atomic-share") never sees a share meant for a person. Off for other themes (Garden Drop has no relay) and with
# INTAKE_SHARE_PICKER=0: then SW is exactly the old text (test_themes.py). OUTCOME: test_pick.py, test_pick_browser.py.
import dropconfig as _dropconfig  # noqa: E402
import os as _os  # noqa: E402

PICKER = (_dropconfig.BRAND.get("theme") == "horizon"
          and _os.environ.get("INTAKE_SHARE_PICKER", "1").strip() != "0")
if PICKER:
    _old = ("const DB = 'atomic-share', STORE = 'pending';",
            "return Response.redirect(new URL('sending?k=' + encodeURIComponent(key), self.registration.scope).href, 303);")
    assert all(SW.count(o) == 1 for o in _old), "atomic_ui SW changed: the picker patch no longer fits"
    SW = (SW.replace(_old[0], "const DB = 'novashare-pick', STORE = 'pending';   // step 5: undecided shares (pick.py)")
            .replace(_old[1], "return Response.redirect(new URL('relay/share?k=' + encodeURIComponent(key), "
                              "self.registration.scope).href, 303);   // step 5: the destination picker"))

# --- the shared upload engine (T0926 U3, 2026-11-19) ----------------------------------------------------
# One copy of "send a file in pieces, resume, checksum each piece, say what happened in plain words", used
# by the sending page below AND the Drop page (portal.py), which used to send each file in one request that
# a dropped signal lost. Jason, 2026-11-19: "like how tcp packets work and break up the up and down load to
# pieces and check sum it without a separate download". Pieces + resume were already here (T0884); the
# checksum is the browser's own Web Crypto (crypto.subtle, no library), checked per piece by resumable.py.
ATOMIC_JS = r"""// atomic.js: Atomic Share upload engine (atomic_ui.py, T0926, 2026-11-19). No outside libraries.
"use strict";
/* exported Atomic */   // the shared engine: /sending and the Drop page use it (global)
const Atomic = (() => {
  const STALL = 8000, CHECKSUM_TRIES = 3;
  const live = new Set(), wakers = new Set();      // requests in flight / pauses that 'online' can cut short
  class Signin extends Error {}
  // T0931 item 21 (2026-11-20): a 5xx from the server (not 507) is "busy", not "no signal". The page said
  // "Signal lost" for both. req() now rejects with Busy; send() retries it with the same 1-2-4-8 s waits and
  // gives up after BUSY_TRIES in a row with a plain-words error (the file stays safe on the phone).
  class Busy extends Error {}
  const BUSY_TRIES = 8;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const mb = n => n >= 1073741824 ? (n / 1073741824).toFixed(2) + ' GB' : (n / 1048576).toFixed(1) + ' MB';
  function left(s) {                                // U13: "31 min left", not "1834 s left"
    s = Math.ceil(s); if (s < 60) return s + ' s left'; if (s < 3600) return Math.round(s / 60) + ' min left';
    const h = Math.floor(s / 3600); return h + ' h ' + Math.round((s - h * 3600) / 60) + ' min left'; }
  // One request; resolves {status, json}. Throws Signin when the gate answered instead of the app, and a plain
  // Error when the network is gone. A piece with no progress for STALL ms is aborted (fast reconnect, T0925).
  function req(method, url, body, onProgress, headers) {
    return new Promise((ok, no) => {
      const x = new XMLHttpRequest(); x.open(method, url); x.responseType = 'text'; live.add(x);
      if (body && !(body instanceof Blob)) x.setRequestHeader('Content-Type', 'application/json');
      for (const k in headers || {}) x.setRequestHeader(k, headers[k]);
      const blob = body instanceof Blob; let dog = null;
      const pet = () => { clearTimeout(dog); dog = setTimeout(() => x.abort(), STALL); };
      if (blob) { pet(); x.upload.onloadend = () => clearTimeout(dog); }
      x.upload.onprogress = e => { if (blob) pet(); if (onProgress) onProgress(e.loaded); };
      x.onloadend = () => { clearTimeout(dog); live.delete(x); };
      x.onabort = x.onerror = x.ontimeout = () => no(new Error('network'));
      x.onload = () => {
        if (x.status === 401 || /\/auth\//.test(x.responseURL)) return no(new Signin());
        if (x.status >= 500 && x.status !== 507) return no(new Busy());
        let j = null; try { j = JSON.parse(x.responseText); } catch { /* not available: nothing to do */ }
        if (j === null && x.status < 500) return no(new Signin());   // an HTML sign-in page, not our JSON
        ok({status: x.status, json: j});
      };
      x.timeout = 120000;
      x.send(blob ? body : body ? JSON.stringify(body) : null);
    });
  }
  function pause(ms) {                              // a wait that poke() (or 'online') ends early
    return new Promise(r => { const w = () => { clearTimeout(t); wakers.delete(w); r(); };
      const t = setTimeout(w, ms); wakers.add(w); });
  }
  function poke() { for (const x of live) if (x.readyState !== 4) x.abort(); for (const w of [...wakers]) w(); }
  addEventListener('online', poke);
  async function sum(blob) {                        // SHA-256 of one piece, hex; null where Web Crypto is missing
    if (!(self.crypto && crypto.subtle)) return null;
    const d = await crypto.subtle.digest('SHA-256', await blob.arrayBuffer());
    return Array.from(new Uint8Array(d), b => b.toString(16).padStart(2, '0')).join('');
  }
  // U8/U14: what happened, in plain words (the server's "why" is for Jason's log, not the phone).
  const KIND = {book: 'a book', comic: 'a comic', document: 'paperwork', recipe: 'a recipe', workout: 'a workout',
                video: 'a video'};
  const cap = s => s ? s[0].toUpperCase() + s.slice(1) : '';
  function plain(r) {
    const k = KIND[r.kind] || 'a file', p = String(r.path || ''), dest = String(r.dest || '');
    switch (r.status) {
      case 'duplicate': return 'Already there: this exact file was sent before, so it was not saved twice.';
      case 'kept': return 'Not filed, but kept safe on the server for Jason to sort.';
      case 'skipped': return 'Skipped: ' + (r.why || 'too big to unpack');
      case 'refused': return 'Not saved: ' + (r.why || 'this kind of link cannot be read');
      case 'error': {
        const c = r.code;
        if (c === 413) return r.why || 'Too big for @@NAME@@.';
        if (c === 507) return 'The server is short of space. Try again later.';
        if (r.why === 'signed out') return 'Signed out before this was sent. Sign in and share it again.';
        return ('Not saved: ' + (r.why || 'something went wrong')).replace(/\.?$/, '.');
      }
      case 'filed':
        if (r.held) return 'Kept for you: ' + k + '. You can read that library but not add to it, so Jason will file it.';
        if (dest.includes('/pending/') || p.includes('/.pending/'))
          return 'Kept for ' + (cap(r.person) || 'you') + ': ' + k + ' waits to be added to its app.';
        if (!r.dest) return 'Saved, not sorted: @@NAME@@ could not tell what this is, so it waits for Jason.';
        if (r.kind === 'video') return 'Filed: a video in the ' + (p.includes('/private/') ? 'private' : 'general') + ' library.';
        if (r.kind === 'document') return 'Filed: paperwork, sent to Paperless.';
        return 'Filed: ' + k + (r.person ? ' on ' + cap(r.person) + "'s shelf" : '') + '.';
    }
    return r.why || String(r.status || 'done');
  }
  const bad = results => results.some(x => x.status === 'error' || x.status === 'refused');
  // Send one file. f = {name, blob, uid?, chunk?, done?, results?} (kept on f so a later call resumes);
  // opts = {hint, shelf}; ui = {save, sending(off), progress(bytes), sorting(again), waiting(), signin()}.
  // PARALLEL, ANY ORDER (T0928, 2026-11-19): WIDTH numbered pieces in flight at once, each written by the
  // server straight into its place, so they can land in any order; the server lists what is missing and
  // only that is (re)sent. Progress = whole pieces in + the bytes of pieces still on their way.
  const WIDTH = 3;
  async function sendPieces(f, st, ui) {
    const c = st.json.chunk || f.chunk, todo = st.json.missing.slice(), going = new Map();
    let whole = st.json.offset, damaged = 0, broken = null;
    const show = () => { let n = whole; for (const v of going.values()) n += v; ui.progress(Math.min(n, f.blob.size)); };
    async function lane() {
      while (todo.length && !broken) {
        const n = todo.shift(), piece = f.blob.slice(n * c, Math.min(f.blob.size, (n + 1) * c)), h = await sum(piece);
        going.set(n, 0);
        let res;
        try { res = await req('PUT', 'up/' + f.uid + '/piece/' + n, piece, b => { going.set(n, b); show(); },
                              h ? {'X-Piece-Sha256': h} : null); }
        catch (e) { going.delete(n); broken = broken || e; return; }
        going.delete(n);
        if (res.status === 200) { whole += piece.size; f.done = whole; show(); continue; }
        if (res.status === 422 && ++damaged < CHECKSUM_TRIES) { todo.push(n); continue; }
        broken = broken || Object.assign(new Error('refused'), {code: res.status,
          why: res.status === 422 ? 'the file kept arriving damaged; try a different network' : (res.json && (res.json.detail || res.json.error))});
      }
    }
    await Promise.all(Array.from({length: WIDTH}, lane));
    if (broken) throw broken;
  }
  async function send(f, opts, ui) {
    let tries = 0, damaged = 0, busy = 0;
    const fail = (code, why) => [{name: f.name, status: 'error', code, why}];
    for (;;) {
      try {
        if (!f.uid) {
          const s = await req('POST', 'up/start', {name: f.name, size: f.blob.size, hint: opts.hint || null,
                                                   shelf: opts.shelf || null, parallel: true,
                                                   lastModified: f.blob.lastModified || 0});   // T0931 item 15: lets the server hand back an unfinished upload instead of starting a second
          if (s.status !== 200) return fail(s.status, s.json && s.json.detail);
          f.uid = s.json.id; f.chunk = s.json.chunk; await ui.save();
        }
        const st = await req('GET', 'up/' + f.uid);
        if (st.status === 404) { f.uid = null; continue; }           // gone from the server (48 h): start again
        let off = st.json.offset; ui.sending(off);
        if (Array.isArray(st.json.missing)) {                         // numbered pieces, any order (T0928)
          try { await sendPieces(f, st, ui); }
          catch (e) { if (e.code) return fail(e.code, e.why); throw e; }
          off = f.blob.size; tries = 0;
        }
        while (off < f.blob.size) {
          const piece = f.blob.slice(off, off + (f.chunk || 4194304)), h = await sum(piece);
          const res = await req('PUT', 'up/' + f.uid + '?offset=' + off, piece, n => ui.progress(off + n),
                                h ? {'X-Piece-Sha256': h} : null);
          if (res.status === 422 && res.json && res.json.checksum) {  // damaged on the way: send it again
            if (++damaged >= CHECKSUM_TRIES) return fail(422, 'the file kept arriving damaged; try a different network');
            continue;
          }
          if (res.status !== 200 && res.status !== 409) return fail(res.status, res.json && res.json.detail);
          off = res.json.offset; f.done = off; ui.progress(off); tries = 0;
        }
        ui.sorting(false);
        const fin = await req('POST', 'up/' + f.uid + '/finish');
        if (fin.status === 409) { if (fin.json && fin.json.working) { ui.sorting(true); await sleep(3000); } continue; }
        busy = 0; f.results = Array.isArray(fin.json) ? fin.json : fail(fin.status, 'the server gave an unexpected answer');
        await ui.save(); return f.results;
      } catch (e) {
        if (e instanceof Signin) { ui.signin(); await pause(2e9); continue; }   // until "Try again now"
        if (e instanceof Busy) {
          if (++busy > BUSY_TRIES) return fail(503, 'The server could not take this file right now. It is still on your phone: share it again later.');
          (ui.busy || ui.waiting)(); await pause(Math.min(8000, 1000 * 2 ** Math.min(busy - 1, 3))); continue;
        }
        ui.waiting(); await pause(Math.min(8000, 1000 * 2 ** Math.min(tries++, 3)));   // 1, 2, 4, 8 s
      }
    }
  }
  return {send, req, poke, pause, plain, bad, mb, left, sleep, Signin, Busy};
})();
"""
# Review 11-23 #1: the engine's messages use the app's own name (drop.toml [brand]), escaped for a JS '...' string.
ATOMIC_JS = ATOMIC_JS.replace("@@NAME@@", themes.current()["name"].replace("\\", "\\\\").replace("'", "\\'"))

# --- the sending page -----------------------------------------------------------------------------------
SENDING_CSS = r"""@@ROOT@@
[hidden]{display:none!important}*{box-sizing:border-box}html,body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:560px;margin:0 auto;padding:24px 16px calc(24px + env(safe-area-inset-bottom))}
.atom{display:block;width:min(56vw,240px);height:auto;margin:8px auto 4px;overflow:visible}
.atom .nucleus{transform-origin:48px 48px;animation:pulse 1.6s ease-in-out infinite}
.atom .glow{opacity:.8;transform-origin:48px 48px;animation:pulse 1.6s ease-in-out infinite}
body.done .nucleus,body.done .glow,body.bad .nucleus,body.bad .glow{animation:none}   /* T0931 item 20: stop the pulse on error too */
body.done .nucleus{fill:var(--ok)}body.bad .nucleus{fill:var(--bad)}
@keyframes pulse{50%{transform:scale(1.18)}}
@media (prefers-reduced-motion:reduce){.atom .orbits,.atom .nucleus,.atom .glow{animation:none}.proton{display:none}}
h1{font-size:1.35rem;margin:8px 0 2px;text-align:center}#state{color:var(--dim);text-align:center;margin:0 0 16px;min-height:1.5em}
.bar{height:14px;border-radius:7px;background:#5a679c;overflow:hidden}   /* T0931 item 22: track #1f2852 was 1.3:1 on the page; #5a679c is 3.1:1 on the page and 3.2:1 on a card (WCAG 1.4.11 needs 3:1) */
.bar>i{display:block;height:100%;width:100%;background:linear-gradient(90deg,var(--cyan),var(--orange));transform:scaleX(0);transform-origin:left;transition:transform .3s ease-out}
.bar.indet>i{width:35%;transition:none;animation:slide 1.2s ease-in-out infinite}@keyframes slide{from{transform:translateX(-100%)}to{transform:translateX(290%)}}
@media (prefers-reduced-motion:reduce){.bar.indet>i{animation:none;width:100%;transform:none;opacity:.5}}
#nums{display:flex;justify-content:space-between;color:var(--dim);font-size:1rem;margin:6px 0 18px;font-variant-numeric:tabular-nums}
ul{list-style:none;padding:0;margin:0}li{background:var(--card);border-radius:12px;padding:12px 14px;margin:0 0 10px}
li b{display:block;word-break:break-word}li small{color:var(--dim);display:block}li .bar{height:6px;margin-top:8px}
li.ok b::before{content:"\2713  ";color:var(--ok)}li.err b::before{content:"\2715  ";color:var(--bad)}
a,button{color:var(--cyan)}button{background:none;border:1px solid var(--cyan);border-radius:10px;padding:10px 16px;font:inherit;min-height:44px;cursor:pointer}
.row{display:flex;gap:12px;justify-content:center;align-items:center;margin-top:16px;flex-wrap:wrap}
.row a{display:inline-flex;align-items:center;min-height:44px;padding:0 8px}
:focus-visible{outline:3px solid var(--cyan);outline-offset:3px;border-radius:6px}
li small a{display:inline-block;padding:10px 0}
a.done{display:flex;justify-content:center;align-items:center;width:100%;min-height:48px;border-radius:10px;background:var(--cyan);color:var(--bg);font-weight:600;text-decoration:none}
"""

# Step 7 (2026-11-24, audit High 2a): this script was inline in PAGE; it is /assets/sending.js now (strict CSP).
SENDING_JS = r"""/* global Atomic */   // from /atomic.js, loaded (deferred) just before this file
"use strict";
// The app name for the tab title, from <html data-app> (step 7: was pasted into an inline script).
const APP = document.documentElement.dataset.app || '';
const DB = 'atomic-share', STORE = 'pending', $ = id => document.getElementById(id);
const {mb} = Atomic;
function openDb() { return new Promise((ok, no) => { const r = indexedDB.open(DB, 1);
  r.onupgradeneeded = () => r.result.createObjectStore(STORE, {keyPath: 'key'});
  r.onsuccess = () => ok(r.result); r.onerror = () => no(r.error); }); }
async function tx(mode, fn) { const d = await openDb(); return new Promise((ok, no) => {
  const t = d.transaction(STORE, mode), s = t.objectStore(STORE); const res = fn(s);
  t.oncomplete = () => ok(res && res.result); t.onerror = () => no(t.error); }); }
const getAll = () => tx('readonly', s => s.getAll());
const put = rec => tx('readwrite', s => s.put(rec));
const del = key => tx('readwrite', s => s.delete(key));
// SPIN (2026-11-19). Jason: "the animation for the atom is jittery after every file". Cause: the speed was a
// CSS animation-duration switched per mode (7s / 2.2s / 14s / none); a new duration re-maps the angle, so the
// orbits jumped at every file boundary (fast -> sorting -> fast). Now one rAF loop keeps the angle and eases
// the SPEED toward each mode's target, so speeding up, slowing down and stopping are continuous.
const SPEED = {fast: 360 / 2.2, '': 360 / 7, wait: 360 / 14, done: 0, bad: 0};   // degrees per second
const orbits = document.querySelector('.atom .orbits'), svg = document.querySelector('.atom'),
      still = matchMedia('(prefers-reduced-motion: reduce)');
let ang = 0, spd = SPEED[''], want = SPEED[''], last = performance.now(), running = false;
// U11 (battery, 2026-11-19): the loop and the SVG protons used to run forever, even after "All filed".
// Now the loop stops once the spin has eased to a halt, and the protons pause with it.
function spin(t) {
  const dt = Math.min(0.1, (t - last) / 1000); last = t;
  spd += (want - spd) * Math.min(1, dt * 2.5);           // ease toward the target speed (about 0.4 s)
  ang = (ang + spd * dt) % 360;
  if (!still.matches) orbits.setAttribute('transform', 'rotate(' + ang.toFixed(2) + ' 48 48)');
  if (want === 0 && spd < 2) { running = false; if (svg.pauseAnimations) svg.pauseAnimations(); return; }
  requestAnimationFrame(spin);
}
function mode(m) {
  document.body.className = m; want = SPEED[m] ?? SPEED[''];
  if (want > 0 && !running && !still.matches) {   // T0931 item 20: no requestAnimationFrame at all while reduced motion is on
    running = true; last = performance.now(); if (svg.unpauseAnimations) svg.unpauseAnimations(); requestAnimationFrame(spin); }
}
mode('');
function say(t) { $('state').textContent = t; }
function again(on) { $('again').hidden = !on; }        // U9: "Try again now" while waiting or signed out
$('again').onclick = () => { again(false); Atomic.poke(); };
// SEND AS ABYSS (Abyss step 5, 2026-11-24; Jason only: the button is only in his copy of this page). The photos and
// videos of this share are kept in memory (the phone's own copy) and, on a tap, handed to Abyss's send page in a
// new window by postMessage: Abyss says {type:'abyss-ready'} first; only a message from Abyss's exact origin AND
// from that window counts; the bytes go as transferable ArrayBuffers to that origin only. Abyss encrypts them in
// its page before anything is uploaded (its server only ever gets ciphertext).
const AB = $('abyss'), media = [];
function keepMedia(f) {
  const t = String(f.type || (f.blob && f.blob.type) || '');
  if (AB && f.blob && /^(image|video)\//.test(t) && media.length < 20) media.push({name: f.name, type: t, blob: f.blob});
}
function showAbyss() { if (AB && media.length) AB.hidden = false; }
if (AB) AB.onclick = () => {
  const to = new URL(AB.dataset.to).origin;
  if (media.reduce((n, m) => n + m.blob.size, 0) > 200 * 1048576) { say('Too big for Abyss: 200 MB at most in all.'); return; }
  const w = window.open(to + '/new?from=novashare', 'abyss');
  if (!w) { say('The Abyss window was blocked: allow pop-ups for this site and tap again.'); return; }
  let handed = false;
  const onMsg = async e => {
    if (e.origin !== to || e.source !== w || !e.data || typeof e.data !== 'object') return;
    if (e.data.type === 'abyss-ready' && !handed) {
      handed = true;
      try {
        const files = [];
        for (const m of media) files.push({name: m.name, type: m.type, buffer: await m.blob.arrayBuffer()});
        w.postMessage({type: 'abyss-files', files}, to, files.map(f => f.buffer));
        say('Handed to Abyss: finish the link in its window.');
      } catch { say('This phone could not read the files again. Share them again to send as Abyss.'); }
    } else if (e.data.type === 'abyss-received') removeEventListener('message', onMsg);
  };
  addEventListener('message', onMsg);
};
let total = 0, sent = 0, t0 = 0, sent0 = 0;
function paint() {
  const p = total ? Math.min(100, sent / total * 100) : 0;
  $('bar').firstElementChild.style.transform = 'scaleX(' + (p / 100).toFixed(4) + ')'; $('pct').textContent = p.toFixed(0) + '%';
  $('bar').setAttribute('aria-valuenow', p.toFixed(0));                       // U12
  $('bar').setAttribute('aria-valuetext', p.toFixed(0) + ' percent');         // T0931 item 22: read as "40 percent", not a bare number
  const secs = (performance.now() - t0) / 1000, rate = secs > 1 ? (sent - sent0) / secs : 0;
  $('speed').textContent = rate ? mb(rate) + '/s' : '';
  $('eta').textContent = rate && sent < total ? Atomic.left((total - sent) / rate) : '';   // U13
  if (working) document.title = p.toFixed(0) + '% · ' + APP;                              // review 11-23 #15
}
function row(name) { const li = document.createElement('li'), b = document.createElement('b'), s = document.createElement('small'),
  bar = document.createElement('div'); bar.className = 'bar'; bar.setAttribute('aria-hidden', 'true'); bar.appendChild(document.createElement('i'));
  b.textContent = name; li.append(b, s, bar); $('list').appendChild(li); return {li, s, bar}; }
function signinLink() { const a = document.createElement('a'); a.href = 'sending'; a.textContent = 'Sign in again';
  return a; }   // U10: back to THIS page after sign-in, which resumes from the phone's copy
// PER-FILE BAR (2026-11-19): each row's bar used to stay empty (only the top bar was painted). Fill it too.
function fill(r, done, size) { r.bar.firstElementChild.style.transform = 'scaleX(' + (size ? Math.min(1, done / size) : 1).toFixed(4) + ')'; }
function verdicts(r, results) {                       // U8/U14: plain words, one line per result
  r.bar.remove(); r.li.className = Atomic.bad(results) ? 'err' : 'ok';
  r.s.textContent = results.map(Atomic.plain).join(' ');
}
// T0931 item 21: the server answered with an error of its own (5xx): say so, not "Signal lost".
function busy() { mode('wait'); again(true); say('The server is busy. Trying again… your progress is kept.'); }
function waiting() { mode('wait'); again(true); say('Signal lost. Waiting to resume… your progress is kept.'); }
function signedOut(r) { working = false; mode('bad'); again(true); say('Sign-in expired. Sign in, then tap Try again now: nothing is lost.');
  r.s.replaceChildren(signinLink()); }
async function sendFile(rec, f, r) {
  const base = sent, size = f.blob.size;
  return Atomic.send(f, {}, {
    save: () => put(rec),
    sending: off => { working = true; mode('fast'); again(false); say('Sending ' + f.name); r.s.textContent = ''; sent = base + off; paint(); fill(r, off, size); },
    progress: n => { sent = base + n; paint(); fill(r, n, size); },
    sorting: more => { mode(''); again(false); say((more ? 'Still sorting ' : 'Sorting ') + f.name + '…');
      r.bar.firstElementChild.style.transform = ''; r.bar.classList.add('indet'); },
    waiting, busy, signin: () => signedOut(r),
  });
}
async function sendLink(rec, r) {
  r.bar.classList.add('indet'); mode('fast'); say('Reading the page…');
  // T0931 item 18 (2026-11-20): the answer was used as a list without looking: ONE 403 (or an HTML error page)
  // made res.json() throw or return an object, and the page then failed on every later try, so a single bad
  // answer locked the sending page for good. Now: 401/redirect = sign in again (as before); 5xx = busy,
  // retry; any other non-OK answer or a non-list body = ONE plain error row, and the page goes on.
  const oops = (code, why) => [{name: rec.url || 'link', status: 'error', code, why}];
  let busyN = 0;
  for (let tries = 0; ; tries++) {
    try {
      const fd = new FormData(); fd.set('title', rec.title); fd.set('text', rec.text); fd.set('url', rec.url);
      const res = await fetch('share', {method: 'POST', body: fd, headers: {'Accept': 'application/json', 'X-Atomic-Direct': '1'}});
      if (res.redirected || res.status === 401) throw new Atomic.Signin();
      if (res.status >= 500 && res.status !== 507) throw new Atomic.Busy();
      if (!res.ok) return oops(res.status, res.status === 403 ? 'you may not add to this library' : 'the server did not accept this link');
      let j = null; try { j = await res.json(); } catch { /* not available: nothing to do */ }
      return Array.isArray(j) ? j : oops(res.status, 'the server gave an unexpected answer');
    } catch (e) {
      // U2 (2026-11-19): a link shared while signed out used to be DELETED from the phone. Now it waits,
      // kept, until sign-in and "Try again now", like a file does.
      if (e instanceof Atomic.Signin) { signedOut(r); await Atomic.pause(2e9); working = true; mode('fast'); say('Reading the page…'); continue; }
      if (e instanceof Atomic.Busy) {
        if (++busyN > 8) return oops(503, 'the server could not take this link right now; share it again later');
        busy(); await Atomic.pause(Math.min(8000, 1000 * 2 ** Math.min(busyN - 1, 3))); mode('fast'); again(false); continue;
      }
      waiting(); await Atomic.pause(Math.min(8000, 1000 * 2 ** Math.min(tries, 3))); mode('fast'); again(false);
    }
  }
}
// T0931 item 22: leaving while pieces are still going out asks first. (Files wait safely in the phone's own
// storage, so nothing is lost either way; this only stops an accidental swipe-away mid-send.)
var working = false;   // var: signedOut() above main() reads it before this line runs
addEventListener('beforeunload', e => { if (working) { e.preventDefault(); e.returnValue = ''; } });
async function main() {
  working = true;
  const k = new URLSearchParams(location.search).get('k');
  let recs = []; try { recs = await getAll(); } catch { /* not available: nothing to do */ }
  recs.sort((a, b) => a.created - b.created);
  if (!recs.length) { working = false; mode('done'); $('bar').hidden = true; $('nums').hidden = true;   // review 11-23 #7
    say(k ? 'Already sent.' : 'Nothing waiting to send.'); return; }
  total = recs.reduce((n, r) => n + r.files.reduce((m, f) => m + f.blob.size, 0), 0);
  $('bar').classList.toggle('indet', !total); t0 = performance.now(); sent0 = 0;
  $('title').textContent = recs.length > 1 ? 'Sending ' + recs.length + ' shares' : 'Sending';
  let anyBad = false;
  for (const rec of recs) {
    if (rec.files.length) for (const f of rec.files) {
      const r = row(f.name + ' · ' + mb(f.blob.size));
      keepMedia(f);                                            // Abyss step 5 (Jason only; no-op for others)
      const had = !!f.results, res = had ? f.results : await sendFile(rec, f, r); verdicts(r, res);
      if (had) { sent += f.blob.size; paint(); }              // sent in an earlier visit: count its bytes
      anyBad = anyBad || Atomic.bad(res);
    } else { const r = row(rec.url || (rec.text || '').slice(0, 80) || 'shared text');
      const res = await sendLink(rec, r); verdicts(r, res);
      anyBad = anyBad || Atomic.bad(res); }
    await del(rec.key);
  }
  // T0931 item 19 (2026-11-20): a link-only share has no bytes, so total was 0: the top bar kept its sliding
  // "indet" state and 0% after "All filed". Now the sliding stops and the bar is full when everything is done.
  $('bar').classList.remove('indet'); if (!total) total = 1;
  sent = total; paint(); $('speed').textContent = ''; $('eta').textContent = ''; again(false);
  mode(anyBad ? 'bad' : 'done'); say(anyBad ? 'Done, but something needs a look: see below.' : 'All filed.');
  working = false; $('title').textContent = anyBad ? 'Finished' : 'Shared';
  showAbyss();
  document.title = (anyBad ? 'Needs a look' : 'Shared') + ' · ' + APP;                     // review 11-23 #15
}
// Step 7: Trusted Types are required on this origin, so the worker URL goes through a policy that allows only /sw.js.
if (navigator.serviceWorker) navigator.serviceWorker.register(globalThis.trustedTypes
  ? trustedTypes.createPolicy('novashare-site-sw', {createScriptURL: u => { if (u !== '/sw.js') throw new Error('blocked'); return u; }}).createScriptURL('/sw.js')
  : '/sw.js');
main().catch(() => { working = false; mode('bad'); say('Something went wrong on this page. Tap Done and share again: files already sent are safe.'); });
"""

# Step 7: no inline style or script; @@CSS_TAG@@ / @@JS_TAG@@ become /assets links with SRI below.
PAGE = r"""<!doctype html><html lang=en data-app="@@NAME_ATTR@@"><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name=theme-color content="@@THEME_COLOR@@"><title>@@NAME@@: sending</title>
<link rel=manifest href="manifest.webmanifest">
@@CSS_TAG@@
<main>
@@SVG@@
<h1 id=title>@@NAME@@</h1><p id=state role=status aria-live=polite>Getting ready…</p>
<div class=bar id=bar role=progressbar aria-label="Sent so far" aria-valuemin=0 aria-valuemax=100 aria-valuenow=0 aria-valuetext="0 percent"><i></i></div>
<div id=nums aria-hidden=true><span id=pct>0%</span><span id=speed></span><span id=eta></span></div>
<ul id=list></ul>   <!-- T0931 item 22: one live region (#state) is enough; a second one read every result twice -->
<div class=row><button id=again hidden>Try again now</button><!--ABYSS-BUTTON--><a class=done href="./">Done</a></div>   <!-- review 11-23 #12: a clear end action -->
</main>
<script defer src="/atomic.js" integrity="@@ATOMIC_SRI@@"></script>
@@JS_TAG@@

"""
# T0942 (2026-11-21): the look (colours, picture, name) comes from themes.py / drop.toml; with theme atomic this is byte-for-byte the old page (test_themes.py).
PAGE = themes.apply(PAGE)


def _sending_css() -> str:
    """The page style plus the theme picture's own style (themes.svg_parts), with the theme colours filled in."""
    return themes.apply(SENDING_CSS) + "\n" + themes.svg_parts(themes.current()["svg"])[1] + "\n"


def _sending_js() -> str:
    """The page script, then the theme picture's script (it waits for DOMContentLoaded on its own)."""
    return SENDING_JS + "\n" + themes.svg_parts(themes.current()["svg"])[2] + "\n"


import site_assets  # noqa: E402

site_assets.register("sending.css", _sending_css)
site_assets.register("sending.js", _sending_js)


def _atomic_sri() -> str:
    import base64
    import hashlib
    return "sha384-" + base64.b64encode(hashlib.sha384(ATOMIC_JS.encode()).digest()).decode()


PAGE = (PAGE.replace("@@CSS_TAG@@", site_assets.css_tag("sending.css"))
        .replace("@@JS_TAG@@", site_assets.js_tag("sending.js"))
        .replace("@@ATOMIC_SRI@@", _atomic_sri())
        .replace("@@NAME_ATTR@@", html.escape(themes.current()["name"], quote=True)))


@router.get("/atomic.js")
def atomic_js():
    """The shared upload engine for /sending and the Drop page (no-cache: an old copy must not linger)."""
    from fastapi.responses import Response
    return Response(ATOMIC_JS, media_type="text/javascript", headers={"Cache-Control": "no-cache"})


ABYSS_URL = os.environ.get("ABYSS_URL", "https://abyss.home.example.net").strip().rstrip("/")


@router.get("/sending", response_class=HTMLResponse)
def sending(x_auth_email: str | None = Header(default=None)):
    """Abyss step 5: Jason's copy has the "Send as Abyss" button; everyone else gets the page as before."""
    import app
    import portal
    me = app._person((x_auth_email or "").strip().lower()) if x_auth_email else None
    ok = me == portal.ADMIN and re.match(r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$", ABYSS_URL)
    btn = f'<button id=abyss hidden data-to="{html.escape(ABYSS_URL)}">Send as Abyss</button>' if ok else ""
    return HTMLResponse(PAGE.replace("<!--ABYSS-BUTTON-->", btn), headers={"Cache-Control": "no-store"})
