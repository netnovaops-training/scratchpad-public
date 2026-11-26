// Passkey sign-in and the small account page. Served at /auth/passkey/passkey.js.
// Plain script, no libraries. Everything shown is set with textContent.
// The password form never depends on anything in here.
(function () {
  'use strict';

  // ---- timing of the sign-in sequence (tune these from a timing test) ----
  // For a person who has a passkey on this device: once the orb video's first
  // frame is on screen, wait this long, then open the passkey prompt...
  var FIRST_FRAME_TO_PROMPT_MS = 300;
  // ...but never later than this long after the page started loading (a slow
  // video must not hold up the prompt).
  var MAX_WAIT_MS = 1200;

  // A non-secret note, kept only in this browser, that a passkey has been used
  // or added on THIS device. It decides whether the page opens the prompt by
  // itself. It holds no credential: just "1". Storage may be blocked; then the
  // page simply behaves as for someone without the note.
  var HERE_KEY = 'pk-here';
  function hereGet() { try { return window.localStorage.getItem(HERE_KEY) === '1'; } catch (e) { return false; } }
  function hereSet() { try { window.localStorage.setItem(HERE_KEY, '1'); } catch (e) { /* fine */ } }
  function hereClear() { try { window.localStorage.removeItem(HERE_KEY); } catch (e) { /* fine */ } }

  var supported = !!(window.PublicKeyCredential && navigator.credentials &&
                     navigator.credentials.get && navigator.credentials.create);

  // base64url <-> bytes. WebAuthn JSON is base64url, which atob() can't read
  // until - and _ are put back, and big buffers are turned into text in chunks.
  function toBuf(s) {
    s = String(s).replace(/-/g, '+').replace(/_/g, '/');
    while (s.length % 4) s += '=';
    var bin = atob(s), out = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out.buffer;
  }
  function toB64u(buf) {
    var b = new Uint8Array(buf), s = '';
    for (var i = 0; i < b.length; i += 8192) {
      s += String.fromCharCode.apply(null, b.subarray(i, i + 8192));
    }
    return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  }

  // POST JSON to a path next to this page. Never throws: gives {ok, body}.
  function post(path, obj) {
    return fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(obj)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (body) {
        return { ok: r.ok, status: r.status, body: body };
      });
    }).catch(function () {
      return { ok: false, status: 0, body: { error: 'Could not reach the site. Check your connection.' } };
    });
  }

  function say(el, text, bad) {
    if (!el) return;
    el.textContent = text || '';
    el.className = bad ? 'note bad' : 'note';
    el.style.display = text ? '' : 'none';
  }

  function cancelled(e) {
    return e && (e.name === 'NotAllowedError' || e.name === 'AbortError');
  }

  // --------------------------------------------------------------- sign in
  function initSignin() {
    var btn = document.getElementById('pk-btn');
    var msg = document.getElementById('pk-msg');
    var nextEl = document.querySelector('input[name="next"]');
    var next = nextEl ? nextEl.value : '';
    var running = null;       // AbortController of a prompt in flight

    if (!supported) { return; }
    var orb = document.getElementById('pk-orbwrap');
    if (orb) { orb.style.display = ''; }
    btn.style.display = '';
    // The clip was started on load; make sure it is running now that it is on screen.
    var clip = document.getElementById('bgvideo');
    if (clip && clip.getAttribute('src') && clip.paused) {
      var pp = clip.play();
      if (pp && pp.catch) { pp.catch(function () {}); }
    }

    function finish(token, cred) {
      var r = cred.response;
      var payload = {
        id: cred.id,
        rawId: toB64u(cred.rawId),
        type: cred.type,
        extensions: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
        response: {
          authenticatorData: toB64u(r.authenticatorData),
          clientDataJSON: toB64u(r.clientDataJSON),
          signature: toB64u(r.signature),
          userHandle: r.userHandle ? toB64u(r.userHandle) : null
        }
      };
      return post('passkey/login/finish', { token: token, credential: payload, next: next });
    }

    // One attempt. conditional=true waits quietly behind the email box's
    // autofill list; false shows the browser's passkey prompt right away.
    function attempt(conditional) {
      if (running) { running.abort(); }
      var ctrl = new AbortController();
      running = ctrl;
      return post('passkey/login/start', {}).then(function (s) {
        if (!s.ok) { throw { plain: s.body.error || 'Passkeys are not available right now.' }; }
        var pk = s.body.options.publicKey;
        pk.challenge = toBuf(pk.challenge);
        pk.allowCredentials = [];
        var req = { publicKey: pk, signal: ctrl.signal };
        if (conditional) { req.mediation = 'conditional'; }
        return navigator.credentials.get(req).then(function (cred) {
          if (!cred) { throw { name: 'NotAllowedError' }; }
          return finish(s.body.token, cred);
        });
      }).then(function (f) {
        if (!f.ok) {
          // The server says this passkey is not (or no longer) one of ours:
          // forget the "passkey on this device" note so the page stops
          // opening the prompt by itself.
          if (f.status === 401) { hereClear(); }
          throw { plain: f.body.error || 'That passkey was not accepted.' };
        }
        hereSet();
        window.location.replace(sameSitePath(f.body.next));
        return true;
      });
    }

    function showFail(e) {
      if (e && e.plain) { say(msg, e.plain, true); }
      else if (cancelled(e)) {
        say(msg, 'Passkey sign-in was cancelled. Type your email and password below, or press the button to try again.', false);
      } else {
        say(msg, 'The passkey did not work here. Use your password below.', true);
      }
    }

    // Quiet background listener for the autofill list. Starts only when the
    // browser supports it; failing silently is fine because it is a bonus.
    function armConditional() {
      if (!(PublicKeyCredential.isConditionalMediationAvailable)) { return; }
      PublicKeyCredential.isConditionalMediationAvailable().then(function (ok) {
        if (ok) { attempt(true).catch(function (e) { if (e && e.plain) { say(msg, e.plain, true); } }); }
      }).catch(function () {});
    }

    btn.addEventListener('click', function () {
      say(msg, 'Waiting for your passkey...', false);
      attempt(false).catch(function (e) { showFail(e); armConditional(); });
    });

    // Arm conditional UI (the browser's own autofill-integrated passkey
    // picker, via mediation: 'conditional' on navigator.credentials.get())
    // immediately on page load, for EVERY visitor whose browser supports it
    // -- not just first-time ones. This is what makes the prompt able to
    // appear with no tap at all, the moment the page is ready, rather than
    // waiting for the cinematic sequence below (which only runs once this
    // device is already marked as having used a passkey here). Safe to run
    // alongside that sequence: starting any new attempt always aborts
    // whichever one was already in flight (see the `running` guard inside
    // attempt()), so the two can never actually race each other.
    armConditional();

    // Without a passkey on this device: the plain page. The orb still plays,
    // but nothing opens by itself; the prompt opens when they tap the orb (or
    // pick a passkey from the email box's autofill list).
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var root = document.documentElement;
    if (!hereGet()) { return; }

    // With one: a short sequence, then the prompt. The page fades in (CSS), the
    // orb video fades and grows in once its first frame is ready, a light sweeps
    // round the orb once (a layer under the video), and the prompt opens
    // FIRST_FRAME_TO_PROMPT_MS later, never later than MAX_WAIT_MS after the
    // page started loading. A cancel does not trigger a retry.
    root.classList.add('cine');
    var prompted = false, framed = false;
    function openPrompt() {
      if (prompted) { return; }
      prompted = true;
      say(msg, 'Looking for a passkey on this device...', false);
      attempt(false).catch(function (e) { showFail(e); armConditional(); });
    }
    var sinceLoad = (window.performance && performance.now) ? performance.now() : 0;
    window.setTimeout(openPrompt, Math.max(0, MAX_WAIT_MS - sinceLoad));
    function firstFrame() {
      if (framed) { return; }
      framed = true;
      if (btn) { btn.classList.add('orb-in'); }
      if (orb && !reduce) { orb.classList.add('sheen'); }   // sweep + one glow pulse
      window.setTimeout(openPrompt, FIRST_FRAME_TO_PROMPT_MS);
    }
    var clipEl = document.getElementById('bgvideo');
    if (clipEl && clipEl.getAttribute('src')) {
      clipEl.addEventListener('error', firstFrame);
      if (clipEl.requestVideoFrameCallback) {
        clipEl.requestVideoFrameCallback(function () { firstFrame(); });
      } else {
        clipEl.addEventListener('playing', firstFrame, { once: true });
      }
    } else {
      firstFrame();   // no clip (reduced motion, data saving): same timing, no wait
    }
  }

  // --------------------------------------------------------------- offer
  // Where the person was heading, from the offer page ('' when not on it).
  // Same-site paths only, as a second guard after the server's own check.
  // 2026-11-20: one shared check, applied right before every redirect below
  // (and to the video address), so text read from the page or a reply can
  // never send the browser to another site or run as a javascript: address.
  // 2026-11-21 (T0965): ported from the Scratchpad copy onto the real source;
  // the invite flow's redirect (added after the cloud fix) is wrapped too.
  function sameSitePath(n) {
    n = String(n || '/');
    // No backslashes or control characters: browsers read "/\evil.com"
    // as "//evil.com".
    if (/[\\\u0000-\u001f\u007f]/.test(n)) { return '/'; }
    return (n.charAt(0) === '/' && n.charAt(1) !== '/') ? n : '/';
  }
  function safeNext() {
    var root = document.getElementById('pk-offer');
    if (!root) { return ''; }
    return sameSitePath(root.getAttribute('data-next') || '/');
  }

  // The "add a passkey?" page after a password sign-in. Nothing here may block
  // the person: every path ends by carrying on to where they were going.
  function initOffer() {
    var next = safeNext();
    if (!supported) { window.location.replace(sameSitePath(next)); return; }
    var msg = document.getElementById('pk-msg');
    document.getElementById('pk-offer-yes').addEventListener('click', function () {
      document.getElementById('pk-offer-buttons').style.display = 'none';
      document.getElementById('pk-add').style.display = '';
      var n = document.getElementById('n');
      if (n) { n.focus(); }
    });
    function answer(choice) {
      say(msg, 'One moment...', false);
      // Carry on whether or not the answer could be saved.
      post('passkey/offer', { choice: choice }).then(function () {
        window.location.replace(sameSitePath(next));
      });
    }
    document.getElementById('pk-offer-later').addEventListener('click', function () { answer('later'); });
    document.getElementById('pk-offer-never').addEventListener('click', function () { answer('never'); });
  }

  // --------------------------------------------------------------- account
  function initAccount() {
    var recover = document.getElementById('pk-recover');
    var offerNext = safeNext();
    var form = document.getElementById('pk-add') || recover;
    var msg = document.getElementById('pk-msg');

    if (form) {
      if (!supported) {
        say(msg, 'This browser cannot make passkeys.', true);
        form.style.display = 'none';
      }
      form.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var days = Number(form.elements['days'] ? form.elements['days'].value : 0);
        var pkName = form.elements['pk_name'] ? form.elements['pk_name'].value.trim() : '';
        var startPath = recover ? 'passkey/recover/start' : 'passkey/register/start';
        var startBody = recover
          ? { token: form.elements['token'].value, days: days }
          : { days: days, password: form.elements['password'].value, name: pkName };
        say(msg, 'Follow the prompt on your device...', false);
        post(startPath, startBody).then(function (s) {
          if (!s.ok) { throw { plain: s.body.error || 'Could not start that.' }; }
          var pk = s.body.options.publicKey;
          pk.challenge = toBuf(pk.challenge);
          pk.user.id = toBuf(pk.user.id);
          (pk.excludeCredentials || []).forEach(function (c) { c.id = toBuf(c.id); });
          return navigator.credentials.create({ publicKey: pk }).then(function (cred) {
            if (!cred) { throw { name: 'NotAllowedError' }; }
            var r = cred.response;
            return post('passkey/register/finish', {
              token: s.body.token,
              credential: {
                id: cred.id,
                rawId: toB64u(cred.rawId),
                type: cred.type,
                extensions: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
                response: {
                  attestationObject: toB64u(r.attestationObject),
                  clientDataJSON: toB64u(r.clientDataJSON)
                }
              }
            });
          });
        }).then(function (f) {
          if (!f.ok) { throw { plain: f.body.error || 'That passkey was not accepted.' }; }
          hereSet();   // a passkey now exists for this device
          if (offerNext) {
            window.location.replace(sameSitePath(offerNext));
          } else if (recover) {
            form.style.display = 'none';
            say(msg, 'Passkey added. Now go to the sign-in page and use it.', false);
          } else {
            window.location.reload();
          }
        }).catch(function (e) {
          if (e && e.plain) { say(msg, e.plain, true); }
          else if (cancelled(e)) { say(msg, 'Cancelled. Nothing was added.', false); }
          else { say(msg, 'That did not work. Nothing was added.', true); }
        });
      });
    }

    // Remove takes two presses, so one stray tap does nothing.
    Array.prototype.forEach.call(document.querySelectorAll('[data-remove]'), function (b) {
      var armed = false;
      b.addEventListener('click', function () {
        if (!armed) {
          armed = true;
          b.textContent = 'Press again to remove';
          setTimeout(function () { armed = false; b.textContent = 'Remove'; }, 4000);
          return;
        }
        post('passkey/remove', { id: b.getAttribute('data-remove') }).then(function (r) {
          if (r.ok) { window.location.reload(); }
          else { say(msg, r.body.error || 'Could not remove that.', true); armed = false; b.textContent = 'Remove'; }
        });
      });
    });

    // Two-press buttons for the bigger actions. After "lost", every session
    // has been ended, so the page goes to the sign-in screen.
    function twoPress(b, label, run) {
      var armed = false;
      b.addEventListener('click', function () {
        if (!armed) {
          armed = true;
          b.textContent = 'Press again to confirm';
          setTimeout(function () { armed = false; b.textContent = label; }, 4000);
          return;
        }
        run(b);
      });
    }
    function afterLost(r) {
      // Passkeys were removed: stop the sign-in page opening the prompt by itself.
      if (r.ok) { hereClear(); window.location.replace('./'); }
      else { say(msg, r.body.error || 'That did not work.', true); }
    }
    Array.prototype.forEach.call(document.querySelectorAll('[data-keep]'), function (b) {
      twoPress(b, 'Keep only this', function () {
        post('passkey/lost', { keep: b.getAttribute('data-keep') }).then(afterLost);
      });
    });
    var lostAll = document.getElementById('pk-lost-all');
    if (lostAll) {
      twoPress(lostAll, 'Remove all my passkeys', function () {
        post('passkey/lost', {}).then(afterLost);
      });
    }
    ['pk-only-on', 'pk-only-off'].forEach(function (id) {
      var b = document.getElementById(id);
      if (!b) { return; }
      b.addEventListener('click', function () {
        post('passkey/only', { on: id === 'pk-only-on' }).then(function (r) {
          if (r.ok) { window.location.reload(); }
          else { say(msg, r.body.error || 'That did not work.', true); }
        });
      });
    });
  }

  // --------------------------------------------------------------- invite
  // A guest invite: the admin console already made the account (label,
  // pages, expiry) before this code existed -- this page's only job is to
  // make the one passkey that account is allowed to hold, then sign the
  // guest straight in, no separate sign-in step. Unlike the recovery form
  // above (which only adds a passkey and sends the person to sign in by
  // hand), this chains register -> login automatically, because a guest who
  // just tapped a text-message link has no idea there is a second step.
  function initInvite() {
    var form = document.getElementById('pk-invite');
    var msg = document.getElementById('pk-msg');
    if (!form) { return; }
    if (!supported) {
      say(msg, 'This browser cannot make passkeys. Try another device.', true);
      form.style.display = 'none';
      return;
    }
    // Sign in with ONLY the passkey this page just made (2026-11-08). Before,
    // allowCredentials was empty, so a phone holding an older passkey for this
    // site (e.g. from a replaced invite) offered both; picking the old one was
    // refused, and the page then said "nothing was created" -- so the guest
    // pressed the now-used invite again. Now: the new key only; and if the
    // automatic prompt fails, the invite form is hidden and a Sign in button
    // (a fresh tap, which some phones require) takes its place.
    function signInWith(rawId) {
      say(msg, 'Signing you in...', false);
      return post('passkey/login/start', {}).then(function (s2) {
        if (!s2.ok) { throw { plain: s2.body.error || 'Could not start signing in.' }; }
        var pk2 = s2.body.options.publicKey;
        pk2.challenge = toBuf(pk2.challenge);
        pk2.allowCredentials = [{ type: 'public-key', id: rawId }];
        return navigator.credentials.get({ publicKey: pk2 }).then(function (cred2) {
          if (!cred2) { throw { name: 'NotAllowedError' }; }
          var r2 = cred2.response;
          return post('passkey/login/finish', {
            token: s2.body.token,
            next: '/',
            credential: {
              id: cred2.id,
              rawId: toB64u(cred2.rawId),
              type: cred2.type,
              extensions: cred2.getClientExtensionResults ? cred2.getClientExtensionResults() : {},
              response: {
                authenticatorData: toB64u(r2.authenticatorData),
                clientDataJSON: toB64u(r2.clientDataJSON),
                signature: toB64u(r2.signature),
                userHandle: r2.userHandle ? toB64u(r2.userHandle) : null
              }
            }
          });
        });
      }).then(function (f2) {
        if (!f2.ok) { throw { plain: f2.body.error || 'Could not sign you in.' }; }
        window.location.replace(sameSitePath(f2.body.next));
      });
    }
    function offerSignIn(rawId, why) {
      form.style.display = 'none';
      say(msg, 'Your passkey is saved. ' + (why ? why + ' ' : '') +
               'Tap Sign in below. Do not use the invite link again: it only works once.', false);
      var btn = document.getElementById('pk-signin-now');
      if (!btn) {
        btn = document.createElement('button');
        btn.id = 'pk-signin-now';
        btn.type = 'button';
        btn.className = 'btn main wide';
        btn.textContent = 'Sign in';
        msg.parentNode.insertBefore(btn, msg.nextSibling);
        btn.addEventListener('click', function () {
          signInWith(rawId).catch(function (e) {
            offerSignIn(rawId, e && e.plain ? e.plain : (cancelled(e) ? 'That was cancelled.' : 'That did not work.'));
          });
        });
      }
    }
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var code = form.elements['code'].value;
      var pkName = form.elements['pk_name'] ? form.elements['pk_name'].value.trim() : '';
      var made = null;
      say(msg, 'Follow the prompt on your device...', false);
      post('passkey/invite/start', { code: code, name: pkName }).then(function (s) {
        if (!s.ok) { throw { plain: s.body.error || 'Could not start that.' }; }
        var pk = s.body.options.publicKey;
        pk.challenge = toBuf(pk.challenge);
        pk.user.id = toBuf(pk.user.id);
        (pk.excludeCredentials || []).forEach(function (c) { c.id = toBuf(c.id); });
        return navigator.credentials.create({ publicKey: pk }).then(function (cred) {
          if (!cred) { throw { name: 'NotAllowedError' }; }
          var r = cred.response;
          made = cred.rawId;
          return post('passkey/register/finish', {
            token: s.body.token,
            credential: {
              id: cred.id,
              rawId: toB64u(cred.rawId),
              type: cred.type,
              extensions: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
              response: {
                attestationObject: toB64u(r.attestationObject),
                clientDataJSON: toB64u(r.clientDataJSON)
              }
            }
          });
        });
      }).then(function (f) {
        if (!f.ok) { made = null; throw { plain: f.body.error || 'That passkey was not accepted.' }; }
        hereSet();
        form.style.display = 'none';
        return signInWith(made).catch(function (e) {
          offerSignIn(made, e && e.plain ? e.plain : '');
        });
      }).catch(function (e) {
        if (made) { offerSignIn(made, ''); return; }
        if (e && e.plain) { say(msg, e.plain, true); }
        else if (cancelled(e)) { say(msg, 'Cancelled. Nothing was created.', false); }
        else { say(msg, 'That did not work. Nothing was created.', true); }
      });
    });
  }

  // ----------------------------------------------------------- owner tools
  // Every action is begun on the server, answered with the owner's OWN
  // passkey (fingerprint/face), then confirmed; a session cookie alone does
  // nothing here.
  function initOwner() {
    var msg = document.getElementById('pk-msg');
    function ownerDo(action) {
      if (!supported) { say(msg, 'This browser cannot use passkeys.', true); return; }
      say(msg, 'Confirm with your passkey...', false);
      post('owner/begin', action).then(function (s) {
        if (!s.ok) { throw { plain: s.body.error || 'Could not start that.' }; }
        var pk = s.body.options.publicKey;
        pk.challenge = toBuf(pk.challenge);
        (pk.allowCredentials || []).forEach(function (c) { c.id = toBuf(c.id); });
        return navigator.credentials.get({ publicKey: pk }).then(function (cred) {
          if (!cred) { throw { name: 'NotAllowedError' }; }
          var r = cred.response;
          return post('owner/confirm', {
            token: s.body.token,
            credential: {
              id: cred.id,
              rawId: toB64u(cred.rawId),
              type: cred.type,
              extensions: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
              response: {
                authenticatorData: toB64u(r.authenticatorData),
                clientDataJSON: toB64u(r.clientDataJSON),
                signature: toB64u(r.signature),
                userHandle: r.userHandle ? toB64u(r.userHandle) : null
              }
            }
          });
        });
      }).then(function (f) {
        if (!f.ok) { throw { plain: f.body.error || 'That was not confirmed.' }; }
        window.location.reload();
      }).catch(function (e) {
        if (e && e.plain) { say(msg, e.plain, true); }
        else if (cancelled(e)) { say(msg, 'Cancelled. Nothing was changed.', false); }
        else { say(msg, 'That did not work. Nothing was changed.', true); }
      });
    }
    Array.prototype.forEach.call(document.querySelectorAll('[data-oact="revoke"]'), function (b) {
      var armed = false, label = b.textContent;
      b.addEventListener('click', function () {
        if (!armed) {
          armed = true; b.textContent = 'Press again to confirm';
          setTimeout(function () { armed = false; b.textContent = label; }, 4000);
          return;
        }
        var a = { action: 'revoke', email: b.getAttribute('data-email') };
        if (b.getAttribute('data-id')) { a.id = b.getAttribute('data-id'); }
        ownerDo(a);
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll('.owner-label'), function (f) {
      f.addEventListener('submit', function (ev) {
        ev.preventDefault();
        ownerDo({ action: 'label', email: f.getAttribute('data-email'), label: f.elements['label'].value });
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll('.owner-cap'), function (f) {
      f.addEventListener('submit', function (ev) {
        ev.preventDefault();
        ownerDo({ action: 'cap', email: f.getAttribute('data-email'), cap: Number(f.elements['cap'].value) });
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll('.owner-guest'), function (f) {
      f.addEventListener('submit', function (ev) {
        ev.preventDefault();
        ownerDo({ action: 'guest', email: f.getAttribute('data-email'),
                  guest: f.elements['guest'].checked, expires: f.elements['expires'].value });
      });
    });
  }

  // ------------------------------------------------------- look and feel
  // (The page background, the navy sky with two drifting glows, is pure CSS:
  // no script is involved.)
  //
  // The looping clip inside the passkey orb. Never started for reduced motion
  // or data saving (the CSS glow shows instead), and hidden again if it cannot
  // load or play; the button works the same either way.
  function initVideo() {
    var v = document.getElementById('bgvideo');
    if (!v) { return; }
    var reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    var save = navigator.connection && navigator.connection.saveData;
    if (reduce || save) { return; }
    function hide() { v.style.display = 'none'; }
    v.addEventListener('error', hide);
    // 2026-11-20: only a relative file name from our own static folder is
    // accepted as the clip's address (no scheme, no leading slash, no "..").
    var clip = v.getAttribute('data-src') || '';
    if (!/^[A-Za-z0-9._\/-]+$/.test(clip) || clip.charAt(0) === '/' || clip.indexOf('..') !== -1) { return; }
    v.src = clip;
    v.style.display = '';
    var p = v.play();
    if (p && p.catch) { p.catch(hide); }
  }
  try { initVideo(); } catch (e) { /* decoration only */ }

  if (document.getElementById('pk-btn')) { initSignin(); }
  if (document.getElementById('pk-offer')) { initOffer(); }
  if (document.getElementById('pk-owner')) { initOwner(); }
  if (document.getElementById('pk-invite')) { initInvite(); }
  if (document.getElementById('pk-add') || document.getElementById('pk-recover') ||
      document.querySelector('[data-remove]') || document.getElementById('pk-only-on') ||
      document.getElementById('pk-only-off')) { initAccount(); }
})();
