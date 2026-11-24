#!/usr/bin/env python3
"""abyss_patch.py - Novashare side of Abyss step 5 (2026-11-24): "Send as Abyss" + the Abyss row in the gateway table.

    python3 abyss_patch.py <copy of ~/nuc-homelab/intake>      (SWAP-PATCH.sh step 1; patches that copy only)

1. gateway.py: an "Abyss" row (computer NUC, container abyss, grant Jason only) and its health check
   GET $ABYSS_HEALTH_URL/healthz (default http://abyss:8300, the compose service on nuc-homelab_default; checked
   from the live intake container 2026-11-24: it answers, 403 before Abyss step 5 opens /healthz to any peer).
   Green only on 200.
2. atomic_ui.py: on the share result page (/sending), Jason only, a "Send as Abyss" button once the share is done
   and had photos or videos. It opens abyss /new?from=novashare in a new window, waits for Abyss's
   {type:"abyss-ready"} (exact Abyss origin AND that window), then posts the files' bytes as transferable
   ArrayBuffers to that origin only. The bytes are encrypted in the Abyss page before anything is uploaded.
   Others get the same page as before (the placeholder is removed).
3. portal.py: an "Abyss" link in Jason's admin links.
Each edit asserts its anchor is there exactly once, so a changed live file stops the patch (nothing half-done).
"""
import pathlib
import sys

W = pathlib.Path(sys.argv[1])


def patch(name, pairs):
    p = W / name
    s = p.read_text(encoding="utf-8")
    for a, b in pairs:
        assert s.count(a) == 1, f"{name}: anchor not found once: {a[:70]!r}"
        s = s.replace(a, b)
    p.write_text(s, encoding="utf-8", newline="\n")
    print("patched", name)


patch("gateway.py", [
    ('''         "grant": _env("INTAKE_VIDEO_GRANT", "jellyfin"), "check": "mount:/srv/media/general"},
    ]''', '''         "grant": _env("INTAKE_VIDEO_GRANT", "jellyfin"), "check": "mount:/srv/media/general"},
        # Abyss step 5 (2026-11-24): "Send as Abyss" on the share result page hands photos/videos to Abyss in the
        # browser (postMessage); Abyss encrypts them there. The server row is for the health dot and the record.
        {"name": "Abyss", "match": "Send as Abyss (photos, videos)", "send": "one-time view-only link",
         "computer": NUC, "container": "abyss", "via": _env("ABYSS_URL", "https://abyss.home.example.net"),
         "grant": "Jason only", "check": "abyss"},
    ]'''),
    ('''    if name.startswith("mount:"):''', '''    if name == "abyss":                     # Abyss step 5: green only on 200 from /healthz
        a = _env("ABYSS_HEALTH_URL", "http://abyss:8300").rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=3) as c:
                r = await c.get(a + "/healthz")
            return "green" if r.status_code == 200 else "red"
        except httpx.HTTPError:
            return "red"
    if name.startswith("mount:"):'''),
])

patch("atomic_ui.py", [
    ('''<div class=row><button id=again hidden>Try again now</button><a class=done href="./">Done</a></div>''',
     '''<div class=row><button id=again hidden>Try again now</button><!--ABYSS-BUTTON--><a class=done href="./">Done</a></div>'''),
    ('''$('again').onclick = () => { again(false); Atomic.poke(); };
''', '''$('again').onclick = () => { again(false); Atomic.poke(); };
// SEND AS ABYSS (Abyss step 5, 2026-11-24; Jason only: the button is only in his copy of this page). The photos and
// videos of this share are kept in memory (the phone's own copy) and, on a tap, handed to Abyss's send page in a
// new window by postMessage: Abyss says {type:'abyss-ready'} first; only a message from Abyss's exact origin AND
// from that window counts; the bytes go as transferable ArrayBuffers to that origin only. Abyss encrypts them in
// its page before anything is uploaded (its server only ever gets ciphertext).
const AB = $('abyss'), media = [];
function keepMedia(f) {
  const t = String(f.type || (f.blob && f.blob.type) || '');
  if (AB && f.blob && /^(image|video)\\//.test(t) && media.length < 20) media.push({name: f.name, type: t, blob: f.blob});
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
      } catch (err) { say('This phone could not read the files again. Share them again to send as Abyss.'); }
    } else if (e.data.type === 'abyss-received') removeEventListener('message', onMsg);
  };
  addEventListener('message', onMsg);
};
'''),
    ('''      const r = row(f.name + ' · ' + mb(f.blob.size));
''', '''      const r = row(f.name + ' · ' + mb(f.blob.size));
      keepMedia(f);                                            // Abyss step 5 (Jason only; no-op for others)
'''),
    ('''  working = false; $('title').textContent = anyBad ? 'Finished' : 'Shared';
''', '''  working = false; $('title').textContent = anyBad ? 'Finished' : 'Shared';
  showAbyss();
'''),
    ('''@router.get("/sending", response_class=HTMLResponse)
def sending():
    return HTMLResponse(PAGE, headers={"Cache-Control": "no-store"})''', '''ABYSS_URL = os.environ.get("ABYSS_URL", "https://abyss.home.example.net").strip().rstrip("/")


@router.get("/sending", response_class=HTMLResponse)
def sending(x_auth_email: str | None = Header(default=None)):
    """Abyss step 5: Jason's copy has the "Send as Abyss" button; everyone else gets the page as before."""
    import app
    import portal
    me = app._person((x_auth_email or "").strip().lower()) if x_auth_email else None
    ok = me == portal.ADMIN and re.match(r"^https://[A-Za-z0-9.-]+(:[0-9]{1,5})?$", ABYSS_URL)
    btn = f'<button id=abyss hidden data-to="{html.escape(ABYSS_URL)}">Send as Abyss</button>' if ok else ""
    return HTMLResponse(PAGE.replace("<!--ABYSS-BUTTON-->", btn), headers={"Cache-Control": "no-store"})'''),
    ('''from fastapi import APIRouter
from fastapi.responses import HTMLResponse
''', '''import html
import os
import re

from fastapi import APIRouter, Header
from fastapi.responses import HTMLResponse
'''),
])

patch("portal.py", [
    ('''      <a href="routes">Routes</a>
''', '''      <a href="routes">Routes</a>
      <a href="https://abyss.home.example.net/new">Abyss</a>
'''),
])
