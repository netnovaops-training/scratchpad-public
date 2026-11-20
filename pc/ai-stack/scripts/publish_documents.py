#!/usr/bin/env python3
"""publish_documents.py: turn All-Documents\\Projects\\Homelab Docs (was Documents\\Homelab Docs) into pages on the site (2026-10-31).

    python publish_documents.py            # build, upload to the NUC, refresh the Proton note "Documents"
    python publish_documents.py --no-upload

Reads every .md file under the Homelab Docs folder (one subfolder per topic), converts it to a plain
HTML page, and uploads the result to ~/nuc-homelab/web/public/documents/ on the NUC. Caddy serves
that at /documents/ behind sign-in, OWNER ONLY (see the /documents block in Caddyfile.nuc): the
folder holds internal notes such as the architecture map.

Layout on the site:   /documents/                      index of every topic
                      /documents/<topic>/               index of that topic's pages
                      /documents/<topic>/<page>/        one page (long pages are split into
                                                        <page>-part-1, -part-2 ... at heading breaks,
                                                        each linking to the previous and next part)
Also writes the Proton Pass note "Documents": the same index with a full link to every page, so the
notes point at the site and the site points back at the notes. Plain text only, never a secret.
Scripts and <iframe>/<script> tags are stripped from page bodies, since some pages came from the web.
"""
import html
import os
import re
import subprocess
import sys
from pathlib import Path

import markdown

# Moved 2026-11-12 into All-Documents (step 2 cleanup). The old path
# C:\Users\USER\Documents\Homelab Docs is now a junction to this folder, so either works.
SRC = r"D:\ai-stack\All-Documents\Projects\Homelab Docs"  # 2026-11-20: All-Documents moved to D:
BUILD = r"D:\homelab-docs\_site_build"
SITE = "https://example.org"
NUC = "server@192.168.1.86"
SPLIT_AT = 45000  # characters of markdown per page before a page is split into parts

# Files from elsewhere that belong in "Our-Setup" (copied in each run, so the folder stays current).
OUR_SETUP = [
    (r"C:\Jasons New Site\www\ai-stack\ARCHITECTURE-MAP.md", "Architecture-map.md"),
    (r"D:\homelab-docs\dozzle\maintenance-notes.md", "Dozzle-maintenance-notes.md"),
    (r"D:\homelab-docs\dozzle\what-is-dozzle.md", "Dozzle-what-is-it.md"),
    (r"D:\homelab-docs\dozzle\container-updates.md", "Dozzle-container-updates.md"),
    (r"D:\homelab-docs\secrets-sweep\REPORT-2026-10-31.md", "Secrets-sweep-report-2026-10-31.md"),
    (r"C:\Jasons New Site\www\ai-stack\docs\COMMENTING-STYLE.md", "Commenting-style-rules.md"),
    (r"C:\Jasons New Site\www\ai-stack\RESUME-HERE.md", "Resume-here.md"),
]

CSS = """:root{--bg:#0f0b1f;--card:#17112c;--line:#2a2147;--ink:#ece7fb;--muted:#9a8fc2;--accent:#8a6eff}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.6 system-ui,-apple-system,"Segoe UI",sans-serif;
padding:20px max(16px,env(safe-area-inset-right,0px)) 64px max(16px,env(safe-area-inset-left,0px))}
main{max-width:860px;margin:0 auto}a{color:var(--accent)}nav.top{font-size:14px;color:var(--muted);margin-bottom:18px}
nav.top a{margin-right:12px}h1{font-size:24px}h2{font-size:19px;margin-top:28px}h3{font-size:16px}
pre{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px;overflow-x:auto;font-size:13px}
code{background:var(--card);padding:1px 5px;border-radius:4px;font-size:.92em}pre code{background:none;padding:0}
table{border-collapse:collapse;display:block;overflow-x:auto}th,td{border:1px solid var(--line);padding:6px 10px;text-align:left}
blockquote{border-left:3px solid var(--accent);margin:12px 0;padding:2px 14px;color:var(--muted)}
ul.idx{list-style:none;padding:0}ul.idx li{padding:8px 0;border-top:1px solid var(--line)}.muted{color:var(--muted);font-size:14px}
.pn{display:flex;justify-content:space-between;margin-top:32px;font-size:14px}"""


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "page"


def clean(body_html):
    body_html = re.sub(r"(?is)<(script|iframe|object|embed)\b.*?</\1>", "", body_html)
    return re.sub(r"(?i)\son\w+\s*=\s*(\"[^\"]*\"|'[^']*')", "", body_html)


def page(title, crumbs, body, prev_next=""):
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="robots" content="noindex, nofollow"><title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>'
            f'<nav class="top">{crumbs}</nav>{body}{prev_next}</main></body></html>')


def title_of(md_text, fallback):
    m = re.search(r"^#\s+(.+)$", md_text, re.MULTILINE)
    return (m.group(1).strip() if m else fallback.replace("-", " ").replace("_", " "))[:120]


def split_parts(md_text):
    """Split at '## ' headings into chunks of about SPLIT_AT characters. One chunk if short."""
    if len(md_text) <= SPLIT_AT:
        return [md_text]
    chunks, cur = [], ""
    for block in re.split(r"(?m)^(?=## )", md_text):
        if cur and len(cur) + len(block) > SPLIT_AT:
            chunks.append(cur)
            cur = ""
        cur += block
    if cur:
        chunks.append(cur)
    return chunks


