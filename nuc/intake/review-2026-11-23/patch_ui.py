#!/usr/bin/env python3
"""patch_ui.py - Novashare screens review 2026-11-23 (frontend.md), applied to a COPY of intake.

    python3 patch_ui.py COPY_OF_INTAKE

Jason approved all high+medium (11-23), plus "admin links: only me". Fixed:
  #1  one name: "Drop" -> the theme's name (drop.toml [brand], live "Novashare") in the heading, the back link,
      engine messages (atomic.js) and the fallback page.
  #2  build notes gone: footer deleted; cards in plain words (no ticket numbers, VPN, "routed by its tag").
  #3  the Drop page takes the theme's colours (horizon: night navy, cyan, orange) and a small mark by the heading,
      so start screen, Drop page and /sending look like one app. A theme without "portal_root" keeps the old look.
  #4  error visible at any progress: horizon's star turns the error colour on body.bad (the ring hid at 0 %).
  #5  fallback result page: plain sentences (same rules as Atomic.plain), theme colours, theme name.
  #6  Drop-page uploads show percent, speed and time left under the bar, and a percent per file.
  #7  "Nothing waiting to send." hides the empty bar and 0 %.
  #8  buttons named by result ("Upload to library", "Upload videos"); grab link "Grab tool (advanced)".
  #9  done stays visible: the photon ring turns the ok colour at 100 %.            (low, cheap)
  #10 grab, sandbox browser, qBittorrent: shown only to INTAKE_ADMIN (person id, default "jason").
  #11 the General/Private choice is a fieldset with a legend.                    (low)
  #12 the end of a share is a full-width "Done" button.                         (low)
  #13 no "→" in link text.                                                       (low)
  #14 debris fades out past 90 %.                                                (low)
  #15 the tab title follows the state (percent, Shared, Needs a look).          (low)
Refuses the live folder, an already patched copy, or any old text not found exactly once.
Variables: folder, files (name -> text), swap() (exact-once replace).
"""
import sys
from pathlib import Path

folder = Path(sys.argv[1]).expanduser()
if folder.resolve() == (Path.home() / "nuc-homelab" / "intake").resolve():
    raise SystemExit("refusing to patch the LIVE intake folder")
files = {n: (folder / n).read_text(encoding="utf-8") for n in ("portal.py", "atomic_ui.py", "themes.py", "share.py")}
if "portal_root" in files["themes.py"]:
    raise SystemExit("already patched")


def swap(name, old, new):
    if files[name].count(old) != 1:
        raise SystemExit(f"{name}: expected exactly one copy of {old[:70]!r}, found {files[name].count(old)}")
    files[name] = files[name].replace(old, new)


# ===================================================== themes.py ============================================
swap("themes.py", '''  body.done .atom .star .nucleus,body.bad .atom .star .nucleus{fill:url(#hzstar)}''',
     '''  body.done .atom .star .nucleus{fill:url(#hzstar)}
  /* review 11-23 #4: an error shows at ANY progress (the red ring sat in .hole, scaled to 0 at 0 %) */
  body.bad .atom .star .nucleus{fill:var(--bad)}
  body.bad .atom .hole{transform:scale(max(.35,var(--g)))}
  /* #9: finished stays visible after the gulp (and with reduced motion): the photon ring turns ok */
  body.done .atom.hz-full .ring{stroke:var(--ok);transition:stroke .6s ease}''')
swap("themes.py", '''  .atom .infall{transform-origin:48px 48px;transform:scale(calc(1 - var(--g) * 0.6));transition:transform .7s ease}''',
     '''  .atom .infall{transform-origin:48px 48px;transform:scale(calc(1 - var(--g) * 0.6));opacity:calc(1 - max(0, var(--g) - 0.9) * 10);transition:transform .7s ease,opacity .7s ease}''')
