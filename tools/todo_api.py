#!/usr/bin/env python3
"""todo_api.py - the /todo page: ONE unified to-do list, kept in a plain file (2026-11-16, v2).

Jason 11-16: "an actual webpage called to-do ... a unified to-do list as a caddy file rather than putting
them on Proton so that I can check them off. Make the list collapsible." Later the same day: broken down
by computer, project, then 2-hour sections; necessary / optional / cosmetic / wish list; a recommended
set and a difficulty on everything; time totals; a security score and a project score; questions saved
in the list so he answers them when he opens the item; each task something he can run with Claude later.

    the list   ~/nuc-homelab/TODO-ALL.md   (plain Markdown, in the nuc-homelab git repo, so it rides along
                                            with every backup: Kopia to the NAS, Kopia to the SSD, a plain
                                            copy on the SSD, a nightly pull to the PC)
    the page   https://example.org/todo/   (household sign-in, Jason only)

Caddy: `handle_path /todo/*` -> forward_auth -> reverse_proxy 127.0.0.1:8788. This service listens on
127.0.0.1 only and checks X-Auth-Email again on its own side (same pattern as receipts_api.py).
Kept running by todo_api_ensure.sh from cron (flock = one copy). After editing this file:
    pkill -f "[t]odo_api.py"; ~/learning-src/todo_api_ensure.sh &      (or wait 5 minutes for cron)

The file format is described at the top of todo_merge.py (which adds new findings and numbers them):

    ## NUC                       computer
    ### Sign-on                  project
    #### Section name            about one 2-hour block
    - [ ] **T0001** [P1][30m][security][necessary][easy][rec] Short title
      Where: / Found: / Do: ...
      Ask: a question for Jason
      Answer: what he typed on the page
      Note: his own note
      Done: 2026-11-17

The file is the truth; the page keeps nothing of its own except which sections you left open.

GET  /             the page
GET  /api/list     the parsed list as JSON
GET  /code/T0123   the suggested code for one item (todo-code/T0123.md), plain text
POST /api/toggle   {"id": "T0001", "done": true}
POST /api/answer   {"id": "T0001", "q": 0, "text": "..."}   q = which Ask line (0 = first); q = -1 = the Note
POST /api/add      {"title": "..."}  -> All / Inbox / Added from the page

SCORES (worked out in the page from the open items, so they rise as boxes are ticked):
    Security  = 100 - (25 per open P1 security item, 8 per P2, 2 per P3), never below 0
    Working   = 100 - (open necessary non-security items: broken P1 20, P2 6, P3 2; other P1 10, P2 3, P3 1)
    Done %    = minutes of necessary work ticked / minutes of all necessary work

OUTCOME 2026-11-16: parser item count equals `grep -c "^- \\["` on the merged file; toggle, answer and
note were each checked on a copy (only the intended lines change; undoing them gives back the same file).

REVIEW 2026-11-16 evening (fixed here, each tested on a copy of the list):
  - every write now also holds ~/.todo-all.lock (fcntl), the same lock todo_merge.py takes, so a tick on the
    page during a merge can no longer be lost (before, only threads inside this one process were kept apart);
  - add() also counts numbers in "Also covers: Txxxx" lines, so a new item never reuses a folded item's number;
  - answer() finds Ask lines the same way parse() does (by the stripped text), so the q-th question on the
    page is always the q-th Ask in the file;
  - the Copy-prompt text names where todo_merge.py and the fragment format live;
  - a "Code: todo-code/T0123.md" line gets a "Show suggested code" button (GET /code/T0123, plain text);
  - times and Done: dates use Eastern time (TODO_TZ), not the NUC's UTC clock: a tick after 7 PM had tomorrow's date.
Checked and fine: a forged X-Auth-Email from outside gets the sign-in redirect (303), Caddy 2.11.4.
"""
import fcntl
import json
import os
import re
import sys
import threading
from contextlib import contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zoneinfo import ZoneInfo

PORT = 8788
ADMIN = "user1@example.com"
TODO = Path(os.environ.get("TODO_FILE", str(Path.home() / "nuc-homelab" / "TODO-ALL.md")))
THREADS = threading.Lock()
LOCKFILE = Path.home() / ".todo-all.lock"     # shared with todo_merge.py
CODE_DIR = TODO.parent / "todo-code"         # suggested code per item: todo-code/T0123.md
MAX_BODY = 8 * 1024
# the NUC clock runs in UTC; dates on the page and Done: lines are Jason's local time (change for the Vegas move)
TZ = ZoneInfo(os.environ.get("TODO_TZ", "America/New_York"))


@contextmanager
def LOCKED():
    """One writer at a time: threads of this server AND other programs (todo_merge.py) that edit the list."""
    with THREADS, open(LOCKFILE, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)

ITEM = re.compile(r"^- \[( |x|X)\] \*\*(T\d{4,})\*\*\s*((?:\[[^\]]*\])*)\s*(.*)$")
TAGS = re.compile(r"\[([^\]]*)\]")
CLASSES = ("necessary", "optional", "cosmetic", "wish")
LEVELS = ("easy", "medium", "hard")


