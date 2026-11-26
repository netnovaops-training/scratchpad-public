#!/usr/bin/env python3
"""The drop page. Who you are decides where it goes.

Jason, 2026-09-11: "either of us need a book we add it to our collection
apparently to down load to paperless and be auto tagged based on who is
logined in ... and after the tag parsed into the right document folder so
they can be trained and everything and then parse them again to the epub
reader I have that can make you read PDFs and speak aloud to TTS".

===========================================================================
THERE IS NO "WHO IS THIS FOR" DROPDOWN, AND THAT IS THE DESIGN.

The household gate has already established who is asking, and Caddy passes
it in X-Auth-Email. Asking again would be worse than redundant: a dropdown
is a thing that can be left on its last value, and the failure mode is
Alex's medical record filed under Casey. The identity comes from the gate,
which is the one fact on the request nobody can mistype.

The ONE choice the page does offer is general vs private, and only for
video, and only to the two accounts that have the private library. That is
a destination, not an identity -- getting it wrong misfiles a film, and
you can move it.

===========================================================================
WHAT HAPPENS AFTER THE UPLOAD, because the point is that nothing else is
manual:

    a book      -> Paperless consume
                -> paperless_route.py     tag -> owner -> permissions
                -> paperless_sync.py      copied to that person's shelf
                -> Kavita /read           readable, per-person SSO
                -> chunks.py              page-anchored, into the study index
                -> narrate.py             optional: read aloud into /listen

    a comic     -> /manga/<person>        Kavita's manga library
    a video     -> general or private     Jellyfin
    paperwork   -> Paperless consume      tag -> owner -> the routing table
    anything else -> unsorted, with a sidecar saying why

Every one of those stages already existed before this page did. This is a
front door onto a pipeline, not a new pipeline.
"""
from __future__ import annotations

import themes  # T0942
import html
import os

import site_assets

# The two accounts with the private Jellyfin library. Kept in step with
# Jellyfin's own per-user grants and with `grab` in people.rs -- three
# places is two too many, but the alternative is this page asking Jellyfin
# at render time and failing open when Jellyfin is down.
# T0927 (2026-11-21): the people tables live in drop.toml now (dropconfig.py; no emails in the code).
# Same names as before, so every use of WHO, PRIVATE_OK, NAMES keeps working. Edit people in drop.toml, not here.
# WHO is re-exported (devices.py, app.py and abyss_keyring.py read portal.WHO), so it is imported on purpose.
from dropconfig import WHO, PRIVATE_OK, NAMES  # noqa: E402, F401

# T0964 (2026-11-21): the video card shows the real upload limit (app.MAX_MB reads the same setting); it said
# "10 GB" as fixed text, which was only true while compose set INTAKE_MAX_MB=10240.
def _max_gb() -> str:
    """T0964: the upload limit in GB, one decimal at most ("10", "1.5"); a non-number setting -> the 2048 default."""
    try:
        mb = int(os.environ.get("INTAKE_MAX_MB", "2048"))
    except ValueError:
        mb = 2048
    return f"{round(mb / 1024, 1):g}"


MAX_GB = _max_gb()


# "robin" added 2026-11-19 (T0925 A12): he had a WHO entry but no name, so his page greeted nobody.

