#!/usr/bin/env python3
# =============================================================================
#  showcase.py -- a public, read-only window onto real source (2026-09-01).
#
#  WHY NOT JUST OPEN THE GIT SERVER. Two reasons, and both matter. The
#  repositories are private and /git sits behind the household sign-in, so
#  a recruiter cannot read a line of it -- and the fix for that is not to
#  unauthenticate a server that also holds family data and the admin UI.
#  Second, a raw repository is the worst way to be read: nobody browsing a
#  stranger's tree finds the interesting file.
#
#  So this publishes an ALLOWLIST -- files chosen because they show
#  something -- as plain, cached HTML under web/public/code/. No auth, no
#  server, nothing dynamic, and nothing included that was not named here.
#  Delete the folder and the window closes.
# =============================================================================
import html
import io
import os

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
OUT = os.path.join(ROOT, "web", "public", "code")

# Chosen to show range: systems Rust, applied-AI Python, real infrastructure,
# and the front-end that drives the memorial.
FILES = [
    ("rust/auth/src/bridge.rs", "Rust",
     "The cross-domain session bridge. Single-use sixty-second codes, "
     "host-bound, with the trust rules written as unit tests underneath."),
    ("rust/common/src/people.rs", "Rust",
     "Identity and permission levels shared by every service -- one "
     "definition of who may do what, so the sites cannot drift apart."),
    ("voice/server.py", "Python",
     "The voice service: two engines behind one OpenAI-shaped API, and the "
     "normalizer that turns page punctuation into speech."),
    ("hermes/app/house.py", "Python",
     "Read-only tools that let a local language model answer questions "
     "about the house -- logs, uptime, git -- without holding any write."),
    ("peewee-tools/mapregion.py", "Python",
     "Pulls two cities from OpenStreetMap, projects them into one vector "
     "map, and records the documented address behind every marker."),
    ("metrics/app.py", "Python",
     "Collection and history for both machines, with the honesty that a "
     "stale sample is reported as stale rather than drawn as fresh."),
    ("telephony/app/conversation.py", "Python",
     "The telephone assistant's turn-taking: barge-in, partial results, "
     "and what to say when a model is slow."),
    ("docker-compose.yml", "YAML",
     "The application host, declared. Every service, its limits, its "
     "volumes, and a comment for every decision that cost something."),
    ("web/Caddyfile", "Caddy",
     "Every route into the house: which are public, which are gated, and "
     "which carry their own keys."),
    ("web/public/peewee/index.html", "HTML / JS",
     "The memorial itself -- the dial, the broadcast, and the narrated "
     "journey across the map of the two cities he worked in."),
]

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{{--ink:#0e1220;--ink2:#131829;--rule:#26304a;--text:#e8ecf5;
--text2:#a3aec6;--signal:#ffb347;--flow:#5bc8e8}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ink);color:var(--text);
font:14px/1.65 "IBM Plex Mono",ui-monospace,monospace}}
header{{border-bottom:1px solid var(--rule);padding:18px 22px}}
header a{{color:var(--flow);text-decoration:none;font-size:.8rem;
letter-spacing:.1em;text-transform:uppercase}}
h1{{font-size:1rem;margin:10px 0 4px;color:var(--signal)}}
.note{{color:var(--text2);font-size:.82rem;max-width:70ch;margin:0}}
.meta{{color:var(--text2);font-size:.74rem;margin-top:8px;
letter-spacing:.06em;text-transform:uppercase}}
pre{{margin:0;padding:22px;overflow-x:auto;background:var(--ink2);
border-top:1px solid var(--rule);font-size:12.5px;line-height:1.6}}
code{{white-space:pre}}
.ln{{color:#48536e;user-select:none}}
</style></head><body>
<header>
  <a href="/code/">&larr; all files</a>
  <h1>{path}</h1>
  <p class="note">{note}</p>
  <div class="meta">{lang} &middot; {lines} lines &middot; read-only copy</div>
</header>
<pre><code>{body}</code></pre>
</body></html>
"""

INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Source &mdash; Jason A. Gibson</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{{--ink:#0e1220;--ink2:#131829;--rule:#26304a;--text:#e8ecf5;
--text2:#a3aec6;--signal:#ffb347;--flow:#5bc8e8}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--ink);color:var(--text);
font:15px/1.7 "IBM Plex Mono",ui-monospace,monospace;padding:0 22px 60px}}
.wrap{{max-width:860px;margin:0 auto}}
header{{padding:34px 0 22px;border-bottom:1px solid var(--rule)}}
h1{{font-size:1.5rem;margin:0 0 10px}}
h1 span{{color:var(--signal)}}
p.lede{{color:var(--text2);font-size:.9rem;max-width:70ch}}
a.back{{color:var(--flow);text-decoration:none;font-size:.78rem;
letter-spacing:.1em;text-transform:uppercase}}
ul{{list-style:none;padding:0;margin:26px 0 0}}
li{{border-bottom:1px solid var(--rule);padding:16px 0}}
li a{{color:var(--text);text-decoration:none;font-weight:600;
font-size:.95rem}}
li a:hover{{color:var(--signal)}}
.d{{color:var(--text2);font-size:.84rem;margin-top:5px;max-width:72ch}}
.t{{color:var(--signal);font-size:.7rem;letter-spacing:.1em;
text-transform:uppercase;margin-top:6px}}
</style></head><body><div class="wrap">
<header>
  <a class="back" href="/">&larr; example.org</a>
  <h1>Read the <span>source</span></h1>
  <p class="lede">Ten files from the running system, chosen because each one
  shows something: a security boundary, an applied-AI service, the
  infrastructure that carries them, or the front end somebody actually
  uses. These are read-only copies, published deliberately &mdash; the
  repositories themselves stay private, because they sit on the same
  machine as my family&rsquo;s photographs.</p>
</header>
<ul>{items}</ul>
</div></body></html>
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    items = []
    for rel, lang, note in FILES:
        src = os.path.join(ROOT, rel)
        if not os.path.exists(src):
            print("missing, skipped:", rel)
            continue
        text = io.open(src, encoding="utf-8", errors="replace").read()
        lines = text.count("\n") + 1
        slug = rel.replace("/", "__").replace(".", "_") + ".html"
        body = "\n".join(
            f'<span class="ln">{i:5}</span>  {html.escape(l)}'
            for i, l in enumerate(text.split("\n"), 1))
        io.open(os.path.join(OUT, slug), "w", encoding="utf-8",
                newline="\n").write(PAGE.format(
                    title=html.escape(rel), path=html.escape(rel),
                    note=html.escape(note), lang=html.escape(lang),
                    lines=f"{lines:,}", body=body))
        items.append(
            f'<li><a href="{slug}">{html.escape(rel)}</a>'
            f'<div class="d">{html.escape(note)}</div>'
            f'<div class="t">{html.escape(lang)} &middot; {lines:,} lines</div></li>')
        print(f"published {rel} ({lines:,} lines)")
    io.open(os.path.join(OUT, "index.html"), "w", encoding="utf-8",
            newline="\n").write(INDEX.format(items="".join(items)))
    print(f"\n{len(items)} files at /code/")


if __name__ == "__main__":
    main()