def minutes(tag):
    """'30m' -> 30, '2h' -> 120, anything else -> 0."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([mh])", tag.strip().lower())
    return int(float(m.group(1)) * (60 if m.group(2) == "h" else 1)) if m else 0


def parse(text):
    """The file -> {"computers": [{"name", "projects": [{"name", "sections": [{"name", "items": [...]}]}]}]}."""
    comps, comp, proj, sect, item = [], None, None, None, None
    intro = []
    for line in text.splitlines():
        if line.startswith("#### "):
            if proj is not None:
                sect = {"name": line[5:].strip(), "items": []}
                proj["sections"].append(sect)
            item = None
        elif line.startswith("### "):
            if comp is not None:
                proj = {"name": line[4:].strip(), "sections": []}
                comp["projects"].append(proj)
            sect, item = None, None
        elif line.startswith("## "):
            comp = {"name": line[3:].strip(), "projects": []}
            comps.append(comp)
            proj, sect, item = None, None, None
        elif ITEM.match(line) and sect is not None:
            m = ITEM.match(line)
            tags = [t.strip() for t in TAGS.findall(m.group(3))]
            low = [t.lower() for t in tags]
            pri = next((t.upper() for t in tags if re.fullmatch(r"(?i)p[1-3]|parked", t)), "P2")
            time_tag = next((t for t in low if minutes(t)), "")
            cls = next((t for t in low if t in CLASSES), "necessary")
            level = next((t for t in low if t in LEVELS), "medium")
            kind = next((t for t in low if t not in CLASSES + LEVELS + ("rec", pri.lower(), time_tag)), "")
            item = {"id": m.group(2), "done": m.group(1) != " ", "pri": pri, "time": time_tag,
                    "min": minutes(time_tag), "kind": kind, "cls": cls, "level": level, "rec": "rec" in low,
                    "title": m.group(4).strip(), "detail": [], "qs": [], "note": ""}
            sect["items"].append(item)
        elif item is not None and line.startswith("  ") and line.strip():
            s = line.strip()
            if s.startswith("Ask:"):
                item["qs"].append({"q": s[4:].strip(), "a": ""})
            elif s.startswith("Answer:") and item["qs"]:
                item["qs"][-1]["a"] = s[7:].strip()
            elif s.startswith("Note:"):
                item["note"] = s[5:].strip()
            else:
                item["detail"].append(s)
        elif line.strip():
            item = None
            if comp is None and not line.startswith("# "):
                intro.append(line.strip())
    return {"computers": comps, "intro": intro}


def read_list():
    try:
        text = TODO.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    data = parse(text)
    data["updated"] = datetime.fromtimestamp(TODO.stat().st_mtime, TZ).strftime("%b %d, %I:%M %p") if TODO.exists() else ""
    return data


def write_atomic(text):
    tmp = TODO.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, TODO)


def find_item(lines, item_id):
    """-> (index of the item line, index just past its detail lines) or None."""
    for i, line in enumerate(lines):
        m = ITEM.match(line)
        if m and m.group(2) == item_id:
            j = i + 1
            while j < len(lines) and lines[j].startswith("  ") and lines[j].strip():
                j += 1
            return i, j
    return None


def one_line(text, limit=1500):
    return re.sub(r"\s+", " ", text or "").strip()[:limit]


def toggle(item_id, done):
    """Flip one item's box in the file. Returns True if the item was found."""
    if not re.fullmatch(r"T\d{4,}", item_id or ""):
        return False
    with LOCKED():
        lines = TODO.read_text(encoding="utf-8").split("\n")
        at = find_item(lines, item_id)
        if not at:
            return False
        i, j = at
        lines[i] = "- [{}]{}".format("x" if done else " ", lines[i][5:])
        body = [l for l in lines[i + 1:j] if not l.startswith("  Done: ")]
        if done:
            body.append("  Done: " + datetime.now(TZ).strftime("%Y-%m-%d"))
        lines[i + 1:j] = body
        write_atomic("\n".join(lines))
        return True


def answer(item_id, q, text):
    """Save Jason's answer to the q-th Ask line of an item (q = -1: his free Note). Empty text removes it."""
    if not re.fullmatch(r"T\d{4,}", item_id or ""):
        return False
    text = one_line(text)
    with LOCKED():
        lines = TODO.read_text(encoding="utf-8").split("\n")
        at = find_item(lines, item_id)
        if not at:
            return False
        i, j = at
        body = lines[i + 1:j]
        if q < 0:
            body = [l for l in body if not l.startswith("  Note: ")]
            if text:
                done_at = next((k for k, l in enumerate(body) if l.startswith("  Done: ")), len(body))
                body.insert(done_at, "  Note: " + text)
        else:
            asks = [k for k, l in enumerate(body) if l.strip().startswith("Ask:")]
            if q >= len(asks):
                return False
            k = asks[q]
            if k + 1 < len(body) and body[k + 1].strip().startswith("Answer:"):
                del body[k + 1]
            if text:
                body.insert(k + 1, "  Answer: " + text)
        lines[i + 1:j] = body
        write_atomic("\n".join(lines))
        return True


def next_prompt(item_id):
    """(status, body, type) for /next/T####: todo_next.py's prompt. Imported late so a todo_next bug can never
    take the list page down; any error answers 500 with the reason, not a traceback."""
    try:
        import importlib

        import todo_next
        importlib.reload(todo_next)
        found = [i for i in todo_next.sp.items() if i["id"] == item_id]
        if not found:
            return 404, '{"error":"no such item"}'
        return 200, todo_next.prompt(todo_next.plan_for(found[0])), "text/plain"
    except Exception as e:  # noqa: BLE001 - shown to the one signed-in admin only
        return 500, json.dumps({"error": f"todo_next failed: {type(e).__name__}"})