# T0926 (2026-11-19, ui-ux-pro-max audit U1 U4 U5 U15): --on-accent fixes dark-mode button text (white on
# #6fd3b5 was 1.8:1, now dark ink, about 11:1); every tap target is at least 44 px; a visible focus ring;
# body text 16 px and nothing under 14 px.
CSS = """
:root{
  --ink:#12151a; --dim:#56606b; --line:#dfe3e8; --bg:#f7f8fa; --card:#fff;
  --accent:#1f6f5c; --on-accent:#fff; --accent-soft:#e8f2ef; --warn:#8a5a00; --warn-soft:#fdf4e3;
  --ok:#1f6f5c; --bad:#b42318;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --ink:#e8ecef; --dim:#a3adb8; --line:#2a3038; --bg:#0f1216; --card:#161a20;
  --accent:#6fd3b5; --on-accent:#0f1216; --accent-soft:#16302a; --warn:#e0b062; --warn-soft:#2c2415;
  --ok:#6fd3b5; --bad:#f87171;
}}
*{box-sizing:border-box}
[hidden]{display:none!important}   /* .nums{display:flex} beat the UA [hidden] rule (11-23) */
body{margin:0;background:var(--bg);color:var(--ink);
  font:16px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:24px;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--dim);margin:0 0 28px;font-size:15px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
  padding:20px;margin-bottom:16px}
.card h2{font-size:17px;margin:0 0 4px;letter-spacing:-.01em}
.card p.note{color:var(--dim);font-size:15px;margin:0 0 16px}
a{color:var(--accent)}
label{display:block;font-size:14px;color:var(--dim);margin:0 0 6px}
input[type=file],input[type=url]{width:100%;min-height:48px;padding:10px 12px;font:inherit;
  border:1px solid var(--line);border-radius:8px;background:var(--bg);
  color:var(--ink)}
.row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-top:14px}
button{font:inherit;font-weight:600;min-height:44px;padding:10px 20px;border-radius:8px;
  border:1px solid var(--accent);background:var(--accent);color:var(--on-accent);
  cursor:pointer}
button.quiet{background:none;color:var(--accent)}
button:disabled{opacity:.5;cursor:default}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:8px;
  overflow:hidden}
.seg label{margin:0;cursor:pointer;font-size:15px;color:var(--ink);background:var(--card)}
.seg input{position:absolute;opacity:0;pointer-events:none}
.seg input:checked+span{background:var(--accent-soft);color:var(--accent);
  font-weight:600}
.seg input:focus-visible+span{outline:3px solid var(--accent);outline-offset:-3px}
.seg span{display:flex;align-items:center;min-height:44px;padding:0 16px}
progress{width:100%;height:8px;margin-top:14px;accent-color:var(--accent)}
.out{list-style:none;padding:0;margin:14px 0 0;font-size:15px}
.out li{padding:8px 0;border-top:1px solid var(--line)}
.out li b{display:block;word-break:break-word}
.out li span{color:var(--dim)}
.out li.ok b::before{content:"\\2713  ";color:var(--ok)}
.out li.bad b::before{content:"\\2715  ";color:var(--bad)}
.links{display:flex;gap:8px;flex-wrap:wrap}
.links a{display:inline-flex;align-items:center;min-height:44px;font-size:15px;text-decoration:none;
  color:var(--accent);border:1px solid var(--line);border-radius:8px;padding:0 14px;
  background:var(--card)}
.links a:hover{border-color:var(--accent)}
.flag{background:var(--warn-soft);border-color:var(--warn)}
.flag h2{color:var(--warn)}
.nums{display:flex;justify-content:space-between;color:var(--dim);font-size:15px;margin-top:6px;font-variant-numeric:tabular-nums}
fieldset.pick{border:0;padding:0;margin:14px 0 0;display:block}
fieldset.pick legend{font-size:14px;color:var(--dim);padding:0;margin:0 0 6px}
/* T0889 step 6 (2026-11-22): the link field. Errors show only after the person has typed and left the
   field, or tried to send (:user-invalid, modern-web-guidance validate-input-after-interaction); before
   that the empty field is neutral. Text + icon, not colour alone. :user-invalid is Baseline widely
   available, so there is no script fallback for older browsers. */
input[type=text]{width:100%;min-height:48px;padding:10px 12px;font:inherit;
  border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--ink)}
.hint{display:block;color:var(--dim);font-size:14px;margin:0 0 6px}
.error-msg{display:none;color:var(--bad);font-size:15px;margin-top:6px}
input:user-invalid{border-color:var(--bad);border-width:2px}
input:user-invalid+.error-msg{display:block}
.field+.field{margin-top:14px}
.visually-hidden:where(:not(:focus-within,:active)){position:absolute!important;clip-path:inset(50%)!important;
  overflow:hidden!important;width:1px!important;height:1px!important;margin:-1px!important;padding:0!important;
  border:0!important;white-space:nowrap!important}
code{font-family:ui-monospace,Consolas,monospace;font-size:14px}
"""

