#!/usr/bin/env python3
"""manga.py: the scratchpad as manga-style flip books, one PDF per function zone, for Kavita.

Jason, 2026-11-12: "a flip page like a comic so that it's searchable, viewable, sharable and saves
space and doesn't have 1000 files ... color coded ... images of how it functions", "using my anime
flip book style" (his manga reader is the Kavita container on the NUC), "a way to do it by function
zone, page and script type", and "I will need to update pages in it as I edit code".

    python -I manga.py            rebuild only the zones whose scripts or docs changed
    python -I manga.py --all      rebuild every zone

HOW PAGES STAY CURRENT: the books are BUILT from the scratchpad (scripts + docs\\...md). Change a
script or its doc, run this (scratch.py runs it after import/finish), and that zone's book is
rebuilt; a hash per zone in book\\.state.json means untouched zones are skipped. Do not hand-edit
the PDFs in Stirling or Word: the next rebuild replaces them. Edit the doc (.md) instead.

Each book: a cover (zone colour, contents grouped by site page and script type, colour legend),
then one manga panel page per script: title, script-type badge (its own colour), what it does in a
speech bubble, and a little flow strip "started by -> this script -> what it touches" drawn in
boxes and arrows. Text is real text, so Kavita and any PDF reader can search it.
PDFs are made by the NUC's Gotenberg (Chromium) through the paperless-ngx container: no port opened.
Output: D:\\scratchpad\\book\\<NN Zone>.pdf, copied to the NUC at ~/documents/jason/Scratchpad Books/
(Kavita's /books/jason library). Garden / Robin material is never in the scratchpad, so never here.
"""
from __future__ import annotations

import csv
import hashlib
import html
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HOME = Path(r"D:\scratchpad")
BOOK = HOME / "book"
STATE = BOOK / ".state.json"
NUC = "server@100.64.0.11"  # Tailscale (2026-11-17); LAN 192.168.1.86 only if Tailscale is down
CADDY = Path(r"C:\Jasons New Site\www\ai-stack\web\Caddyfile")

# Function zones: (name, colour, rule on the scratchpad path). First match wins.
ZONES = [
    ("Sign-in and People", "#E4572E", r"/rust/(auth|common|admin)/|authentik|people-mirror|auth-setup|admin-password"),
    ("Secrets and Proton Pass", "#7B2CBF", r"^pc/secrets-tools/|env-sync|ai-compose|/secrets"),
    ("Hermes and AI", "#2E86AB", r"/(hermes|litellm|telegram|voice|stt|shared|colibri|models|voices)[/.]"),
    ("Phone Line", "#F18F01", r"/telephony/"),
    ("Documents and Paperless", "#3BB273", r"paperless|_documents|tidy_watch|audio_files|^pc/doc-gather/"),
    ("Website Pages", "#D81B60", r"/(rust/(landing|legacy)|web|homepage|collage|dj|peewee-tools|pi-digits|files|tables|pi|n8n|kitchen|cups|printq)/"),
    ("Developer Tools", "#546E7A", r"^pc/(checks|archive-tools)/|/rust/scratchpad/|doc_backfill|build_code_inventory|import_graph|sync_scratchpad|/scratchpad/files/|codemap"),
    ("PC Care", "#8D6E63", r"^pc/(pc-cleanup|www-root|desktop)/"),
    ("Operations and Backups", "#1B998B", r"."),
]
TYPES = {  # extension -> (script type, colour)
    ".py": ("Python", "#3572A5"), ".ps1": ("PowerShell", "#012456"), ".psm1": ("PowerShell", "#012456"),
    ".cmd": ("Windows batch", "#4D4D4D"), ".bat": ("Windows batch", "#4D4D4D"), ".vbs": ("VBScript", "#945DB7"),
    ".sh": ("Shell", "#4EAA25"), ".bash": ("Shell", "#4EAA25"), ".rs": ("Rust", "#B7410E"),
    ".html": ("Web page", "#E34C26"), ".css": ("Web page", "#E34C26"), ".js": ("Web page", "#C9A400"),
    ".mjs": ("Web page", "#C9A400"), ".yml": ("Config", "#6A737D"), ".yaml": ("Config", "#6A737D"),
    ".toml": ("Config", "#6A737D"), ".conf": ("Config", "#6A737D"), ".sql": ("SQL", "#E38C00"),
}