def stage_our_setup():
    dest = os.path.join(SRC, "Our-Setup")
    os.makedirs(dest, exist_ok=True)
    for src, name in OUR_SETUP:
        if os.path.exists(src):
            with open(src, encoding="utf-8", errors="replace") as f, open(os.path.join(dest, name), "w", encoding="utf-8") as g:
                g.write(f.read())


def main():
    stage_our_setup()
    out_root = os.path.join(BUILD, "documents")
    if os.path.isdir(out_root):
        for d, _, files in os.walk(out_root):
            for fn in files:
                os.remove(os.path.join(d, fn))
    topics = []  # (topic, [(title, url_path)])
    for topic in sorted(os.listdir(SRC)):
        tdir = os.path.join(SRC, topic)
        if not os.path.isdir(tdir):
            continue
        pages = []
        for fn in sorted(os.listdir(tdir)):
            if not fn.lower().endswith(".md") or fn.upper() == "INDEX.MD":
                continue
            text = Path(os.path.join(tdir, fn)).read_text(encoding="utf-8", errors="replace")
            base = slug(os.path.splitext(fn)[0])
            ttl = title_of(text, os.path.splitext(fn)[0])
            chunks = split_parts(text)
            for i, chunk in enumerate(chunks, 1):
                name = base if len(chunks) == 1 else f"{base}-part-{i}"
                label = ttl if len(chunks) == 1 else f"{ttl} (part {i} of {len(chunks)})"
                body = clean(markdown.markdown(chunk, extensions=["tables", "fenced_code", "sane_lists"]))
                pn = ""
                if len(chunks) > 1:
                    prev = f'<a href="../{base}-part-{i - 1}/">&larr; Part {i - 1}</a>' if i > 1 else "<span></span>"
                    nxt = f'<a href="../{base}-part-{i + 1}/">Part {i + 1} &rarr;</a>' if i < len(chunks) else "<span></span>"
                    pn = f'<div class="pn">{prev}{nxt}</div>'
                crumbs = '<a href="/documents/">Documents</a><a href="/documents/{}/">{}</a><a href="/todo/">To Do</a>'.format(slug(topic), html.escape(topic.replace("-", " ")))
                d = os.path.join(out_root, slug(topic), name)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
                    f.write(page(label, crumbs, body, pn))
                pages.append((label, f"/documents/{slug(topic)}/{name}/"))
        if pages:
            topics.append((topic, pages))
            items = "".join(f'<li><a href="{u}">{html.escape(t)}</a></li>' for t, u in pages)
            d = os.path.join(out_root, slug(topic))
            with open(os.path.join(d, "index.html"), "w", encoding="utf-8") as f:
                f.write(page(topic, '<a href="/documents/">Documents</a>', "<h1>{}</h1><ul class='idx'>{}</ul>".format(html.escape(topic.replace("-", " ")), items)))
    sections = "".join(
        "<h2><a href='/documents/{}/'>{}</a> <span class='muted'>{} pages</span></h2><ul class='idx'>{}</ul>".format(slug(t), html.escape(t.replace("-", " ")), len(p), "".join(f'<li><a href="{u}">{html.escape(x)}</a></li>' for x, u in p))
        for t, p in topics)
    with open(os.path.join(out_root, "index.html"), "w", encoding="utf-8") as f:
        f.write(page("Documents", '<a href="/todo/">To Do</a><a href="/projects/">Projects</a>',
                     "<h1>Documents</h1><p class='muted'>Offline copies of the docs this setup depends on, plus our own notes. Owner only.</p>" + sections))
    total = sum(len(p) for _, p in topics)
    print(f"built {total} pages in {len(topics)} topics -> {out_root}")

    note = ["=== DOCUMENTS (auto-generated by scripts/publish_documents.py; do not edit by hand) ===",
            f"All pages are owner-only. Index: {SITE}/documents/", ""]
    for t, p in topics:
        note.append("--- {} ---".format(t.replace("-", " ")))
        note += ["{}  {}{}".format(x.replace('"', "'"), SITE, u) for x, u in p]
        note.append("")
    note_text = "\n".join(note)

    if "--no-upload" in sys.argv:
        return 0
    # tar on this side streams into ssh on the other; two processes joined by a pipe, no shell.
    tar = subprocess.Popen(["tar", "-C", BUILD, "-cf", "-", "documents"], stdout=subprocess.PIPE)
    ssh = subprocess.run(["ssh", NUC, "mkdir -p ~/nuc-homelab/web/public && tar -C ~/nuc-homelab/web/public -xf -"], stdin=tar.stdout, check=False)
    tar.stdout.close()
    if tar.wait() != 0 or ssh.returncode != 0:
        print("FAIL upload")
        return 1
    print("PASS uploaded to the NUC")
    env = dict(os.environ, PROTON_PASS_KEY_PROVIDER="fs")
    have = subprocess.run(["pass-cli", "item", "view", "--vault-name", "Notes", "--item-title", "Documents", "--field", "note"],
                          env=env, capture_output=True, check=False).returncode == 0
    args = (["item", "update", "--vault-name", "Notes", "--item-title", "Documents", "--field", "note=" + note_text] if have else
            ["item", "create", "note", "--vault-name", "Notes", "--title", "Documents", "--note", note_text])
    ok = subprocess.run(["pass-cli"] + args, env=env, capture_output=True, check=False).returncode == 0
    print("{} Proton note Documents ({} chars)".format("PASS" if ok else "FAIL", len(note_text)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