JS = """
// T0926 U3 (2026-11-19): the Drop page sends through the same engine as a phone share (atomic.js): 4 MB
// pieces, each SHA-256-checked, resumed after a dropped signal, plain-words results. Before, each file
// went in ONE request and a dropped signal lost it. Still up to 3 files at a time (Jason 2026-11-19:
// "set up multiple file shares"), so one bad file never sinks the rest.
// While a sign-in has expired, the page waits (files stay in memory) and "Sign in" opens a new tab;
// after signing in there, "Try again now" here carries on. Closing THIS tab does lose the queue
// (a share from the phone's share sheet survives that: it is kept on the phone, see /sending).
function wire(formId, outId, barId, hint){
  const f=document.getElementById(formId), o=document.getElementById(outId),
        b=document.getElementById(barId), again=f.querySelector('.again');
  again.onclick=()=>{ again.hidden=true; Atomic.poke(); };
  f.addEventListener('submit', async e=>{
    e.preventDefault();
    const input=f.querySelector('input[type=file]'), files=[...input.files];
    if(!files.length) return;
    const btn=f.querySelector('button[type=submit]'); btn.disabled=true; o.replaceChildren();
    const total=files.reduce((s,x)=>s+x.size,0)||1, done=new Array(files.length).fill(0);
    b.hidden=false; b.value=0;
    // review 11-23 #6: percent, speed and time left under the bar (same numbers as /sending)
    const nums=document.getElementById(barId.replace('b','n')), t0=performance.now();
    if(nums){ nums.hidden=false; nums.textContent=''; }
    const paint=()=>{ const got=done.reduce((s,v)=>s+v,0); b.value=got/total*100;
      if(!nums) return; const secs=(performance.now()-t0)/1000, rate=secs>1?got/secs:0;
      nums.replaceChildren(...[Math.floor(got/total*100)+'%', rate?Atomic.mb(rate)+'/s':'',
        rate&&got<total?Atomic.left((total-got)/rate):''].map(x=>{ const s=document.createElement('span');
        s.textContent=x; return s; })); };
    const shelfEl=f.querySelector('input[name=shelf]:checked');
    const opts={hint:hint||null, shelf:shelfEl?shelfEl.value:null};
    const rows=files.map(file=>{ const li=document.createElement('li'), n=document.createElement('b'),
      s=document.createElement('span'); n.textContent=file.name+' · '+Atomic.mb(file.size); s.textContent='Waiting…';
      li.append(n,s); o.appendChild(li); return s; });
    let next=0, allOk=true;
    const worker=async()=>{ while(next<files.length){ const i=next++, file=files[i], s=rows[i];
      const res=await Atomic.send({name:file.name, blob:file}, opts, {
        save:async()=>{},
        sending:n=>{ again.hidden=true; done[i]=n; paint(); s.textContent='Sending…'; },
        progress:n=>{ done[i]=n; paint(); s.textContent='Sending… '+Math.floor(n/(file.size||1)*100)+'%'; },
        sorting:more=>{ s.textContent=more?'Still sorting…':'Sorting…'; },
        waiting:()=>{ again.hidden=false; s.textContent='Signal lost. It will carry on by itself.'; },
        busy:()=>{ again.hidden=false; s.textContent='The server is busy. Trying again…'; },   /* T0931 item 21, 2026-11-20 */
        signin:()=>{ again.hidden=false; const a=document.createElement('a'); a.href='./'; a.target='_blank';
          a.rel='noopener'; a.textContent='Sign in'; s.replaceChildren('Sign-in expired. ', a,
          ' in a new tab, then tap Try again now. Nothing is lost.'); },
      });
      done[i]=file.size; paint();
      const bad=Atomic.bad(res); allOk=allOk&&!bad;
      s.parentNode.className=bad?'bad':'ok'; s.textContent=res.map(Atomic.plain).join(' ');
    } };
    await Promise.all([worker(),worker(),worker()]);
    btn.disabled=false; b.hidden=true; again.hidden=true; if(nums) nums.hidden=true;
    if(allOk) f.reset();
  });
}
// Shares from the phone's share sheet that have not finished sending (kept on the phone by the service
// worker) get a way back from here (U10), instead of only by re-opening the share.
(function(){
  try{ const r=indexedDB.open('atomic-share',1);
    r.onupgradeneeded=()=>r.result.createObjectStore('pending',{keyPath:'key'});
    r.onsuccess=()=>{ try{ const q=r.result.transaction('pending').objectStore('pending').count();
      q.onsuccess=()=>{ if(q.result>0){ const w=document.getElementById('waiting');
        w.querySelector('span').textContent=q.result===1?'1 share from your phone is waiting to send.'
          :q.result+' shares from your phone are waiting to send.'; w.hidden=false; } }; }catch { /* not available: nothing to do */ } };
  }catch { /* not available: nothing to do */ }
})();
// T0889 step 6 (2026-11-22): keep aria-invalid and the error text in step with :user-invalid on the link
// field (taken from modern-web-guidance validate-input-after-interaction). The error id is listed in
// aria-describedby only while it shows: a hidden element that aria-describedby points at is still read out.
// One polite live region announces a new error half a second after the field is left.
(function(){
  const say=document.createElement('div'); say.setAttribute('aria-live','polite');
  say.className='visually-hidden'; document.body.append(say); let t;
  const link=(el,shown)=>{ const id=el.dataset.error; if(!id) return;
    const ids=(el.getAttribute('aria-describedby')||'').split(/\\s+/).filter(x=>x&&x!==id);
    if(shown) ids.push(id);
    if(ids.length) el.setAttribute('aria-describedby',ids.join(' ')); else el.removeAttribute('aria-describedby'); };
  const sync=(el,announce)=>{ if(!el.dataset||!el.dataset.error) return;
    const bad=el.matches(':user-invalid'); el.setAttribute('aria-invalid',bad?'true':'false'); link(el,bad);
    if(bad&&announce){ const m=document.getElementById(el.dataset.error); clearTimeout(t); say.textContent='';
      t=setTimeout(()=>{ say.textContent='Link: '+m.textContent.replace(/\\s+/g,' ').trim(); },500); } };
  document.querySelectorAll('[data-error]').forEach(el=>sync(el,false));
  document.addEventListener('blur',e=>sync(e.target,true),true);
  document.addEventListener('input',e=>sync(e.target,false));
  document.addEventListener('invalid',e=>sync(e.target,false),true);  // focus moves there; no double announce
  const f3=document.getElementById('f3');
  if(f3) f3.addEventListener('submit',()=>{ const b=f3.querySelector('button[type=submit]');
    b.disabled=true; b.textContent='Saving…'; });   // only fires when the field is valid; stops a double send
})();
"""