def type_of(path: str) -> tuple[str, str]:
    name = path.rsplit("/", 1)[-1]
    if name.startswith("Dockerfile"):
        return "Docker", "#0DB7ED"
    if name.startswith("Caddyfile"):
        return "Config", "#6A737D"
    return TYPES.get(Path(name).suffix.lower(), ("Other", "#999999"))


def zone_of(path: str) -> tuple[str, str]:
    for name, colour, rx in ZONES:
        if re.search(rx, path, re.IGNORECASE):
            return name, colour
    return ZONES[-1][:2]


def site_pages() -> dict[str, str]:
    """container name -> the site path Caddy sends to it (read from web\\Caddyfile)."""
    out, cur = {}, "/"
    for line in CADDY.read_text("utf-8", "replace").splitlines():
        s = line.strip()
        m = re.match(r"handle(?:_path)?\s+(/[^\s{]*)", s)
        if m:
            cur = m.group(1).rstrip("*")
        m = re.match(r"^([a-z0-9.-]+\.(?:org|net|com))\s*\{", s)
        if m:
            cur = m.group(1)
        m = re.match(r"reverse_proxy\s+([a-z][a-z0-9_-]*):\d+", s)
        if m and m.group(1) not in out:
            out[m.group(1)] = cur
    return out


def page_of(path: str, pages: dict[str, str]) -> str:
    parts = path.split("/")
    for p in parts[2:5]:
        key = {"rust": None, "peewee-tools": "peewee-studio"}.get(p, p)
        if key and key in pages:
            return pages[key]
    if len(parts) > 3 and parts[2] == "rust" and parts[3] in pages:
        return pages[parts[3]]
    return "not a web page"


def summary(row: dict) -> str:
    area, rel = row["scratchpad_path"].split("/", 2)[1:]
    doc = HOME / "docs" / "pc" / area / (rel + ".md")
    if doc.is_file():
        m = re.search(r"## What it does\s+(.+?)(?:\n#|\Z)", doc.read_text("utf-8", "replace"), re.DOTALL)
        if m:
            text = " ".join(m.group(1).split())
            return " ".join(re.split(r"(?<=[.!?])\s", text)[:3])[:420]
    src = HOME / row["scratchpad_path"]
    try:
        for line in src.read_text("utf-8", "replace").splitlines()[:20]:
            t = line.strip().lstrip("#/*;'\"-<!>:@ ").strip()
            if len(t) > 15 and not t.lower().startswith(("!/", "requires", "usr/bin", "echo off")):
                return t[:300] + "  (no doc yet)"
    except OSError:
        pass
    return "No description yet: this script needs a doc."


def started_by(row: dict) -> str:
    p, kind = row["scratchpad_path"], row["kind"]
    if "desktop shortcut" in kind:
        return kind.replace("started by the ", "")
    if (HOME / p).parent.joinpath("Dockerfile").exists() or "/app/" in p:
        return "a Docker container"
    if re.search(r"(backup|export|metrics|vector|index-feed|people-mirror|docker-fix|sync_documents)", p):
        return "a Windows scheduled task"
    return "you (run by hand)"


def touches(row: dict) -> list[str]:
    try:
        text = (HOME / row["scratchpad_path"]).read_text("utf-8", "replace")
    except OSError:
        return []
    found = []
    for rx, label in [(r"paperless", "Paperless"), (r"pass-cli", "Proton Pass"), (r"ollama|litellm", "AI models"),
                      (r"smtp|imap|gmail", "Email"), (r"192\.168\.86\.86|ssh ", "the NUC"), (r"docker", "Docker"),
                      (r"All-Documents", "All-Documents"), (r"twilio", "Twilio"), (r"rclone|gdrive", "Google Drive"),
                      (r"postgres|sqlite|\.db\b", "a database"), (r"caddy", "Caddy")]:
        if re.search(rx, text, re.IGNORECASE) and label not in found:
            found.append(label)
    return found[:4] or ["files on this PC"]


