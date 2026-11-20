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

import html

# The two accounts with the private Jellyfin library. Kept in step with
# Jellyfin's own per-user grants and with `grab` in people.rs -- three
# places is two too many, but the alternative is this page asking Jellyfin
# at render time and failing open when Jellyfin is down.
PRIVATE_OK = {"user@example.com", "user@example.com"}

WHO = {
    "user@example.com": "jason",
    "user@example.com": "jason",
    "user@example.com": "alex",
    "user@example.com": "casey",
    "user@example.com": "mom",
    # Added 2026-09-26. Robin is a full SSO account with grants on
    # recipe, paperless and read -- and was unknown HERE, so every
    # file he sent was filed as "no person" and went to unsorted.
    # Two lists of who exists, and only one of them knew about him.
    'user@example.com': 'robin',
}

# "robin" added 2026-11-19 (T0925 A12): he had a WHO entry but no name, so his page greeted nobody.
NAMES = {"jason": "Jason", "alex": "Alex", "casey": "Casey", "mom": "Mom", "robin": "Robin"}

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
footer{color:var(--dim);font-size:14px;margin-top:26px;line-height:1.7}
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
    const paint=()=>{ b.value=done.reduce((s,v)=>s+v,0)/total*100; };
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
        progress:n=>{ done[i]=n; paint(); },
        sorting:more=>{ s.textContent=more?'Still sorting…':'Sorting…'; },
        waiting:()=>{ again.hidden=false; s.textContent='Signal lost. It will carry on by itself.'; },
        signin:()=>{ again.hidden=false; const a=document.createElement('a'); a.href='./'; a.target='_blank';
          a.rel='noopener'; a.textContent='Sign in'; s.replaceChildren('Sign-in expired. ', a,
          ' in a new tab, then tap Try again now. Nothing is lost.'); },
      });
      done[i]=file.size; paint();
      const bad=Atomic.bad(res); allOk=allOk&&!bad;
      s.parentNode.className=bad?'bad':'ok'; s.textContent=res.map(Atomic.plain).join(' ');
    } };
    await Promise.all([worker(),worker(),worker()]);
    btn.disabled=false; b.hidden=true; again.hidden=true;
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
          :q.result+' shares from your phone are waiting to send.'; w.hidden=false; } }; }catch(_){} };
  }catch(_){}
})();
"""


def page(email: str, person: str | None) -> str:
    name = NAMES.get(person or "", "")
    greet = f"Signed in as {html.escape(email)}"
    if name:
        greet += f" — filing as <strong>{html.escape(name)}</strong>"

    private_block = ""
    if email in PRIVATE_OK:
        private_block = """
        <div class="row">
          <div class="seg">
            <label><input type="radio" name="shelf" value="general" checked>
              <span>General</span></label>
            <label><input type="radio" name="shelf" value="private">
              <span>Private</span></label>
          </div>
        </div>"""

    unknown = ""
    if not person:
        unknown = """
      <div class="card flag">
        <h2>This account is not mapped to a person</h2>
        <p class="note">Uploads will land in <code>unsorted/</code> with a note
        rather than being filed. Nothing is lost -- but nothing is routed
        either. Jason can add the mapping.</p>
      </div>"""

    # U6 (2026-11-19): a real document (doctype, lang, charset); U7: results are announced (aria-live).
    return f"""<!doctype html><html lang="en"><meta charset="utf-8">
<!-- served by ai-stack/intake -->
<title>Atomic Share</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="manifest" href="manifest.webmanifest">
<meta name="theme-color" content="#0b1026">
<script>if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js");</script>
<style>{CSS}</style>
<div class="wrap">
  <h1>Drop</h1>
  <p class="sub">{greet}</p>
  {unknown}

  <div class="card flag" id="waiting" hidden>
    <h2><span></span></h2>
    <div class="row links"><a href="sending">Carry on sending &rarr;</a></div>
  </div>

  <div class="card">
    <h2>A book, a comic, or paperwork</h2>
    <p class="note">Filed under your name automatically. Books reach
      <a href="https://example.org/read/">/read</a> and the study
      index; paperwork goes to Paperless and is routed by its tag.
      Big files are sent in pieces and carry on after a lost signal.</p>
    <form id="f1" action="upload" method="post" enctype="multipart/form-data">
      <label for="u1">Files</label>
      <input id="u1" type="file" name="file" multiple required>
      <div class="row"><button type="submit">Upload</button>
        <button type="button" class="quiet again" hidden>Try again now</button></div>
      <progress id="b1" max="100" value="0" aria-label="Sent so far" hidden></progress>
      <ul class="out" id="o1" aria-live="polite"></ul>
    </form>
  </div>

  <div class="card">
    <h2>A video</h2>
    <p class="note">Goes straight to Jellyfin. Up to 10 GB per file.</p>
    <form id="f2" action="upload" method="post" enctype="multipart/form-data">
      <label for="u2">Videos</label>
      <input id="u2" type="file" name="file" accept="video/*" multiple required>
      {private_block}
      <div class="row"><button type="submit">Upload</button>
        <button type="button" class="quiet again" hidden>Try again now</button></div>
      <progress id="b2" max="100" value="0" aria-label="Sent so far" hidden></progress>
      <ul class="out" id="o2" aria-live="polite"></ul>
    </form>
  </div>

  <div class="card">
    <h2>Elsewhere</h2>
    <p class="note">Everything below runs through the VPN on the Pi.</p>
    <div class="links">
      <a href="https://grab.home.example.net/">Paste a URL &rarr;</a>
      <a href="https://sandbox.home.example.net/">Sandbox browser</a>
      <a href="https://torrent.home.example.net/">qBittorrent</a>
      <a href="https://media.home.example.net/">Jellyfin</a>
      <a href="https://example.org/read/">Reader</a>
    </div>
  </div>

  <footer>
    The sandbox browser and qBittorrent sit behind the same sign-in (T0872, 2026-11-18);
    the sandbox asks its own login once (saved in Proton Pass).<br>
    Nothing here asks who a file is for: the sign-in already said.
  </footer>
</div>
<script src="atomic.js"></script>
<script>{JS}
wire('f1','o1','b1',null); wire('f2','o2','b2','video');</script>"""
