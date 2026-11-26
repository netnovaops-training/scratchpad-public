#!/usr/bin/env python3
"""make_catalog.py - write the Scratchpad's "magazine" catalog: CATALOG.md and catalog/index.html (2026-11-23).

    py -3.14 -I tools/make_catalog.py                      rewrite CATALOG.md + catalog/index.html in this repo
    py -3.14 -I tools/make_catalog.py --out DIR            write the same two files into DIR instead
    py -3.14 -I tools/make_catalog.py --public DIR         public edition into DIR (no private paths or names)

Purpose: TOOLS.md is a bare one-line-per-script list. Jason 2026-11-23 asked for a catalog he can browse like a
magazine ("CATALOG.md + HTML"): a cover with counts, a table of contents, and one section per book chapter
(book/*.pdf: 01 Sign-in and People ... 09 Operations and Backups), each with a plain-words intro, then one
entry per script: what it does, how to run it, where it runs, when it last changed, and whether it has a doc.
Scripts that fit no chapter go under "Workshop".

Outcome: every tracked .py/.sh/.ps1 file appears exactly once (tools/test_make_catalog.py checks this).
The one-line "what it does" comes from make_tools_index.summary(), so it always matches TOOLS.md.
The public edition keeps only the files tools/public_copy.py would copy, runs public_copy's own word rules over
every line, then removes anything still private (drive paths, home paths, LAN/Tailscale addresses, emails,
DuckDNS/Zapto host names); it refuses to write if any of those survive. public_copy.py --write calls it for
D:/scratchpad-public, so the public repo gets its catalog in the same nightly run.
"""
import argparse
import csv
import html
import importlib.util
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS = Path(__file__).resolve().parent
EXT = {".py", ".sh", ".ps1"}