CSS = """
@page { size: 148mm 210mm; margin: 0 }
* { box-sizing: border-box }
body { margin: 0; font-family: 'DejaVu Sans', Arial, sans-serif; color: #111 }
.page { width: 148mm; height: 210mm; padding: 7mm; page-break-after: always; position: relative;
  background: #fff radial-gradient(#0002 1px, transparent 1.2px) 0 0 / 3.2mm 3.2mm }
.panel { border: 1.4mm solid #111; border-radius: 2mm; background: #fff; padding: 4mm; margin-bottom: 4mm;
  box-shadow: 1.6mm 1.6mm 0 #111 }
.band { color: #fff; font-weight: 900; font-style: italic; letter-spacing: .3mm; padding: 2mm 3mm;
  border: 1.2mm solid #111; transform: skew(-6deg); display: inline-block; font-size: 4.2mm }
h1 { font-size: 8mm; font-weight: 900; font-style: italic; margin: 3mm 0 2mm; line-height: 1.05;
  word-break: break-word; text-shadow: .6mm .6mm 0 #ddd }
.badge { display: inline-block; color: #fff; font-weight: 800; padding: 1mm 2.5mm; border-radius: 6mm;
  border: .8mm solid #111; font-size: 3.4mm; margin-right: 1.5mm }
.bubble { position: relative; background: #fff; border: 1mm solid #111; border-radius: 6mm; padding: 4mm 5mm;
  font-size: 3.9mm; line-height: 1.45; margin: 5mm 0 7mm }
.bubble:after { content: ''; position: absolute; left: 12mm; bottom: -5.2mm; border: 3mm solid transparent;
  border-top: 4.5mm solid #111 }
.flow { display: flex; align-items: center; gap: 2mm; font-size: 3.3mm; font-weight: 700 }
.box { border: .9mm solid #111; border-radius: 2mm; padding: 2mm; text-align: center; flex: 1; min-height: 14mm;
  display: flex; align-items: center; justify-content: center }
.arrow { font-size: 6mm; font-weight: 900 }
.meta { font-size: 3.1mm; line-height: 1.6; word-break: break-all }
.meta b { display: inline-block; width: 24mm }
.num { position: absolute; right: 7mm; bottom: 4mm; font-weight: 900; font-style: italic; font-size: 4mm }
.cover h1 { font-size: 13mm; color: #fff; text-shadow: 1mm 1mm 0 #111; -webkit-text-stroke: .5mm #111 }
.toc { font-size: 3.1mm; line-height: 1.45; columns: 2; column-gap: 5mm }
.toc h3 { font-size: 3.5mm; margin: 2mm 0 1mm; break-after: avoid }
.legend span { display: inline-block; margin: .8mm 1mm; }
"""


def cover(zone: str, colour: str, rows: list, pages: dict) -> str:
    groups = defaultdict(list)
    for r in rows:
        groups[(page_of(r["scratchpad_path"], pages), type_of(r["scratchpad_path"])[0])].append(r)
    toc = "".join(
        f"<h3>{html.escape(pg)} &middot; {html.escape(tp)}</h3>"
        + "".join(f"<div>{html.escape(r['scratchpad_path'].split('/', 2)[2])}</div>" for r in g)
        for (pg, tp), g in sorted(groups.items()))
    legend = "".join(f"<span class=badge style='background:{c}'>{t}</span>"
                     for t, c in sorted({type_of(r['scratchpad_path']) for r in rows}))
    return (f"<div class=page style='background:{colour}'><div class=cover><div class=band style='background:#111'>"
            f"JASON'S SCRATCHPAD</div><h1>{html.escape(zone)}</h1></div>"
            f"<div class=panel><div class=legend><b>Script types:</b> {legend}</div>"
            f"<p class=meta>{len(rows)} scripts, grouped by site page and type. Each page: what it does, "
            f"what starts it, what it touches.</p><div class=toc>{toc}</div></div></div>")