swap("themes.py", '''HORIZON = {
    "root": ATOMIC["root"],''', '''HORIZON = {
    "root": ATOMIC["root"],
    # Review 11-23 #3: the Drop page (portal.py) uses these tokens too, so it matches /sending and the splash.
    # Contrast on --bg #0b1026: ink 16:1, dim 7.6:1, accent 10:1; on-accent ink on the cyan button 10:1.
    "portal_root": "--ink:#e8ecff;--dim:#9aa3c7;--line:#26305a;--bg:#0b1026;--card:#131a3a;--accent:#5ad1ff;"
                   "--on-accent:#0b1026;--accent-soft:#16264a;--warn:#ffb46b;--warn-soft:#2a2238;--ok:#4ade80;--bad:#f87171;color-scheme:dark",
    # The small mark beside the Drop page heading: the app icon's black hole and disk, simplified.
    "mark": '<svg class="mark" width="30" height="30" viewBox="0 0 96 96" aria-hidden="true">'
            '<ellipse cx="48" cy="48" rx="44" ry="12" fill="none" stroke="#ffb46b" stroke-width="5" transform="rotate(-16 48 48)"/>'
            '<circle cx="48" cy="48" r="24" fill="#000"/><circle cx="48" cy="48" r="26" fill="none" stroke="#ffe6b0" stroke-width="4"/></svg>',''')

# ===================================================== portal.py ============================================
swap("portal.py", '''def page(email: str, person: str | None) -> str:''', '''# Review 11-23 #10 (Jason: "only me"): the admin tools are listed for this person id only.
ADMIN = os.environ.get("INTAKE_ADMIN", "jason").strip()


def _theme_css() -> str:
    """Review 11-23 #3: the theme's own colours for this page, if the theme has them (horizon does).

    `:root:not(#_)` outranks the dark-mode block above (`:root:not([data-theme=light])`), so the theme's
    palette applies in light and dark alike, the way /sending always looked."""
    t = themes.current()
    return f":root:not(#_){{{t['portal_root']}}}\\n.mark{{vertical-align:-5px;margin-right:8px}}" if t.get("portal_root") else ""


def page(email: str, person: str | None) -> str:''')
swap("portal.py", '''    private_block = ""
    if email in PRIVATE_OK:
        private_block = """
        <div class="row">
          <div class="seg">
            <label><input type="radio" name="shelf" value="general" checked>
              <span>General</span></label>
            <label><input type="radio" name="shelf" value="private">
              <span>Private</span></label>
          </div>
        </div>"""''', '''    private_block = ""
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
        </fieldset>"""''')
swap("portal.py", '''        <p class="note">Uploads will land in <code>unsorted/</code> with a note
        rather than being filed. Nothing is lost -- but nothing is routed
        either. Jason can add the mapping.</p>''', '''        <p class="note">Your files are kept with a note instead of being filed.
        Nothing is lost. Ask Jason to add your account.</p>''')
swap("portal.py", '''<title>{themes.current()['name']}</title>''', '''<title>{html.escape(themes.current()['name'])}</title>''')
swap("portal.py", '''<style>{CSS}</style>
<div class="wrap">
  <h1>Drop</h1>''', '''<style>{CSS}{_theme_css()}</style>
<div class="wrap">
  <h1>{themes.current().get('mark', '')}{html.escape(themes.current()['name'])}</h1>''')
swap("portal.py", '''<a href="sending">Carry on sending &rarr;</a>''', '''<a href="sending">Carry on sending</a>''')
swap("portal.py", '''    <p class="note">Filed under your name automatically. Books reach
      <a href="https://example.org/read/">/read</a> and the study
      index; paperwork goes to Paperless and is routed by its tag.
      Big files are sent in pieces and carry on after a lost signal.</p>''', '''    <p class="note">Filed under your name. Books go to the
      <a href="https://example.org/read/">Reader</a>; paperwork goes to Paperless.
      Big files carry on by themselves after a lost signal.</p>''')