def questions_json():
    """(status, body) for /api/questions: [{id, title, asks}] for [first] items with unanswered Ask lines."""
    try:
        import importlib

        import todo_next
        importlib.reload(todo_next)
        rows = [{"id": i["id"], "title": i["head"][:120], "asks": todo_next.open_asks(i)}
                for i in todo_next.first_items() if "[first]" in i["head"]]
        return 200, json.dumps([r for r in rows if r["asks"]])
    except Exception as e:  # noqa: BLE001
        return 500, json.dumps({"error": f"todo_next failed: {type(e).__name__}"})


def add(title):
    """A new item typed on the page goes to All / Inbox / Added from the page. Returns its id."""
    title = one_line(title, 300)
    if not title:
        return None
    with LOCKED():
        text = TODO.read_text(encoding="utf-8") if TODO.exists() else "# To do\n"
        # numbers of live items AND of items folded into others ("Also covers: T0456 ..."): never reuse either
        nums = [int(a or b) for a, b in re.findall(r"\*\*T(\d{4,})\*\*|Also covers: T(\d{4,})", text)]
        new_id = f"T{max(nums) + 1 if nums else 1:04d}"
        block = [f"- [ ] **{new_id}** [P2][30m][unfinished][necessary][medium] {title}",
                 "  Found: added from the page on {}".format(datetime.now(TZ).strftime("%Y-%m-%d"))]
        lines = text.rstrip("\n").split("\n")
        head = "#### Added from the page"
        if head in lines:
            at = lines.index(head) + 1
            lines[at:at] = block
        else:
            if "## All" not in lines:
                lines += ["", "## All"]
            at = lines.index("## All") + 1
            lines[at:at] = ["", "### Inbox", "", head] + block + [""]
        write_atomic("\n".join(lines) + "\n")
        return new_id


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>To Do</title>
<style>
:root{--bg:#f6f5f1;--card:#fff;--ink:#1d2327;--muted:#5a666e;--line:#d9d6cc;--accent:#2a6786;
  --bad:#b3261e;--warn:#8f5600;--good:#2f6b2f;--chip:#ebe8df;--sub:#faf9f5}
@media (prefers-color-scheme:dark){:root{--bg:#14181b;--card:#1d2327;--ink:#e8e6e1;--muted:#a3adb4;--line:#353e44;
  --accent:#7cc0de;--bad:#ff8a80;--warn:#ffc56b;--good:#9ad19a;--chip:#2a3238;--sub:#192024}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
header{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:8px 12px}
h1{font-size:19px;margin:0}
#sum{color:var(--muted);font-size:13.5px}
#bar{height:6px;border-radius:3px;background:var(--chip);overflow:hidden;margin:6px 0}
#bar i{display:block;height:100%;background:var(--accent);width:0}
.row{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-top:6px}
button{font:inherit;font-size:14px;min-height:40px;padding:6px 12px;border-radius:20px;border:1px solid var(--line);
  background:var(--card);color:var(--ink);cursor:pointer}
button[aria-pressed=true]{background:var(--accent);color:var(--bg);border-color:var(--accent)}
button.small{min-height:36px;font-size:13px;border-radius:8px}
input[type=search],input[type=text],textarea{font:inherit;min-height:42px;padding:8px 10px;border-radius:10px;border:1px solid var(--line);
  background:var(--card);color:var(--ink);flex:1;min-width:150px;width:100%}
textarea{min-height:64px;resize:vertical}
main{padding:10px 12px 90px;max-width:920px;margin:0 auto}
details{border-radius:12px}
details.comp{background:var(--card);border:1px solid var(--line);margin:10px 0}
details.comp>summary{font-size:18px;font-weight:700;padding:14px 12px}
details.proj{border-top:1px solid var(--line);border-radius:0}
details.proj>summary{padding:12px;font-weight:650}
details.sect{border-top:1px dashed var(--line);border-radius:0;background:var(--sub)}
details.sect>summary{padding:10px 12px 10px 20px;font-weight:550;font-size:15px}
details.help{border:1px solid var(--line);background:var(--card);margin:8px 0;font-size:14.5px}
details.help>summary{padding:10px 12px;font-weight:600}
details.help div{padding:0 14px 12px}
summary{list-style:none;cursor:pointer;display:flex;gap:8px;align-items:flex-start}
summary::-webkit-details-marker{display:none}
summary::before{content:"\25B8";color:var(--muted);flex:none;width:1em}
details[open]>summary::before{content:"\25BE"}
summary .n{flex:1;min-width:0}
.meta{display:block;color:var(--muted);font-size:13px;font-weight:400;margin-top:2px}
.score{display:inline-block;font-size:12.5px;font-weight:650;padding:1px 8px;border-radius:10px;background:var(--chip);margin:2px 4px 0 0;white-space:nowrap}
.good{color:var(--good)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.box{margin:0 12px 10px;padding:10px;border:1px solid var(--line);border-radius:10px;background:var(--card);font-size:14px}
.box h4{margin:0 0 4px;font-size:14px}
.box .line{color:var(--muted)}
.clab{padding:8px 12px 2px 20px;font-size:12.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--muted);font-weight:700}
.item{display:flex;gap:10px;padding:10px 12px 10px 20px;border-top:1px dotted var(--line);align-items:flex-start}
.item input[type=checkbox]{flex:none;width:26px;height:26px;margin-top:2px;accent-color:var(--accent)}
.item .t{flex:1;min-width:0}
.item .title{cursor:pointer;overflow-wrap:anywhere}
.item.done .title{text-decoration:line-through;color:var(--muted)}
.pill{display:inline-block;font-size:12px;padding:1px 7px;border-radius:10px;background:var(--chip);margin-right:4px;white-space:nowrap}
.pill.q{background:var(--accent);color:var(--bg)}
.id{color:var(--muted);font-size:12.5px;white-space:nowrap}
.detail{margin:8px 0 2px;font-size:14.5px;overflow-wrap:anywhere}
.detail .d{color:var(--muted);margin:2px 0}
.detail .d b{color:var(--ink);font-weight:600}
.qa{margin:10px 0;padding:10px;border-left:3px solid var(--accent);background:var(--card);border-radius:0 8px 8px 0}
.qa label{display:block;font-weight:600;margin-bottom:6px}
pre.code{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.45 ui-monospace,Consolas,monospace;background:var(--sub);border:1px solid var(--line);border-radius:8px;padding:10px;margin:6px 0;max-height:70vh;overflow:auto}
.saved{font-size:13px;color:var(--good);margin-left:8px}
#msg{position:fixed;left:50%;bottom:16px;transform:translateX(-50%);background:var(--ink);color:var(--bg);padding:8px 16px;
  border-radius:20px;font-size:14px;display:none;max-width:90vw;z-index:9}
#intro{color:var(--muted);font-size:14px;margin:4px 0 6px}
.empty{padding:14px;color:var(--muted)}
a{color:var(--accent)}
:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
@media (prefers-reduced-motion:no-preference){#bar i{transition:width .3s}}
</style>
</head>
<body>
<header>
  <h1>To Do</h1>
  <div id="sum">Loading...</div>
  <div id="bar"><i></i></div>
  <div class="row">
    <label for="q" style="position:absolute;left:-9999px">Search</label>
    <input id="q" type="search" placeholder="Search: a word, T-number, or kind">
    <button id="allq" type="button" class="small">All my questions</button>
  </div>
  <pre id="qlist" hidden style="white-space:pre-wrap"></pre>
</header>
<main>
  <details class="help" id="filters"><summary><span class="n">Filters <span id="fsum" class="meta" style="display:inline"></span></span></summary><div>
    <div class="row" role="group" aria-label="Show">
      <button data-f="show" data-v="open" aria-pressed="true">Open</button>
      <button data-f="show" data-v="done" aria-pressed="false">Done</button>
      <button data-f="show" data-v="all" aria-pressed="false">All</button>
    </div>
    <div class="row" role="group" aria-label="Class">
      <button data-f="cls" data-v="necessary" aria-pressed="false">Necessary</button>
      <button data-f="cls" data-v="optional" aria-pressed="false">Optional</button>
      <button data-f="cls" data-v="cosmetic" aria-pressed="false">Cosmetic</button>
      <button data-f="cls" data-v="wish" aria-pressed="false">Wish list</button>
    </div>
    <div class="row" role="group" aria-label="Difficulty">
      <button data-f="level" data-v="easy" aria-pressed="false">Easy</button>
      <button data-f="level" data-v="medium" aria-pressed="false">Medium</button>
      <button data-f="level" data-v="hard" aria-pressed="false">Hard</button>
    </div>
    <div class="row" role="group" aria-label="More">
      <button data-f="rec" data-v="1" aria-pressed="false">Recommended first</button>
      <button data-f="ask" data-v="1" aria-pressed="false">Has a question for me</button>
      <button data-f="kind" data-v="security" aria-pressed="false">Security</button>
      <button data-f="kind" data-v="broken" aria-pressed="false">Broken</button>
      <button id="fold">Close all sections</button>
    </div>
  </div></details>
  <details class="help"><summary><span class="n">How to read this list</span></summary><div>
    <p><b>Layout:</b> computer, then project, then sections. Each section is about one 2-hour block of work.</p>
    <p><b>Class:</b> Necessary = needed for the project to work and be safe. Optional = worth doing. Cosmetic = looks and wording. Wish list = ideas for later.</p>
    <p><b>Security score</b> (0 to 100, higher is safer): starts at 100 and loses 25 for each open P1 security item, 8 for each P2, 2 for each P3.</p>
    <p><b>Working score</b> (0 to 100, higher is healthier): starts at 100 and loses points for each open necessary item that is not a security item. Broken things cost the most (20 for P1).</p>
    <p><b>Done %</b>: how much of the necessary work is ticked, counted in time. All scores go up as you tick boxes.</p>
    <p><b>Questions:</b> open an item to see questions for you. Type your answer and press Save. It is stored in the list.</p>
    <p><b>Work on it with Claude:</b> press "Copy prompt" on an item, section or project, then paste it into a new Claude session.</p>
  </div></details>
  <div id="intro"></div>
  <div id="list"></div>
  <form id="addf" class="row" style="margin-top:18px">
    <label for="addt" style="position:absolute;left:-9999px">New item</label>
    <input id="addt" type="text" maxlength="300" placeholder="Add an item (goes to All, Inbox)">
    <button type="submit">Add</button>
  </form>
  <p style="color:var(--muted);font-size:13px;margin-top:22px">The list is the file <code>~/nuc-homelab/TODO-ALL.md</code> on the NUC.
  It is in every nightly backup and copied to the SSD and the PC.</p>
</main>
<div id="msg" role="status"></div>
<script>
(function(){
  var data=null,q="",F={show:"open",cls:{},level:{},rec:false,ask:false,kind:{}};
  var CLS=["necessary","optional","cosmetic","wish"],CLSN={necessary:"Necessary",optional:"Optional",cosmetic:"Cosmetic",wish:"Wish list"};
  var LV=["easy","medium","hard"];
  var openSet={};try{openSet=JSON.parse(localStorage.getItem("todo-open2")||"{}")}catch(e){}
  function saveOpen(){try{localStorage.setItem("todo-open2",JSON.stringify(openSet))}catch(e){}}
  function el(tag,cls,text){var e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}
  // T1017: one grouped list of every open question on the [first] items; "defaults" answers all of them
  document.getElementById("allq").addEventListener("click",function(){
    var pre=document.getElementById("qlist");
    if(!pre.hidden){pre.hidden=true;return}
    fetch("api/questions",{credentials:"same-origin"}).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()})
      .then(function(rows){var n=0,out=[];
        rows.forEach(function(r){out.push(r.id+" "+r.title);r.asks.forEach(function(a){out.push("  "+(++n)+". "+a)})});
        pre.textContent=n?out.join("\n")+"\n\nReply to Claude with 'defaults' to take every recommended choice.":"No open questions.";
        pre.hidden=false})
      .catch(function(e){msg("Could not load the questions ("+e.message+")")})});
  function msg(t){var m=document.getElementById("msg");m.textContent=t;m.style.display="block";clearTimeout(msg.t);msg.t=setTimeout(function(){m.style.display="none"},2800)}
  function hm(min){if(!min)return"0m";var h=Math.floor(min/60),m=min%60;return (h?h+"h":"")+(m?(h?" ":"")+m+"m":"")}
  function any(o){return Object.keys(o).some(function(k){return o[k]})}
  function unanswered(it){return it.qs.filter(function(x){return !x.a}).length}
  function match(it){
    if(F.show==="open"&&it.done)return false;
    if(F.show==="done"&&!it.done)return false;
    if(any(F.cls)&&!F.cls[it.cls])return false;
    if(any(F.level)&&!F.level[it.level])return false;
    if(any(F.kind)&&!F.kind[it.kind])return false;
    if(F.rec&&!it.rec)return false;
    if(F.ask&&!unanswered(it))return false;
    if(q){var hay=(it.id+" "+it.title+" "+it.kind+" "+it.cls+" "+it.level+" "+it.detail.join(" ")+" "+it.qs.map(function(x){return x.q}).join(" ")).toLowerCase();
      if(!q.split(/\s+/).every(function(w){return hay.indexOf(w)>=0}))return false}
    return true;
  }
  function stats(items){
    var s={total:items.length,open:0,min:0,sec:100,work:100,nTot:0,nDone:0,by:{},lv:{},ask:0};
    CLS.forEach(function(c){s.by[c]=0});LV.forEach(function(l){s.lv[l]=0});
    items.forEach(function(it){
      if(it.cls==="necessary"){s.nTot+=it.min||30;if(it.done)s.nDone+=it.min||30}
      if(it.done)return;
      s.open++;s.min+=it.min;s.by[it.cls]+=it.min;s.lv[it.level]+=it.min;s.ask+=unanswered(it);
      var p=it.pri;
      if(it.kind==="security")s.sec-=(p==="P1"?25:p==="P2"?8:p==="P3"?2:0);
      else if(it.cls==="necessary"){
        if(it.kind==="broken")s.work-=(p==="P1"?20:p==="P2"?6:p==="P3"?2:0);
        else s.work-=(p==="P1"?10:p==="P2"?3:p==="P3"?1:0)}
    });
    s.sec=Math.max(0,s.sec);s.work=Math.max(0,s.work);s.pct=s.nTot?Math.round(100*s.nDone/s.nTot):100;
    return s;
  }
  function band(n){return n>=80?"good":n>=50?"warn":"bad"}
  function word(n){return n>=80?"good":n>=50?"needs work":"poor"}
  function scores(parent,s){
    var a=el("span","score "+band(s.sec),"Security "+s.sec+" ("+word(s.sec)+")");
    var b=el("span","score "+band(s.work),"Working "+s.work+" ("+word(s.work)+")");
    var c=el("span","score",s.pct+"% done");
    parent.appendChild(a);parent.appendChild(b);parent.appendChild(c);
  }
  function avg(list,key){return list.length?Math.round(list.reduce(function(n,s){return n+s[key]},0)/list.length):100}
  function all(node){var out=[];
    if(node.items)return node.items;
    (node.sections||node.projects||node.computers||[]).forEach(function(c){out=out.concat(all(c))});return out}
  function copy(text,what){
    function ok(){msg("Copied the prompt for "+what+". Paste it into a new Claude session.")}
    if(navigator.clipboard&&navigator.clipboard.writeText)navigator.clipboard.writeText(text).then(ok).catch(function(){window.prompt("Copy this:",text)});
    else window.prompt("Copy this:",text);
  }
  var RULES=" The list is the file ~/nuc-homelab/TODO-ALL.md on the NUC (ssh server@192.168.1.86); the page is /todo. First read HANDOFF.md, then read these items in the file (search for the T-numbers), including my Answer and Note lines. Guide me, don't solve it for me unless I am stuck and ask. Never print secrets. When an item is finished and checked, tick it in the file (\"[ ]\" to \"[x]\") and add anything new you find as new items with ~/learning-src/todo_merge.py (fragment format: ~/nuc-homelab/TODO-ALL-format.md).";
  function pItem(c,p,s,it){return "Let's work on to-do item "+it.id+": \""+it.title+"\" (computer: "+c.name+", project: "+p.name+", section: "+s.name+", about "+(it.time||"30m")+", "+it.level+")."+RULES}
  function pSect(c,p,s){var open=s.items.filter(function(i){return !i.done});
    return "Let's work on one 2-hour section of my to-do list: \""+s.name+"\" (computer: "+c.name+", project: "+p.name+"). Open items: "+open.map(function(i){return i.id+" "+i.title}).join("; ")+". Start with the necessary ones, easiest first."+RULES}
  function pProj(c,p){var st=stats(all(p));
    return "Let's plan my work on the project \""+p.name+"\" on "+c.name+" from my to-do list. It has "+st.open+" open items, about "+hm(st.min)+" of work, in these sections: "+p.sections.map(function(s){return s.name}).join("; ")+". Read the whole project in the file, tell me which section to do first and why, then we start it."+RULES}
  function itemRow(c,p,s,it){
    var row=el("div","item"+(it.done?" done":""));
    var cb=el("input");cb.type="checkbox";cb.checked=it.done;cb.setAttribute("aria-label","Done: "+it.title);
    cb.addEventListener("change",function(){toggle(it,cb.checked,cb)});
    var t=el("div","t"),title=el("div","title");title.setAttribute("role","button");title.tabIndex=0;
    if(it.rec)title.appendChild(el("span","pill","★ first"));
    title.appendChild(el("span","pill",it.level));
    if(it.time)title.appendChild(el("span","pill",it.time));
    if(it.kind)title.appendChild(el("span","pill"+(it.kind==="security"||it.kind==="broken"?" bad":""),it.kind));
    var u=unanswered(it);if(u)title.appendChild(el("span","pill q",u+(u>1?" questions":" question")));
    title.appendChild(document.createTextNode(it.title+" "));
    title.appendChild(el("span","id",it.id+" · "+it.pri));
    var built=false,d=el("div","detail");d.hidden=true;
    function build(){
      built=true;
      it.detail.forEach(function(line){var x=el("div","d"),m=/^(\w[\w ]{0,12}):\s*(.*)$/.exec(line);
        if(m){x.appendChild(el("b",null,m[1]+": "));x.appendChild(document.createTextNode(m[2]))}else x.textContent=line;d.appendChild(x);
        if(m&&m[1]==="Code")d.appendChild(codeBox(it.id))});
      it.qs.forEach(function(x,qi){d.appendChild(qa("Question for you: "+x.q,x.a,function(v){x.a=v;return save(it,qi,v)}))});
      d.appendChild(qa("My note (optional)",it.note,function(v){it.note=v;return save(it,-1,v)}));
      var b=el("button","small","Copy next step for Claude");b.type="button";
      b.addEventListener("click",function(){
        fetch("next/"+it.id,{credentials:"same-origin"}).then(function(r){if(!r.ok)throw new Error(r.status);return r.text()})
          .then(function(t){copy(t,it.id)}).catch(function(){copy(pItem(c,p,s,it),it.id)})});
      d.appendChild(b);
    }
    function flip(){if(!built)build();d.hidden=!d.hidden}
    title.addEventListener("click",flip);
    title.addEventListener("keydown",function(e){if(e.key==="Enter"||e.key===" "){e.preventDefault();flip()}});
    t.appendChild(title);t.appendChild(d);row.appendChild(cb);row.appendChild(t);
    if(q||F.ask){build();d.hidden=false}
    return row;
  }
  function codeBox(id){
    var w=el("div"),b=el("button","small","Show suggested code"),pre=el("pre","code");b.type="button";pre.hidden=true;
    b.addEventListener("click",function(){
      if(!pre.hidden){pre.hidden=true;b.textContent="Show suggested code";return}
      fetch("code/"+id,{credentials:"same-origin"}).then(function(r){if(!r.ok)throw new Error(r.status);return r.text()})
        .then(function(t){pre.textContent=t;pre.hidden=false;b.textContent="Hide suggested code"})
        .catch(function(e){msg("Could not load the code ("+e.message+")")})});
    w.appendChild(b);w.appendChild(pre);return w;
  }
  var qn=0;
  function qa(label,value,saver){
    var w=el("div","qa"),id="qa"+(++qn),l=el("label",null,label);l.htmlFor=id;
    var ta=el("textarea");ta.id=id;ta.value=value||"";ta.maxLength=1500;
    var b=el("button","small","Save");b.type="button";var ok=el("span","saved");
    b.addEventListener("click",function(){ok.textContent="";saver(ta.value.trim()).then(function(){ok.textContent="Saved in the list"})
      .catch(function(e){ok.textContent="";msg("Could not save ("+e.message+"). Reload and sign in again.")})});
    w.appendChild(l);w.appendChild(ta);var r=el("div","row");r.appendChild(b);r.appendChild(ok);w.appendChild(r);return w;
  }
  function det(cls,key,forceOpen){
    var d=el("details",cls);d.open=forceOpen||!!openSet[key];
    d.addEventListener("toggle",function(){if(!forceOpen){openSet[key]=d.open;saveOpen()}});return d;
  }
  function render(){
    var list=document.getElementById("list");list.textContent="";qn=0;
    var everything=all(data),S=stats(everything),force=!!q||F.ask||F.rec;
    var projStats=[];data.computers.forEach(function(c){c.projects.forEach(function(p){projStats.push(stats(all(p)))})});
    document.getElementById("sum").textContent=S.open+" open, "+(S.total-S.open)+" done · about "+Math.round(S.min/60)+" hours left ("+
      Math.round(S.by.necessary/60)+"h necessary) · security "+avg(projStats,"sec")+", working "+avg(projStats,"work")+
      (S.ask?" · "+S.ask+" questions for you":"")+(data.updated?" · changed "+data.updated:"");
    document.querySelector("#bar i").style.width=S.pct+"%";
    document.getElementById("intro").textContent=(data.intro||[]).join(" ");
    var nf=(F.show!=="open"?1:0)+Object.keys(F.cls).filter(function(k){return F.cls[k]}).length+Object.keys(F.level).filter(function(k){return F.level[k]}).length+
      Object.keys(F.kind).filter(function(k){return F.kind[k]}).length+(F.rec?1:0)+(F.ask?1:0);
    document.getElementById("fsum").textContent=nf?"("+nf+" on)":"";
    var shown=0;
    data.computers.forEach(function(c){
      var cItems=all(c),cShown=cItems.filter(match);if(!cShown.length)return;
      var cs=stats(cItems),ps=c.projects.map(function(p){return stats(all(p))});
      var cd=det("comp","c:"+c.name,force),sm=el("summary"),n=el("span","n",c.name);
      var meta=el("span","meta",cs.open+" open · "+hm(cs.min)+" left ("+hm(cs.by.necessary)+" necessary) · "+c.projects.length+" projects");
      n.appendChild(meta);var sc=el("span","meta");scores(sc,{sec:avg(ps,"sec"),work:avg(ps,"work"),pct:cs.pct});n.appendChild(sc);
      sm.appendChild(n);cd.appendChild(sm);
      c.projects.forEach(function(p){
        var pItems=all(p),pShown=pItems.filter(match);if(!pShown.length)return;
        var st=stats(pItems),pd=det("proj","p:"+c.name+"/"+p.name,force),ps2=el("summary"),pn=el("span","n",p.name);
        pn.appendChild(el("span","meta",st.open+" open · "+hm(st.min)+" left · "+p.sections.length+" sections"+(st.ask?" · "+st.ask+" questions":"")));
        var sc2=el("span","meta");scores(sc2,st);pn.appendChild(sc2);ps2.appendChild(pn);pd.appendChild(ps2);
        var built=false;
        function build(){
          if(built)return;built=true;
          var box=el("div","box");box.appendChild(el("h4",null,"Time left in this project: "+hm(st.min)));
          box.appendChild(el("div","line","By class: "+CLS.map(function(k){return CLSN[k]+" "+hm(st.by[k])}).join(" · ")));
          box.appendChild(el("div","line","By difficulty: "+LV.map(function(k){return k+" "+hm(st.lv[k])}).join(" · ")));
          var b=el("button","small","Copy prompt: plan this project with Claude");b.type="button";b.style.marginTop="8px";
          b.addEventListener("click",function(){copy(pProj(c,p),p.name)});box.appendChild(b);pd.appendChild(box);
          var recs=[];p.sections.forEach(function(s){s.items.forEach(function(it){if(it.rec&&!it.done&&match(it))recs.push([s,it])})});
          if(recs.length&&!F.rec){
            recs.sort(function(a,b){return LV.indexOf(a[1].level)-LV.indexOf(b[1].level)});
            var rd=det("sect","r:"+c.name+"/"+p.name,force),rs=el("summary"),rn=el("span","n","★ Recommended first");
            rn.appendChild(el("span","meta",recs.length+" items · "+hm(recs.reduce(function(n,x){return n+x[1].min},0))+" · easiest first"));
            rs.appendChild(rn);rd.appendChild(rs);recs.forEach(function(x){rd.appendChild(itemRow(c,p,x[0],x[1]))});pd.appendChild(rd);
          }
          p.sections.forEach(function(s){
            var items=s.items.filter(match);if(!items.length)return;
            var ss=stats(s.items),sd=det("sect","s:"+c.name+"/"+p.name+"/"+s.name,force),s3=el("summary"),sn=el("span","n",s.name);
            sn.appendChild(el("span","meta",ss.open+" open · "+hm(ss.min)+(ss.min>150?" (more than one 2-hour block)":"")+(ss.ask?" · "+ss.ask+" questions":"")));
            s3.appendChild(sn);sd.appendChild(s3);
            var sb=false;
            function sbuild(){
              if(sb)return;sb=true;
              var b2=el("button","small","Copy prompt: do this section with Claude");b2.type="button";b2.style.margin="4px 12px 6px 20px";
              b2.addEventListener("click",function(){copy(pSect(c,p,s),"this section")});sd.appendChild(b2);
              items.sort(function(a,b){return (CLS.indexOf(a.cls)-CLS.indexOf(b.cls))||(LV.indexOf(a.level)-LV.indexOf(b.level))});
              var last="";items.forEach(function(it){
                if(it.cls!==last){last=it.cls;var m=items.filter(function(i){return i.cls===last&&!i.done}).reduce(function(n,i){return n+i.min},0);
                  sd.appendChild(el("div","clab",CLSN[last]+" · "+hm(m)))}
                sd.appendChild(itemRow(c,p,s,it))});
            }
            if(sd.open)sbuild();sd.addEventListener("toggle",function(){if(sd.open)sbuild()});
            pd.appendChild(sd);
          });
        }
        if(pd.open)build();pd.addEventListener("toggle",function(){if(pd.open)build()});
        shown+=pShown.length;cd.appendChild(pd);
      });
      list.appendChild(cd);
    });
    if(!shown)list.appendChild(el("div","empty","Nothing matches. Open Filters and choose All, or clear the search."));
  }
  function post(path,body){return fetch(path,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body),credentials:"same-origin"})
    .then(function(r){if(!r.ok)throw new Error(r.status);return r.json()})}
  function toggle(it,done,cb){
    post("api/toggle",{id:it.id,done:done}).then(function(){it.done=done;msg(done?"Done: "+it.id:"Reopened: "+it.id);render()})
      .catch(function(e){cb.checked=!done;msg("Could not save ("+e.message+"). Reload and sign in again.")});
  }
  function save(it,qi,text){return post("api/answer",{id:it.id,q:qi,text:text})}
  function load(){fetch("api/list",{credentials:"same-origin"}).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()})
    .then(function(d){data=d;render()}).catch(function(e){document.getElementById("sum").textContent="Could not load the list ("+e.message+"). Reload the page."})}
  document.querySelectorAll("button[data-f]").forEach(function(b){b.addEventListener("click",function(){
    var f=b.getAttribute("data-f"),v=b.getAttribute("data-v");
    if(f==="show"){F.show=v;document.querySelectorAll("button[data-f=show]").forEach(function(x){x.setAttribute("aria-pressed",x===b)})}
    else if(f==="rec"||f==="ask"){F[f]=!F[f];b.setAttribute("aria-pressed",F[f])}
    else{F[f][v]=!F[f][v];b.setAttribute("aria-pressed",!!F[f][v])}
    render()})});
  var qt;document.getElementById("q").addEventListener("input",function(e){clearTimeout(qt);qt=setTimeout(function(){q=e.target.value.trim().toLowerCase();render()},250)});
  document.getElementById("fold").addEventListener("click",function(){openSet={};saveOpen();render()});
  document.getElementById("addf").addEventListener("submit",function(e){e.preventDefault();var i=document.getElementById("addt");
    if(!i.value.trim())return;post("api/add",{title:i.value}).then(function(r){i.value="";msg("Added "+r.id+" under All, Inbox");load()})
      .catch(function(er){msg("Could not add ("+er.message+")")})});
  load();
})();
</script>
</body>
</html>
"""


class H(BaseHTTPRequestHandler):
    server_version = "todo"

    def log_message(self, fmt, *args):
        sys.stderr.write("[{}] {}\n".format(datetime.now(TZ).strftime("%F %T"), fmt % args))

    def send(self, code, body, ctype="application/json"):
        raw = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def allowed(self):
        if (self.headers.get("X-Auth-Email") or "").strip().lower() == ADMIN:
            return True
        self.send(403, '{"error":"not available"}')
        return False

    def do_GET(self):
        if not self.allowed():
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            self.send(200, PAGE, "text/html")
        elif path == "/api/list":
            self.send(200, json.dumps(read_list()))
        elif re.fullmatch(r"/next/T\d{4,}", path):
            # T1017: the autopilot's next-step prompt for one item (todo_next.py), plain text, read-only
            self.send(*next_prompt(path[6:]))
        elif path == "/api/questions":
            # T1017: every unanswered Ask line of the [first] items, one grouped list
            self.send(*questions_json())
        elif re.fullmatch(r"/code/T[0-9]{4,}", path):
            # suggested code for one item (written by the 11-16 plan review), shown as plain text, never run
            # 2026-11-20: the file is resolved and must sit inside CODE_DIR, so the id can never walk out of it
            # (ASCII digits only; the old \d also matched other scripts' digits).
            code_root = os.path.realpath(str(CODE_DIR))
            f = os.path.realpath(os.path.join(code_root, path[6:] + ".md"))
            f = Path(f) if f.startswith(code_root.rstrip(os.sep) + os.sep) else None
            if f is not None and f.is_file():
                self.send(200, f.read_text(encoding="utf-8", errors="replace"), "text/plain")
            else:
                self.send(404, '{"error":"no suggestion for this item"}')
        else:
            self.send(404, '{"error":"not found"}')

    def do_POST(self):
        if not self.allowed():
            return
        # a browser form from another site cannot send JSON without a preflight; refuse anything cross-site anyway
        if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none") or \
                "application/json" not in (self.headers.get("Content-Type") or ""):
            return self.send(403, '{"error":"cross-site"}')
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= MAX_BODY:
                return self.send(413, '{"error":"too big"}')
            body = json.loads(self.rfile.read(n).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError  # noqa: TRY004  caught just below and answered as a 400
        except (ValueError, UnicodeDecodeError):
            return self.send(400, '{"error":"bad request"}')
        path = self.path.split("?")[0]
        if path == "/api/toggle":
            ok = toggle(str(body.get("id", "")), bool(body.get("done")))
            self.send(200 if ok else 404, json.dumps({"ok": ok}))
        elif path == "/api/answer":
            try:
                qi = int(body.get("q", 0))
            except (TypeError, ValueError):
                return self.send(400, '{"error":"bad request"}')
            ok = answer(str(body.get("id", "")), qi, str(body.get("text", "")))
            self.send(200 if ok else 404, json.dumps({"ok": ok}))
        elif path == "/api/add":
            new_id = add(str(body.get("title", "")))
            self.send(200 if new_id else 400, json.dumps({"id": new_id}))
        else:
            self.send(404, '{"error":"not found"}')


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