# Review 11-23 #10 (Jason: "only me"): the admin tools are listed for this person id only.
ADMIN = os.environ.get("INTAKE_ADMIN", "jason").strip()


def _theme_css() -> str:
    """Review 11-23 #3: the theme's own colours for this page, if the theme has them (horizon does).

    `:root:not(#_)` outranks the dark-mode block above (`:root:not([data-theme=light])`), so the theme's
    palette applies in light and dark alike, the way /sending always looked."""
    t = themes.current()
    return f":root:not(#_){{{t['portal_root']}}}\n.mark{{vertical-align:-5px;margin-right:8px}}" if t.get("portal_root") else ""


# Step 7 (2026-11-24, audit High 2a): the style and script of this page were inline (and the SW was registered by
# an inline line). They are files now (/assets/portal.css, /assets/portal.js, SRI in the page), so the strict site
# CSP (site_assets.SITE_CSP: script-src 'self', style-src 'self', Trusted Types) holds on the Drop page too.
SW_REGISTER = """/* global Atomic */   // from /atomic.js, loaded (deferred) just before this file
// Step 7: was an inline <script> in the page head. Trusted Types are required on this origin, so the worker URL goes
// through a policy that allows only /sw.js.
if ('serviceWorker' in navigator) navigator.serviceWorker.register(globalThis.trustedTypes
  ? trustedTypes.createPolicy('novashare-portal-sw', {createScriptURL: u => { if (u !== '/sw.js') throw new Error('blocked'); return u; }}).createScriptURL('/sw.js')
  : '/sw.js');
"""


def _portal_css() -> str:
    return CSS + _theme_css() + "\n" + themes.svg_parts(themes.current().get("mark", ""))[1] + "\n"


def _portal_js() -> str:
    return (SW_REGISTER + JS + "\nwire('f1','o1','b1',null); wire('f2','o2','b2','video');\n"
            + themes.svg_parts(themes.current().get("mark", ""))[2] + "\n")


site_assets.register("portal.css", _portal_css)
site_assets.register("portal.js", _portal_js)