swap("portal.py", '''      <div class="row"><button type="submit">Upload</button>
        <button type="button" class="quiet again" hidden>Try again now</button></div>
      <progress id="b1" max="100" value="0" aria-label="Sent so far" hidden></progress>''', '''      <div class="row"><button type="submit">Upload to library</button>
        <button type="button" class="quiet again" hidden>Try again now</button></div>
      <progress id="b1" max="100" value="0" aria-label="Sent so far" hidden></progress>
      <div class="nums" id="n1" aria-hidden="true" hidden></div>''')
swap("portal.py", '''      <div class="row"><button type="submit">Upload</button>
        <button type="button" class="quiet again" hidden>Try again now</button></div>
      <progress id="b2" max="100" value="0" aria-label="Sent so far" hidden></progress>''', '''      <div class="row"><button type="submit">Upload videos</button>
        <button type="button" class="quiet again" hidden>Try again now</button></div>
      <progress id="b2" max="100" value="0" aria-label="Sent so far" hidden></progress>
      <div class="nums" id="n2" aria-hidden="true" hidden></div>''')
swap("portal.py", '''  <div class="card">
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
</div>''', '''  <div class="card">
    <h2>Other apps</h2>
    <div class="links">
      <a href="https://example.org/read/">Reader</a>
      <a href="https://media.home.example.net/">Jellyfin</a>{admin_links}
    </div>
  </div>
</div>''')
# the admin links are built in page() before the f-string (review 11-23 #10)
swap("portal.py", '''    # U6 (2026-11-19): a real document (doctype, lang, charset); U7: results are announced (aria-live).''',
     '''    admin_links = ""
    if person and person == ADMIN:       # review 11-23 #10: admin tools for Jason only; #8 #13: clear names, no arrows
        admin_links = """
      <a href="https://grab.home.example.net/">Grab tool (advanced)</a>
      <a href="https://sandbox.home.example.net/">Sandbox browser</a>
      <a href="https://torrent.home.example.net/">qBittorrent</a>"""

    # U6 (2026-11-19): a real document (doctype, lang, charset); U7: results are announced (aria-live).''')
swap("portal.py", '''footer{color:var(--dim);font-size:14px;margin-top:26px;line-height:1.7}''',
     '''.nums{display:flex;justify-content:space-between;color:var(--dim);font-size:15px;margin-top:6px;font-variant-numeric:tabular-nums}
fieldset.pick{border:0;padding:0;margin:14px 0 0;display:block}
fieldset.pick legend{font-size:14px;color:var(--dim);padding:0;margin:0 0 6px}''')
# #6 numbers on the Drop page
swap("portal.py", '''    b.hidden=false; b.value=0;
    const paint=()=>{ b.value=done.reduce((s,v)=>s+v,0)/total*100; };''', '''    b.hidden=false; b.value=0;
    // review 11-23 #6: percent, speed and time left under the bar (same numbers as /sending)
    const nums=document.getElementById(barId.replace('b','n')), t0=performance.now();
    if(nums){ nums.hidden=false; nums.textContent=''; }
    const paint=()=>{ const got=done.reduce((s,v)=>s+v,0); b.value=got/total*100;
      if(!nums) return; const secs=(performance.now()-t0)/1000, rate=secs>1?got/secs:0;
      nums.replaceChildren(...[Math.floor(got/total*100)+'%', rate?Atomic.mb(rate)+'/s':'',
        rate&&got<total?Atomic.left((total-got)/rate):''].map(x=>{ const s=document.createElement('span');
        s.textContent=x; return s; })); };''')
swap("portal.py", '''        progress:n=>{ done[i]=n; paint(); },''',
     '''        progress:n=>{ done[i]=n; paint(); s.textContent='Sending… '+Math.floor(n/(file.size||1)*100)+'%'; },''')
swap("portal.py", '''    btn.disabled=false; b.hidden=true; again.hidden=true;''',
     '''    btn.disabled=false; b.hidden=true; again.hidden=true; if(nums) nums.hidden=true;''')