def script_page(r: dict, n: int, zone: str, colour: str, pages: dict) -> str:
    p = r["scratchpad_path"]
    tname, tcol = type_of(p)
    flow_boxes = "".join(f"<div class=box>{html.escape(t)}</div>" for t in touches(r)[:2])
    return (f"<div class=page><div class=band style='background:{colour}'>{html.escape(zone)}</div>"
            f"<h1>{html.escape(p.rsplit('/', 1)[-1])}</h1>"
            f"<span class=badge style='background:{tcol}'>{tname}</span>"
            f"<span class=badge style='background:#111'>{html.escape(page_of(p, pages))}</span>"
            f"<div class=bubble>{html.escape(summary(r))}</div>"
            f"<div class=panel><div class=flow><div class=box style='background:#FFF3C4'>{html.escape(started_by(r))}</div>"
            f"<span class=arrow>&#10140;</span><div class=box style='background:{colour};color:#fff'>this script</div>"
            f"<span class=arrow>&#10140;</span>{flow_boxes}</div></div>"
            f"<div class=panel><div class=meta><b>Runs from</b> {html.escape(r['runs_from'])}<br>"
            f"<b>Master copy</b> D:\\scratchpad\\{html.escape(p.replace('/', chr(92)))}<br>"
            f"<b>Doc</b> {'yes' if r['documented'] == 'yes' else 'NOT YET'}</div></div>"
            f"<div class=num>{n}</div></div>")


def render(html_doc: str) -> bytes:
    r = subprocess.run(["ssh", NUC, ("docker exec -i paperless-ngx curl -s -f -F 'files=@-;filename=index.html' "
                        "-F paperWidth=5.83 -F paperHeight=8.27 -F marginTop=0 -F marginBottom=0 -F marginLeft=0 "
                        "-F marginRight=0 -F printBackground=true http://gotenberg:3000/forms/chromium/convert/html")],
                       input=html_doc.encode("utf-8"), capture_output=True, timeout=600, check=False)
    if r.returncode != 0 or not r.stdout.startswith(b"%PDF"):
        raise RuntimeError(f"Gotenberg did not return a PDF (exit {r.returncode})")
    return r.stdout


def main() -> int:
    rows = list(csv.DictReader((HOME / "MANIFEST.csv").open(encoding="utf-8")))
    pages = site_pages()
    by_zone = defaultdict(list)
    for r in rows:
        by_zone[zone_of(r["scratchpad_path"])].append(r)
    BOOK.mkdir(exist_ok=True)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    built = 0
    for i, (zone, colour) in enumerate([z[:2] for z in ZONES], 1):
        zr = sorted(by_zone.get((zone, colour), []), key=lambda r: (page_of(r["scratchpad_path"], pages),
                                                                     type_of(r["scratchpad_path"])[0], r["scratchpad_path"]))
        if not zr:
            continue
        h = hashlib.sha256()
        for r in zr:
            h.update(r["sha256"].encode())
            area, rel = r["scratchpad_path"].split("/", 2)[1:]
            doc = HOME / "docs" / "pc" / area / (rel + ".md")
            if doc.exists():
                h.update(doc.read_bytes())
        h.update(Path(__file__).read_bytes())  # a style change rebuilds everything
        key = h.hexdigest()
        out = BOOK / f"{i:02d} {zone}.pdf"
        if "--all" not in sys.argv and state.get(zone) == key and out.exists():
            continue
        body = cover(zone, colour, zr, pages) + "".join(
            script_page(r, n, zone, colour, pages) for n, r in enumerate(zr, 2))
        out.write_bytes(render(f"<!doctype html><meta charset=utf-8><title>{html.escape(zone)}</title>"
                               f"<style>{CSS}</style>{body}"))
        state[zone] = key
        built += 1
        print(f"OK {out.name}: {len(zr) + 1} pages, {out.stat().st_size // 1024} KB")
    STATE.write_text(json.dumps(state, indent=1))
    if built:
        # Plain quoted remote path: a backslash-escaped space (first try, 2026-11-12) made scp write
        # a stray FILE named "Scratchpad\ Books" instead of filling the folder.
        subprocess.run(["ssh", NUC, 'mkdir -p "$HOME/documents/jason/Scratchpad Books"'], check=False)
        subprocess.run(["scp", "-q", *[str(p) for p in BOOK.glob("*.pdf")],
                        f"{NUC}:documents/jason/Scratchpad Books/"], check=False)
    print(f"{built} book(s) rebuilt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