def _load(name):
    """Import a sibling tool by file path (py -I does not put this folder on sys.path). None if it is missing.

    OUTCOME: public_copy.py is never copied to the public repo, so in D:/scratchpad-public this returns None and
    the public tree (already cleaned) is catalogued as it is.
    """
    path = TOOLS / f"{name}.py"
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(f"_catalog_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


INDEX = _load("make_tools_index")

# Sections, in book-chapter order. Intros are plain words, two or three sentences each.
SECTIONS = [
    ("01", "Sign-in and People",
     "One sign-in for every page on the site, and the lists of who may use what. "
     "These scripts set up the sign-in service, register apps with it, and keep the people lists in step."),
    ("02", "Secrets and Proton Pass",
     "Passwords and keys live in Proton Pass, never in plain text. "
     "These scripts rotate keys, move values into the vault, and scan the code for anything that slipped through."),
    ("03", "Hermes and AI",
     "Hermes is the house AI assistant, backed by local models on the PC's GPU. "
     "Here are its tools, its shared memory and routing code, the voice and speech services, and the model helpers."),
    ("04", "Phone Line",
     "The phone line lets Hermes answer calls and texts. "
     "Its scripts handle callers, consent, audio and messages."),
    ("05", "Documents and Paperless",
     "Paper and digital documents end up in Paperless, sorted and searchable. "
     "These scripts gather documents from the PC and phone, sort them, and load them in."),
    ("06", "Website Pages",
     "Everything a visitor can open in a browser: Novashare file drops, the garden, the studio, the wiki and the "
     "small apps behind each page. Most run in Docker behind Caddy on the NUC or the PC."),
    ("07", "Developer Tools",
     "The tools that look after the code itself: indexes, checks, the public copy, and the GitHub and Dependabot "
     "helpers that keep every repo current. Run these when you change code, not when the site breaks."),
    ("08", "PC Care",
     "Keeping the Windows PC quiet, clean and up to date. "
     "Clean-up, updates, drive and health logs, the firewall, and the shortcuts on the desktop."),
    ("09", "Operations and Backups",
     "Keeping the machines running and the data safe. "
     "Backups and restore drills, the NAS, the Pi's monitors, start-up and repair scripts for the AI stack, and the nightly jobs."),
    ("10", "Workshop",
     "Tools that fit no chapter: one-off projects, photo and media helpers, the to-do list, and experiments. "
     "Handy to know about, rarely needed."),
]

# First matching rule wins, so the order matters (paperless_people is a document tool before it is a people
# tool; gpu-rotation is PC care before it is AI). Matched against the lower-cased path.
# OUTCOME 2026-11-23: for pc/ the rules reproduce the book's chapter lists (checked against book/*.pdf text);
# nuc/, pi/ and tools/ are not in the book and were placed by what each script's header says it does.
RULES = [
    ("05", r"paperless|doc-gather|tools/doc_gather|tools/doc_inventory|_documents\.py|tidy_watch|audio_files"),
    ("02", r"secrets-tools|/keys/|rotate_|secret|seal_|_keys|env-sync|caddy_keys"),
    ("01", r"oidc|auth-setup|people-mirror|shared/identity|shared/invites"),
    ("04", r"telephony|twilio"),
    ("08", r"pc-cleanup|desktop/restart docker|desktop/update all apps|www-root|pc_health|pc_drive|docker_trim|"
           r"after-reboot"),
    ("03", r"hermes|colibri|litellm|ai-stack/shared/|/stt/|telegram|/voice/|voices\.ps1|skills_prompt|homelab_mcp|"
           r"claude"),
    ("07", r"archive-tools|checks/|build_code_inventory|import_graph|sync_scratchpad|make_tools_index|make_catalog|"
           r"github|dependabot|/ci/|ci_scan|code_versions|py_versions|public_copy|public_backport|projects_public|"
           r"public_push|sync_main|start-dev|start_check|site_repo_build|t0931_cycle|done_scan|docs_status|doc_sidefx|"
           r"doc_backfill|tools/scratch\.py|add_ps_comments|test-pipefail"),
    ("06", r"nuc/intake|garden|peewee-tools|collage|/dj/|files/app|pi-digits|scratchpad/files/|ai-stack/tables/|"
           r"printq|printserver|caddy|wiki|seed-|websites|site_smoke|publish-ipa|release-app|places_import|"
           r"stirling|make_dashboard|nuc/t09"),
    ("09", r"backup|kopia|restore|nas_|sqlite-dump|wv-dump|config_export|break_finder|weekly_updates|alert|"
           r"watchdog|metrics|quarantine|trash-squeeze|vault|machine_|tools/copy/|ai-stack/pi/|^pi/|nuc/root/|"
           r"runtime_bumps|unify_|unified_|^pc/ai-stack/[^/]+\.(ps1|sh)$|tools/nuc/|swap|nuc_alert|"
           r"kopia_add|docker|drive-sync|update-stack"),
]

# The public edition must never show these (checked after writing; any hit stops the run).
# A drive letter is one letter NOT inside a word ("https://" must not count) and not followed by a second "/".
PRIVATE_LEFT = re.compile(r"(?<![A-Za-z])[A-Za-z]:\\|(?<![A-Za-z])[A-Za-z]:/(?!/)|\b100\.\d|192\.168\.|@g[m]ail|duckdns|zapto|/home/(?!user\b)\w+",
                          re.I)
# Replacements for the public edition, run AFTER public_copy's rules (which turn real values into placeholders
# that still look like addresses, e.g. 192.168.1.x and 100.64.0.N; the catalog hides those too).
SCRUB = [
    (re.compile(r"\\\\[\w.-]+\\[^\s`'\"]*"), "[share]"),
    (re.compile(r"(?<![A-Za-z])[A-Za-z]:(\\|/(?!/))[^\s`'\"]*"), "[path]"),
    (re.compile(r"/home/\w+"), "~"),
    (re.compile(r"\b100\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "[tailscale-ip]"),
    (re.compile(r"\b192\.168\.\d{1,3}\.[\dx]{1,3}\b"), "[lan-ip]"),
    (re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+"), "[email]"),
    (re.compile(r"\b[\w.-]*\.(duckdns|zapto)\.org\b", re.I), "[host]"),
    (re.compile(r"\b100\.(?=\d)"), "100 ."),  # any other "100.<digit>" left (a version, a share) reads as an IP
]


def git(*args, cwd=ROOT):
    """Run git in the repo and return stdout (core.quotepath off, so names with spaces or accents stay plain)."""
    return subprocess.run(["git", "-c", "core.quotepath=false", "-C", str(cwd), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace", check=True).stdout


def tracked_scripts(public_rules):
    """Every tracked .py/.sh/.ps1 path; with public_copy's rules, only the ones it would copy."""
    files = [f for f in git("ls-files").split("\n") if f and Path(f).suffix.lower() in EXT]
    files = [f for f in files if "/__pycache__/" not in f and not f.startswith("docs/")]
    if public_rules:
        pc = public_rules
        files = [f for f in files if (f.startswith(pc.INCLUDE_PREFIX) or not f.startswith(pc.EXCLUDE_PREFIX))
                 and f not in pc.EXCLUDE_FILE]
    return sorted(files, key=str.lower)


def last_changed():
    """{path: YYYY-MM-DD of the last commit that touched it}, from ONE git log pass (one call per file took ~40 s)."""
    dates, current = {}, None
    for line in git("log", "--format=\x01%cs", "--name-only").split("\n"):
        if line.startswith("\x01"):
            current = line[1:]
        elif line and current:
            dates.setdefault(line, current)
    return dates


def manifest():
    """{scratchpad_path: row} from MANIFEST.csv (private repo only; the public copy leaves it out)."""
    path = ROOT / "MANIFEST.csv"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as fh:
        return {row["scratchpad_path"]: row for row in csv.DictReader(fh)}


def header_text(p: Path) -> str:
    """The script's docstring (Python) or its leading comment block (shell, PowerShell), plain text."""
    text = p.read_text(encoding="utf-8", errors="replace")
    if p.suffix == ".py":
        import ast
        try:
            doc = ast.get_docstring(ast.parse(text))
        except (SyntaxError, ValueError):
            doc = None
        if doc:
            return doc
    out, in_block = [], False
    for line in text.splitlines()[:80]:
        s = line.strip()
        opening = s.startswith("<#")
        if opening:
            in_block = True
            s = s[2:]
        if in_block:
            if "#>" in s:
                out.append(s.split("#>")[0])
                break
            # The opening line keeps only the text after "<#" (OUTCOME: the bare "<#" was the summary before).
            out.append(s if opening else line.rstrip())
            continue
        if s.startswith("#!") or s.lower().startswith(("#requires", "set -", "param(")):
            continue
        if s.startswith("#"):
            out.append(re.sub(r"^\s*#+ ?", "", line.rstrip()))
        elif out or s:
            break
    return "\n".join(out)


RUN_WORDS = re.compile(r"^(py|python3?|pwsh|powershell|bash|sh|sudo|\./|~/|\.\\|&|docker|uv|cargo)\b", re.I)


def usage(p: Path, head: str) -> str:
    """The run line from the header: a 'Usage:'/'Run:' line, else the first indented command line, else ''.

    OUTCOME 2026-11-23: most headers show usage as an indented command followed by 2+ spaces and an
    explanation ("python3 github_merge.py --write   merge the safe ones"); only the command part is kept.
    """
    lines = head.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^\s*(?:usage|run|run it|runs?)(?:\s*\([^)]*\))?\s*:\s*(.*)$", line, re.I)
        if m:
            rest = m.group(1).strip() or (lines[i + 1].strip() if i + 1 < len(lines) else "")
            if rest:
                return re.split(r"\s{3,}", rest)[0].rstrip(",;")
    for line in lines[1:]:
        s = line.strip()
        # "name.ps1 -- what it does" is a title line, not a command; "name.ps1 -Force" is a command.
        own = s.startswith(p.name) and not re.match(rf"{re.escape(p.name)}\s*(--?\s|:|$)", s)
        if line.startswith((" ", "\t")) and (RUN_WORDS.match(s) or own):
            return re.split(r"\s{2,}", s)[0]
    return ""


def machine(rel: str, head: str, row: dict | None) -> str:
    """Which machine runs it: NUC, PC, Pi or NAS, from the folder, MANIFEST runs_from, or the header."""
    low = rel.lower()
    if low.startswith("pi/") or "/ai-stack/pi/" in low or low.startswith("pc/ai-stack/pi/"):
        return "Pi"
    if low.startswith("nuc/") or low.startswith("tools/nuc/"):
        return "NUC"
    # Only tools/ holds NAS scripts; elsewhere "the NAS" in a header is where data goes, not where it runs.
    if low.startswith("tools/") and re.search(r"run\s*\(nas\)|on the synology|on the nas\b", head, re.I):
        return "NAS"
    if row and row.get("runs_from", "").startswith(("~/", "/home")):
        return "NUC"
    if low.startswith("tools/") and re.search(r"\b(on|ON) the NUC\b|\(NUC\)", head.split("\n\n")[0]):
        return "NUC"
    return "PC"


def section_of(rel: str) -> str:
    """Chapter number for a path (first matching RULE), '10' (Workshop) when none fits."""
    low = rel.lower()
    for sec, rx in RULES:
        if re.search(rx, low):
            return sec
    return "10"


def make_cleaner(public, public_rules):
    """A text cleaner for the chosen edition: identity for private; public_copy rules (if present) + SCRUB."""
    if not public:
        return lambda s: s

    from collections import Counter

    def clean(s):
        if public_rules is not None:
            s = public_rules.clean(s, Counter(), "(catalog)")
        for rx, rep in SCRUB:
            s = rx.sub(rep, s)
        return s
    return clean


def collect(public, public_rules):
    """One dict per script with every field the two outputs need, already cleaned for the edition."""
    dates, rows = last_changed(), manifest()
    clean = make_cleaner(public, public_rules)
    docs = ROOT / "docs"
    entries = []
    for rel in tracked_scripts(public_rules if public else None):
        p = ROOT / rel
        head = header_text(p)
        row = rows.get(rel)
        what = INDEX.summary(p) if INDEX else ""
        if not what or what.startswith("(no header"):
            # make_tools_index skips PowerShell <# ... #> blocks; their first line is still a good summary.
            what = next((ln.strip() for ln in head.splitlines() if ln.strip()), "(no header comment yet)")
        what = re.sub(rf"^{re.escape(p.name)}\s*[-:]+\s*", "", what.strip())
        documented = (docs / f"{rel}.md").is_file() or (row or {}).get("documented") == "yes"
        entries.append({
            "path": clean(rel), "name": clean(p.name), "folder": clean(str(Path(rel).parent).replace("\\", "/")),
            "what": clean(what), "run": clean(usage(p, head)), "where": machine(rel, head, row),
            "runs_from": "" if public else (row or {}).get("runs_from", ""),
            "changed": dates.get(rel, "not committed yet"), "documented": documented,
            "section": section_of(rel),
        })
    return entries


def md_escape(s: str) -> str:
    """Keep header text readable on GitHub: '<folder>' would be swallowed as an HTML tag, so escape '<'."""
    return s.replace("<", "&lt;")


def render_md(entries, public):
    """CATALOG.md: cover, contents, then each section's intro and its entries."""
    by = {sec: [e for e in entries if e["section"] == sec] for sec, _, _ in SECTIONS}
    shown = [(sec, title, intro) for sec, title, intro in SECTIONS if by[sec]]
    edition = max((e["changed"] for e in entries if e["changed"][:1].isdigit()), default="")
    machines = {m: sum(e["where"] == m for e in entries) for m in ("PC", "NUC", "Pi", "NAS")}
    docd = sum(e["documented"] for e in entries)
    out = [
        "# The Scratchpad Catalog" + (" (public edition)" if public else ""), "",
        f"*Edition of {edition}. Every script in this repo, one page per topic.*", "",
        "## On the cover", "",
        ("This is a guide to the scripts in this repo: what each one does, how to run it, which machine it runs "
         "on, when it last changed, and whether it has its own write-up. "
         "Browse by chapter below, or open `catalog/index.html` for the magazine version with a search box."), "",
        f"- **{len(entries)} scripts** in {len(shown)} chapters",
        "- " + ", ".join(f"**{n}** on the {m}" for m, n in machines.items() if n),
        f"- **{docd}** have their own doc, **{len(entries) - docd}** have only their header", "",
        ("How to read an entry: the first line is the script's own header line (the same line as in TOOLS.md). "
         "*Run* is the usage line from the header; when the header has none it says so. "
         "Regenerate with `py -3.14 -I tools/make_catalog.py`."), "",
        "## Contents", "",
    ]
    for sec, title, _ in shown:
        anchor = re.sub(r"[^a-z0-9 -]", "", f"{sec} {title}".lower()).replace(" ", "-")
        # A bullet, not "1." : Markdown renumbers ordered lists, and chapter 04 can be empty (no scripts tracked).
        out.append(f"- [{sec} {title}](#{anchor}): {len(by[sec])} scripts")
    out.append("")
    for sec, title, intro in shown:
        out += ["---", "", f"## {sec} {title}", "", f"*{intro}*", ""]
        for e in by[sec]:
            out.append(f"### `{e['name']}`")
            out.append("")
            out.append(md_escape(e["what"]))
            out.append("")
            run = f"`{e['run']}`" if e["run"] else "no usage line in the header"
            where = e["where"] + (f" (`{e['runs_from']}`)" if e["runs_from"] else "")
            out.append(f"- **Run:** {run}")
            out.append(f"- **Where:** {where} · **File:** `{e['path']}`")
            out.append(f"- **Last changed:** {e['changed']} · **Documented:** {'yes' if e['documented'] else 'no'}")
            out.append("")
    return "\n".join(out)


CSS = """
:root{--bg:#f7f3ec;--paper:#fffdf8;--ink:#1d1b18;--muted:#6b645a;--rule:#ddd3c4;--accent:#a3361f;
--chip:#efe7da;--pc:#2f5d8a;--nuc:#6a3f8f;--pi:#3e7a3a;--nas:#8a5a1f;--mark:#ffe58a}
@media (prefers-color-scheme:dark){:root{--bg:#15130f;--paper:#1e1b16;--ink:#efe9de;--muted:#a69d8f;
--rule:#3a342b;--accent:#e2775c;--chip:#2c2720;--pc:#8bb6e0;--nuc:#c4a0e6;--pi:#9dd497;--nas:#e0b07a;--mark:#6b5a12}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:0 16px}
.serif{font-family:Georgia,"Iowan Old Style","Palatino Linotype",serif}
header.mast{border-bottom:3px double var(--ink);padding:28px 0 18px;text-align:center}
.kicker{font-size:.75rem;letter-spacing:.18em;text-transform:uppercase;color:var(--accent);font-weight:700}
h1{font-family:Georgia,serif;font-size:clamp(2.2rem,7vw,4.4rem);line-height:1;margin:.15em 0;letter-spacing:-.02em}
.issue{color:var(--muted);font-style:italic}
.cover{display:grid;grid-template-columns:2fr 1fr;gap:28px;padding:24px 0;border-bottom:1px solid var(--rule)}
.cover p{font-family:Georgia,serif;font-size:1.15rem;margin:0 0 .8em}
.cover p:first-child::first-letter{float:left;font-size:3.4em;line-height:.85;padding:.05em .08em 0 0;color:var(--accent)}
.stats{display:grid;grid-template-columns:1fr 1fr;gap:12px;align-content:start}
.stat{background:var(--paper);border:1px solid var(--rule);padding:12px}
.stat b{display:block;font:700 2rem/1 Georgia,serif;font-variant-numeric:tabular-nums}
.stat span{font-size:.8rem;color:var(--muted)}
.search{position:sticky;top:0;z-index:5;background:var(--bg);padding:12px 0;border-bottom:1px solid var(--rule)}
.search input{width:100%;font:inherit;font-size:1rem;padding:10px 14px;border:1px solid var(--rule);
border-radius:6px;background:var(--paper);color:var(--ink)}
.search input:focus{outline:2px solid var(--accent);outline-offset:1px}
#count{font-size:.85rem;color:var(--muted);margin-top:6px}
nav.toc{padding:20px 0;border-bottom:1px solid var(--rule)}
nav.toc ol{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:8px 24px}
nav.toc a{color:var(--ink);text-decoration:none;display:flex;gap:10px;align-items:baseline;padding:4px 0;
border-bottom:1px dotted var(--rule)}
nav.toc a:hover{color:var(--accent)}
nav.toc .n{font:700 1.3rem Georgia,serif;color:var(--accent);min-width:1.6em}
nav.toc .c{margin-left:auto;color:var(--muted);font-size:.8rem;font-variant-numeric:tabular-nums}
section.ch{padding:32px 0 8px;border-bottom:1px solid var(--rule)}
section.ch h2{font:700 clamp(1.7rem,4.5vw,2.6rem)/1.1 Georgia,serif;margin:.1em 0 .3em}
.deck{font:italic 1.1rem/1.5 Georgia,serif;color:var(--muted);max-width:62ch;margin:0 0 20px}
.grid{columns:3 300px;column-gap:20px}
article{break-inside:avoid;background:var(--paper);border:1px solid var(--rule);border-top:3px solid var(--accent);
padding:12px 14px;margin:0 0 20px}
article h3{margin:0 0 4px;font-size:1rem;overflow-wrap:anywhere}
article h3 code{font-size:.95rem}
article p{margin:0 0 8px;font-family:Georgia,serif}
code{font-family:ui-monospace,"Cascadia Mono",Consolas,monospace;font-size:.85em}
.run{display:block;background:var(--chip);padding:6px 8px;border-radius:4px;overflow-wrap:anywhere;margin:0 0 8px}
.none{color:var(--muted);font-style:italic;font-size:.85rem;display:block;margin:0 0 8px}
.meta{display:flex;flex-wrap:wrap;gap:6px;font-size:.75rem;color:var(--muted)}
.tag{padding:1px 7px;border-radius:999px;background:var(--chip);font-weight:600}
.m-PC{color:var(--pc)}.m-NUC{color:var(--nuc)}.m-Pi{color:var(--pi)}.m-NAS{color:var(--nas)}
.doc-no{opacity:.7}
.path{font-size:.72rem;color:var(--muted);overflow-wrap:anywhere;margin-top:6px}
footer{padding:28px 0 40px;color:var(--muted);font-size:.85rem;text-align:center}
.hide{display:none}
@media (max-width:720px){.cover{grid-template-columns:1fr}.grid{columns:1}}
"""

JS = """
const q=document.getElementById('q'),count=document.getElementById('count'),
cards=[...document.querySelectorAll('article')],secs=[...document.querySelectorAll('section.ch')];
function run(){const words=q.value.toLowerCase().trim().split(/\\s+/).filter(Boolean);let n=0;
for(const c of cards){const ok=words.every(w=>c.dataset.s.includes(w));c.classList.toggle('hide',!ok);if(ok)n++}
for(const s of secs){s.classList.toggle('hide',!s.querySelector('article:not(.hide)'))}
count.textContent=words.length?n+' of '+cards.length+' scripts match':cards.length+' scripts'}
q.addEventListener('input',run);run();
"""


def render_html(entries, public):
    """catalog/index.html: the same content as CATALOG.md, laid out like a magazine, one self-contained file."""
    e_ = html.escape
    by = {sec: [e for e in entries if e["section"] == sec] for sec, _, _ in SECTIONS}
    shown = [(sec, title, intro) for sec, title, intro in SECTIONS if by[sec]]
    edition = max((e["changed"] for e in entries if e["changed"][:1].isdigit()), default="")
    machines = {m: sum(e["where"] == m for e in entries) for m in ("PC", "NUC", "Pi", "NAS")}
    docd = sum(e["documented"] for e in entries)
    stats = [(len(entries), "scripts"), (len(shown), "chapters"), (docd, "with their own doc")]
    stats += [(n, f"run on the {m}") for m, n in machines.items() if n]
    h = ["<!doctype html>", '<html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>Scratchpad Catalog</title><style>{CSS}</style></head><body>",
         '<div class="wrap"><header class="mast">',
         f'<div class="kicker">{"Public edition" if public else "Private edition"} · scripts of the home lab</div>',
         "<h1>The Scratchpad Catalog</h1>",
         f'<div class="issue">Edition of {e_(edition)}</div></header>',
         '<div class="cover"><div>',
         ("<p>Every script in this repo, gathered into chapters like a magazine: what each one does, how to run it, "
          "which machine it runs on, when it last changed, and whether it has its own write-up.</p>"),
         ("<p>Start with the contents, or type in the search box: it filters by name, folder, machine or any word "
          "from the description. Each card's first line is the script's own header line.</p>"),
         "</div><div class=\"stats\">"]
    h += [f'<div class="stat"><b>{n}</b><span>{e_(label)}</span></div>' for n, label in stats]
    h += ["</div></div>",
          '<div class="search"><label class="kicker" for="q">Search the catalog</label>',
          '<input id="q" type="search" placeholder="Try: backup, NUC, rotate, docker" autocomplete="off">',
          '<div id="count" aria-live="polite"></div></div>',
          '<nav class="toc" aria-label="Contents"><div class="kicker">Contents</div><ol>']
    for sec, title, _ in shown:
        h.append(f'<li><a href="#ch{sec}"><span class="n">{int(sec)}</span><span>{e_(title)}</span>'
                 f'<span class="c">{len(by[sec])}</span></a></li>')
    h.append("</ol></nav>")
    for sec, title, intro in shown:
        h += [f'<section class="ch" id="ch{sec}"><div class="kicker">Chapter {int(sec)}</div>',
              f"<h2>{e_(title)}</h2>", f'<p class="deck">{e_(intro)}</p>', '<div class="grid">']
        for e in by[sec]:
            search = " ".join([e["path"], e["what"], e["run"], e["where"], title,
                               "documented" if e["documented"] else "undocumented"]).lower()
            run = (f'<code class="run">{e_(e["run"])}</code>' if e["run"]
                   else '<span class="none">No usage line in the header.</span>')
            rf = f' title="{e_(e["runs_from"])}"' if e["runs_from"] else ""
            h += [f'<article data-s="{e_(search)}"><h3><code>{e_(e["name"])}</code></h3>',
                  f'<p>{e_(e["what"])}</p>{run}<div class="meta">',
                  f'<span class="tag m-{e["where"]}"{rf}>{e["where"]}</span>',
                  f'<span class="tag">changed {e_(e["changed"])}</span>',
                  f'<span class="tag{"" if e["documented"] else " doc-no"}">'
                  f'{"documented" if e["documented"] else "no doc yet"}</span></div>',
                  f'<div class="path">{e_(e["path"])}</div></article>']
        h.append("</div></section>")
    h += ["<footer>Generated by tools/make_catalog.py from each script's own header. "
          "The Markdown version is CATALOG.md.</footer></div>", f"<script>{JS}</script></body></html>"]
    return "\n".join(h)


def write(out_dir: Path, entries, public):
    """Write CATALOG.md and catalog/index.html into out_dir; for public, refuse if anything private is left."""
    md, page = render_md(entries, public) + "\n", render_html(entries, public) + "\n"
    if public:
        for label, text in (("CATALOG.md", md), ("catalog/index.html", page)):
            hits = {m.group(0).lower() for m in PRIVATE_LEFT.finditer(text)}
            if hits:
                sys.exit(f"STOP: public {label} still holds private patterns {sorted(hits)[:5]}; nothing written")
    (out_dir / "catalog").mkdir(parents=True, exist_ok=True)
    (out_dir / "CATALOG.md").write_text(md, encoding="utf-8", newline="\n")
    (out_dir / "catalog" / "index.html").write_text(page, encoding="utf-8", newline="\n")
    print(f"{out_dir}: {len(entries)} scripts, {'public' if public else 'private'} edition")


def main(argv=None):
    """Parse the flags and write one edition."""
    ap = argparse.ArgumentParser(description="Write CATALOG.md and catalog/index.html.")
    ap.add_argument("--public", metavar="DIR", help="write the public edition into DIR")
    ap.add_argument("--out", metavar="DIR", help="write the private edition into DIR instead of the repo")
    a = ap.parse_args(argv)
    if a.public:
        rules = _load("public_copy")  # None in the public repo itself: its tree is already the public set
        write(Path(a.public), collect(True, rules), True)
    else:
        write(Path(a.out) if a.out else ROOT, collect(False, None), False)


if __name__ == "__main__":
    main()