# ===================================================== atomic_ui.py =========================================
swap("atomic_ui.py", "if (c === 413) return r.why || 'Too big for Drop.';", "if (c === 413) return r.why || 'Too big for @@NAME@@.';")
swap("atomic_ui.py", "if (!r.dest) return 'Saved, not sorted: Drop could not tell what this is, so it waits for Jason.';",
     "if (!r.dest) return 'Saved, not sorted: @@NAME@@ could not tell what this is, so it waits for Jason.';")
swap("atomic_ui.py", '''  return {send, req, poke, pause, plain, bad, mb, left, sleep, Signin, Busy};
})();
"""
''', '''  return {send, req, poke, pause, plain, bad, mb, left, sleep, Signin, Busy};
})();
"""
# Review 11-23 #1: the engine's messages use the app's own name (drop.toml [brand]), escaped for a JS '...' string.
ATOMIC_JS = ATOMIC_JS.replace("@@NAME@@", themes.current()["name"].replace("\\\\", "\\\\\\\\").replace("'", "\\\\'"))
''')
swap("atomic_ui.py", '''li small a{display:inline-block;padding:10px 0}''', '''li small a{display:inline-block;padding:10px 0}
a.done{display:flex;justify-content:center;align-items:center;width:100%;min-height:48px;border-radius:10px;background:var(--cyan);color:var(--bg);font-weight:600;text-decoration:none}''')
swap("atomic_ui.py", '''<div class=row><a href="./">Back to Drop</a><button id=again hidden>Try again now</button></div>''',
     '''<div class=row><button id=again hidden>Try again now</button><a class=done href="./">Done</a></div>   <!-- review 11-23 #12: a clear end action -->''')
swap("atomic_ui.py", '''  if (!recs.length) { working = false; mode('done'); say(k ? 'Already sent.' : 'Nothing waiting to send.'); return; }''',
     '''  if (!recs.length) { working = false; mode('done'); $('bar').hidden = true; $('nums').hidden = true;   // review 11-23 #7
    say(k ? 'Already sent.' : 'Nothing waiting to send.'); return; }''')
swap("atomic_ui.py", '''  $('eta').textContent = rate && sent < total ? Atomic.left((total - sent) / rate) : '';   // U13''',
     '''  $('eta').textContent = rate && sent < total ? Atomic.left((total - sent) / rate) : '';   // U13
  if (working) document.title = p.toFixed(0) + '% · @@NAME@@';                              // review 11-23 #15''')
swap("atomic_ui.py", '''  working = false; $('title').textContent = anyBad ? 'Finished' : 'Shared';''',
     '''  working = false; $('title').textContent = anyBad ? 'Finished' : 'Shared';
  document.title = (anyBad ? 'Needs a look' : 'Shared') + ' · @@NAME@@';                     // review 11-23 #15''')
swap("atomic_ui.py", '''say('Something went wrong on this page. Tap Back to Drop and share again: files already sent are safe.'); });''',
     '''say('Something went wrong on this page. Tap Done and share again: files already sent are safe.'); });''')