def page(email: str, person: str | None) -> str:
    import atomic_ui
    name = NAMES.get(person or "", "")
    greet = f"Signed in as {html.escape(email)}"
    if name:
        greet += f" — filing as <strong>{html.escape(name)}</strong>"

    private_block = ""
    if email in PRIVATE_OK:
        # review 11-23 #11: a question for the two choices (fieldset + legend), not two bare words
        private_block = """
        <fieldset class="row pick">
          <legend>Which video library?</legend>
          <div class="seg">
            <label><input type="radio" name="shelf" value="general" checked>
              <span>General</span></label>
            <label><input type="radio" name="shelf" value="private">
              <span>Private</span></label>
          </div>
        </fieldset>"""

    unknown = ""
    if not person:
        unknown = """
      <div class="card flag">
        <h2>This account is not mapped to a person</h2>
        <p class="note">Your files are kept with a note instead of being filed.
        Nothing is lost. Ask Jason to add your account.</p>
      </div>"""

    # T0889 step 6 (2026-11-22): the card says what will really happen to a YouTube link. While METUBE_URL
    # is unset (11-22: the MeTube download folder is still an open Ask on T0889) share.py prints YouTube
    # pages to PDF like any page, so the card must not promise an mp3.
    import link_route
    if link_route.enabled():
        yt_note = ("A YouTube link is saved as a video (mp4). Put <strong>mp3</strong> or "
                   "<strong>song</strong> in the note to keep only the sound.")
    else:
        yt_note = "YouTube links are saved as a PDF for now; the music and video downloader is not switched on yet."

    admin_links = ""
    if person and person == ADMIN:       # review 11-23 #10: admin tools for Jason only; #8 #13: clear names, no arrows
        admin_links = """
      <a href="routes">Routes</a>
      <a href="https://abyss.home.example.net/new">Abyss</a>
      <a href="https://grab.home.example.net/">Grab tool (advanced)</a>
      <a href="https://sandbox.home.example.net/">Sandbox browser</a>
      <a href="https://torrent.home.example.net/">qBittorrent</a>"""

    # U6 (2026-11-19): a real document (doctype, lang, charset); U7: results are announced (aria-live).
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<!-- served by ai-stack/intake -->
<title>{html.escape(themes.current()['name'])}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="{themes.current()['theme_color']}">
{site_assets.css_tag("portal.css")}
<div class="wrap">
  <h1>{themes.svg_parts(themes.current().get('mark', ''))[0]}{html.escape(themes.current()['name'])}</h1>
  <p class="sub">{greet}</p>
  {unknown}

  <div class="card flag" id="waiting" hidden>
    <h2><span></span></h2>
    <div class="row links"><a href="sending">Carry on sending</a></div>
  </div>

  <div class="card">
    <h2>A book, a comic, or paperwork</h2>
    <p class="note">Filed under your name. Books go to the
      <a href="https://example.org/read/">Reader</a>; paperwork goes to Paperless.
      Big files carry on by themselves after a lost signal.</p>
    <form id="f1" action="upload" method="post" enctype="multipart/form-data">
      <label for="u1">Files</label>
      <input id="u1" type="file" name="file" multiple required>
      <div class="row"><button type="submit">Upload to library</button>
        <button type="button" class="quiet again" hidden>Try again now</button></div>
      <progress id="b1" max="100" value="0" aria-label="Sent so far" hidden></progress>
      <div class="nums" id="n1" aria-hidden="true" hidden></div>
      <ul class="out" id="o1" aria-live="polite"></ul>
    </form>
  </div>

  <div class="card">
    <h2>A video</h2>
    <p class="note">Goes straight to Jellyfin. Up to {MAX_GB} GB per file.</p>
    <form id="f2" action="upload" method="post" enctype="multipart/form-data">
      <label for="u2">Videos</label>
      <input id="u2" type="file" name="file" accept="video/*" multiple required>
      {private_block}
      <div class="row"><button type="submit">Upload videos</button>
        <button type="button" class="quiet again" hidden>Try again now</button></div>
      <progress id="b2" max="100" value="0" aria-label="Sent so far" hidden></progress>
      <div class="nums" id="n2" aria-hidden="true" hidden></div>
      <ul class="out" id="o2" aria-live="polite"></ul>
    </form>
  </div>

  <!-- T0889 step 6 (2026-11-22): a link pasted here takes the same road as a link shared from the phone's
       share sheet (POST share, share.py): direct PDF / Word / photo links are downloaded and filed, articles
       become a PDF of their text, anything else a PDF of the page; nothing is dropped. Plain form post, no
       script needed; share.py answers with its own result page and a link back here. -->
  <div class="card">
    <h2>A link</h2>
    <p class="note">A link to a PDF, Word file or photo is filed like an upload. An article is saved
      as a PDF of its text. {yt_note}</p>
    <form id="f3" action="share" method="post" enctype="multipart/form-data">
      <div class="field">
        <label for="u3">Link</label>
        <span id="u3-hint" class="hint">The full web address, starting with https://</span>
        <input id="u3" type="url" name="url" inputmode="url" autocomplete="off" autocapitalize="off"
          spellcheck="false" required pattern="https?://.+" placeholder="https://"
          aria-describedby="u3-hint" data-error="u3-error">
        <div id="u3-error" class="error-msg"><span role="img" aria-label="Error">&#x2715;</span>
          Paste the whole address, starting with https:// (for example https://example.com/page).</div>
      </div>
      <div class="field">
        <label for="u3t">Note (optional)</label>
        <input id="u3t" type="text" name="text" autocomplete="off" maxlength="300">
      </div>
      <div class="row"><button type="submit">Save the link</button></div>
    </form>
  </div>

  <div class="card">
    <h2>Other apps</h2>
    <div class="links">
      <a href="https://example.org/read/">Reader</a>
      <a href="https://media.home.example.net/">Jellyfin</a>{admin_links}
    </div>
  </div>
</div>
<script defer src="/atomic.js" integrity="{atomic_ui._atomic_sri()}"></script>
{site_assets.js_tag("portal.js")}"""