# ===================================================== share.py =============================================
swap("share.py", '''def _page(results: list[dict]) -> str:
    rows = "".join(
        "<li><strong>{}</strong>: {} &rarr; {}<br><small>{}</small></li>".format(
            html.escape(str(r.get("name", ""))), html.escape(str(r.get("status", ""))),
            html.escape(str(r.get("kind", "") or "-")), html.escape(str(r.get("why", ""))[:300]))
        for r in results) or "<li>Nothing was shared.</li>"
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>Drop: done</title><style>body{font:16px/1.5 system-ui;margin:16px;max-width:640px}"
            "li{margin:0 0 12px}small{color:#555}</style><h1>Shared</h1><ul>" + rows +
            "</ul><p><a href='./'>Back to Drop</a></p>")''', '''_KIND = {"book": "a book", "comic": "a comic", "document": "paperwork", "recipe": "a recipe",
         "workout": "a workout", "video": "a video"}


def _plain(r: dict) -> str:
    """One result in plain words: the same rules as Atomic.plain in atomic.js (review 11-23 #5)."""
    k, p, dest, st = _KIND.get(r.get("kind"), "a file"), str(r.get("path") or ""), str(r.get("dest") or ""), r.get("status")
    person = str(r.get("person") or "")
    if st == "duplicate":
        return "Already there: this exact file was sent before, so it was not saved twice."
    if st == "kept":
        return "Not filed, but kept safe on the server for Jason to sort."
    if st == "skipped":
        return "Skipped: " + str(r.get("why") or "too big to unpack")
    if st == "refused":
        return "Not saved: " + str(r.get("why") or "this kind of link cannot be read")
    if st == "error":
        return ("Not saved: " + str(r.get("why") or "something went wrong")).rstrip(".") + "."
    if st == "filed":
        if r.get("held"):
            return f"Kept for you: {k}. You can read that library but not add to it, so Jason will file it."
        if "/pending/" in dest or "/.pending/" in p:
            return f"Kept for {person.capitalize() or 'you'}: {k} waits to be added to its app."
        if not r.get("dest"):
            return f"Saved, not sorted: {themes.current()['name']} could not tell what this is, so it waits for Jason."
        if r.get("kind") == "video":
            return "Filed: a video in the " + ("private" if "/private/" in p else "general") + " library."
        if r.get("kind") == "document":
            return "Filed: paperwork, sent to Paperless."
        return f"Filed: {k}" + (f" on {person.capitalize()}'s shelf" if person else "") + "."
    return str(r.get("why") or st or "done")


def _page(results: list[dict]) -> str:
    """The result page when the service worker did not take the share (review 11-23 #5: plain words, theme look).

    Was: raw "book.pdf: filed -> book", no theme, no dark mode, titled "Drop: done"."""
    t = themes.current()
    bad = any(r.get("status") in ("error", "refused") for r in results)
    rows = "".join("<li class='{}'><b>{}</b><small>{}</small></li>".format(
        "err" if r.get("status") in ("error", "refused") else "ok", html.escape(str(r.get("name", ""))),
        html.escape(_plain(r)[:300])) for r in results) or "<li><b>Nothing was shared.</b></li>"
    name = html.escape(t["name"])
    return ("<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            f"<meta name=theme-color content='{t['theme_color']}'><title>{'Needs a look' if bad else 'Shared'} · {name}</title>"
            "<style>" + t["root"] + "*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);"
            "font:16px/1.5 system-ui,sans-serif}main{max-width:560px;margin:0 auto;padding:24px 16px}"
            "h1{font-size:1.35rem;margin:8px 0 16px}ul{list-style:none;padding:0;margin:0}"
            "li{background:var(--card);border-radius:12px;padding:12px 14px;margin:0 0 10px}"
            "li b{display:block;word-break:break-word}li small{color:var(--dim);display:block}"
            "li.ok b::before{content:'\\\\2713  ';color:var(--ok)}li.err b::before{content:'\\\\2715  ';color:var(--bad)}"
            "a.done{display:flex;justify-content:center;align-items:center;min-height:48px;margin-top:16px;"
            "border-radius:10px;background:var(--cyan);color:var(--bg);font-weight:600;text-decoration:none}"
            ":focus-visible{outline:3px solid var(--cyan);outline-offset:3px}</style>"
            f"<main><h1>{'Something needs a look' if bad else 'Shared'}</h1><ul>" + rows +
            "</ul><a class=done href='./'>Done</a></main>")''')

for name, text in files.items():
    compile(text, name, "exec")
    (folder / name).write_text(text, encoding="utf-8")
print("patched: portal.py, atomic_ui.py, themes.py, share.py (screens review 11-23)")
