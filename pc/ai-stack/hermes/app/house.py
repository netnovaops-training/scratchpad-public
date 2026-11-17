"""The house tools -- Hermes stops being a stranger in its own home.

Until 2026-08-31 Hermes had file tools, a shell in its own container, and a
web fetcher: capable hands, no knowledge of the household it lives in. These
tools are its eyes on the stack Jason built -- monitoring, the photo
library, the Pee-Wee tribute, the travel map, and the standing to-do list --
each through the service's OWN interface with the least credential that
works, so Hermes can answer "is everything up?", "how many photos does Mom
have?", "who is on the dial?", or "what is still pending?" without anyone
opening a dashboard.

DELIBERATELY NOT HERE: the Docker socket (root of the machine does not
belong in a chat loop), anything that deletes, and the sign-in gate's
people file. Hermes reads the house and appends to the list; it does not
rewire the house. The one write, pending_add, appends a line to PENDING.md
-- the file whose whole purpose is collecting loose ends before they are
lost, which is exactly what a house assistant hears all day.
"""
from __future__ import annotations

import json
import time as _t
import os
import re
import urllib.parse
import urllib.request

KUMA_URL = os.environ.get("HOUSE_KUMA_URL", "http://192.168.1.86:3001")
IMMICH_URL = os.environ.get("HOUSE_IMMICH_URL", "http://192.168.1.86:2283")
IMMICH_KEY = os.environ.get("HOUSE_IMMICH_KEY", "")
SITE_DIR = os.environ.get("HOUSE_PEEWEE_SITE", "/house/peewee")
MAP_DATA = os.environ.get("HOUSE_MAP_DATA", "/house/mapdata")
PENDING = os.environ.get("HOUSE_PENDING", "/house/PENDING.md")
LOKI_URL = os.environ.get("HOUSE_LOKI_URL", "http://192.168.1.205:3100")
GITEA_URL = os.environ.get("HOUSE_GITEA_URL", "http://192.168.1.86:3000")
GITEA_TOKEN = os.environ.get("HOUSE_GITEA_TOKEN", "")
REPOS = ("Jason/Jasons-Site", "Jason/nuc-homelab")


def _q(v: str) -> str:
    """URL-encode one query value."""
    from urllib.parse import quote
    return quote(v or "", safe="")


def _get(url: str, headers: dict | None = None, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def _post(url: str, form: dict, headers: dict | None = None,
          timeout: int = 30) -> str:
    """The first tool here that writes anything.

    Every other house tool reads. zee_remember writes, because "Hermes
    remembers things about her" has to land somewhere she can see and
    correct -- and the honest place for that is her own record, not a
    store only this service holds.
    """
    data = urllib.parse.urlencode(form).encode()
    req = urllib.request.Request(url, data=data, headers=headers or {},
                                 method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def house_status() -> str:
    """Every monitor's current state, via 'The House' status page -- the
    same public-by-design API the family's page uses (Kuma keeps /metrics
    behind its own keys even in proxy-auth mode; the status page is the
    door that is meant to be open)."""
    cfg = json.loads(_get(KUMA_URL + "/api/status-page/family"))
    beats = json.loads(_get(KUMA_URL + "/api/status-page/heartbeat/family"))
    names = {}
    for group in cfg.get("publicGroupList", []):
        for m in group.get("monitorList", []):
            names[str(m["id"])] = m["name"]
    up, down = [], []
    for mid, hb in beats.get("heartbeatList", {}).items():
        name = names.get(str(mid), "monitor " + str(mid))
        latest = hb[-1] if hb else {}
        (up if latest.get("status") == 1 else down).append(name)
    if not up and not down:
        return "The House page has no monitors yet."
    out = []
    if down:
        out.append("DOWN: " + ", ".join(sorted(down)))
    out.append("up: " + ", ".join(sorted(up)))
    return chr(10).join(out)


def photos_stats() -> str:
    """Who has how many photos in the family library.

    Counted from each person's LIBRARY, not from Immich's usage numbers:
    usage counts only uploads, and the Google-sync photos are external
    library assets -- 'Jason: 0 photos' from the usage endpoint while his
    library holds thousands is technically true and completely misleading.
    """
    if not IMMICH_KEY:
        return "no Immich key configured (HOUSE_IMMICH_KEY)"
    users = {u["id"]: (u.get("name") or u.get("email", "?"))
             for u in json.loads(_get(IMMICH_URL + "/api/admin/users",
                                      {"x-api-key": IMMICH_KEY}))}
    libs = json.loads(_get(IMMICH_URL + "/api/libraries",
                           {"x-api-key": IMMICH_KEY}))
    lines = []
    for lib in libs:
        st = json.loads(_get(IMMICH_URL + "/api/libraries/" + lib["id"] + "/statistics",
                             {"x-api-key": IMMICH_KEY}))
        owner = users.get(lib.get("ownerId"), "?")
        lines.append(f"{owner}: {st.get('photos', 0)} photos, "
                     f"{st.get('videos', 0)} videos ({lib.get('name', '?')})")
    no_lib = [n for uid, n in users.items()
              if not any(l.get("ownerId") == uid for l in libs)]
    if no_lib:
        lines.append("no library yet: " + ", ".join(sorted(no_lib)))
    return chr(10).join(lines) or "no libraries"


def tribute_status() -> str:
    """Who is on the Pee-Wee dial, and what each still needs."""
    with open(os.path.join(SITE_DIR, "data", "stations.json"),
              encoding="utf-8") as f:
        d = json.load(f)
    lines = []
    for st in d.get("stations", []):
        narrated = sum(1 for a in st.get("audio", []) if a)
        flags = []
        if st.get("draft"):
            flags.append("draft")
        if not st.get("photos"):
            flags.append("no photos")
        if narrated < len(st.get("stories", [])):
            flags.append(f"{narrated}/{len(st['stories'])} narrated")
        lines.append(f"{st['name']}: {len(st.get('stories', []))} tellings, "
                     f"{len(st.get('photos', []))} photos"
                     + (f" [{', '.join(flags)}]" if flags else ""))
    return "\n".join(lines) or "the dial is empty"


def map_places(person: str = "") -> str:
    """Where somebody has been, from the travel map. Person by first name
    (jason, casey, mom, robin); empty lists everyone's counts."""
    if not os.path.isdir(MAP_DATA):
        return ("the travel-map data lives on the NUC and is not mounted "
                "here yet -- it is on the pending list")
    names = [n[:-5] for n in os.listdir(MAP_DATA)
             if n.endswith(".json") and n != "db.json"]
    if not person:
        out = []
        for n in sorted(names):
            with open(os.path.join(MAP_DATA, n + ".json"), encoding="utf-8") as f:
                d = json.load(f)
            total = sum(len(v) for v in d.get("scratched", {}).values())
            out.append(f"{n}: {total} places scratched")
        return "\n".join(out)
    n = person.strip().lower()
    if n not in names:
        return f"no map for '{person}' (have: {', '.join(sorted(names))})"
    with open(os.path.join(MAP_DATA, n + ".json"), encoding="utf-8") as f:
        d = json.load(f)
    out = []
    for map_name, entries in d.get("scratched", {}).items():
        if entries:
            out.append(f"{map_name}: " + ", ".join(
                f"{e['code']}{' (' + e['year'] + ')' if e.get('year') else ''}"
                for e in entries))
    return "\n".join(out) or f"{person} has not scratched anywhere yet"


def pending_read() -> str:
    """The household's standing to-do list, verbatim."""
    with open(PENDING, encoding="utf-8") as f:
        return f.read()


def pending_add(item: str) -> str:
    """Append one line to the to-do list. Appends only -- never rewrites."""
    item = " ".join(item.split())
    if not item:
        return "nothing to add"
    with open(PENDING, "a", encoding="utf-8", newline="\n") as f:
        f.write(f"- [ ] (via Hermes) {item}\n")
    return f"added: {item}"


def _loki(query: str, minutes: int, limit: int) -> list[str]:
    import time as _t
    import urllib.parse as _up
    end = int(_t.time() * 1e9)
    start = end - minutes * 60 * int(1e9)
    url = (LOKI_URL + "/loki/api/v1/query_range?"
           + _up.urlencode({"query": query, "start": start, "end": end,
                            "limit": limit, "direction": "backward"}))
    d = json.loads(_get(url, timeout=30))
    lines = []
    for stream in d.get("data", {}).get("result", []):
        c = stream.get("stream", {}).get("container", "?")
        for ts, line in stream.get("values", []):
            lines.append((int(ts), f"[{c}] {line.rstrip()}"))
    lines.sort()
    return [l for _, l in lines][-limit:]


def logs_search(text: str = "", container: str = "", minutes: int = 60) -> str:
    """Search the last N minutes of every container's logs, both machines
    (Loki holds them all). text filters lines; container narrows to one
    container name (substring)."""
    sel = f'{{container=~".*{container}.*"}}' if container else '{container=~".+"}'
    q = sel + (f' |~ "(?i){text}"' if text else "")
    lines = _loki(q, max(1, min(minutes, 1440)), 40)
    return chr(10).join(lines) or "nothing matched"


def logs_errors(minutes: int = 30) -> str:
    """Errors, panics and failures across EVERY container in the last N
    minutes -- the 'what just went wrong' question, answered in one call."""
    q = '{container=~".+"} |~ "(?i)(error|panic|fatal|traceback|exception)"'
    # Known-benign chatter that would bury real problems: the Google sync
    # prints an ERROR line per SUCCESSFUL download (upstream noise, proven
    # on this tool's first live run).
    q += ' !~ "(?i)(error=0|no error|error_count|unhandled page event)"'
    lines = _loki(q, max(1, min(minutes, 1440)), 30)
    return chr(10).join(lines) or f"no errors in the last {minutes} minutes"


def git_commits(n: int = 6) -> str:
    """The latest commits in both household repos -- what changed lately."""
    if not GITEA_TOKEN:
        return "no Gitea token configured"
    out = []
    for repo in REPOS:
        try:
            commits = json.loads(_get(
                f"{GITEA_URL}/api/v1/repos/{repo}/commits?limit={min(n,20)}&stat=false",
                {"Authorization": "token " + GITEA_TOKEN}))
        except Exception as e:
            out.append(f"{repo}: unreachable ({e})")
            continue
        out.append(repo + ":")
        for c in commits:
            msg = c.get("commit", {}).get("message", "").splitlines()[0]
            when = c.get("commit", {}).get("committer", {}).get("date", "")[:16]
            out.append(f"  {c.get('sha','')[:7]}  {when}  {msg}")
    return chr(10).join(out)


def git_file(repo: str, path: str) -> str:
    """Read one file from a household repo ('site' = the main stack, 'nuc' =
    the NUC's). Lets Hermes read the actual code, not just its workspace."""
    if not GITEA_TOKEN:
        return "no Gitea token configured"
    full = REPOS[0] if repo.strip().lower() in ("site", "ai-stack", "jasons-site") else REPOS[1]
    import base64
    d = json.loads(_get(f"{GITEA_URL}/api/v1/repos/{full}/contents/" + path,
                        {"Authorization": "token " + GITEA_TOKEN}))
    if isinstance(d, list):
        return chr(10).join(e.get("name", "?") + ("/" if e.get("type") == "dir" else "")
                            for e in d)
    raw = base64.b64decode(d.get("content", "")).decode("utf-8", "replace")
    return raw[:8000]


# --- Casey's career notes ------------------------------------------------------
#
#  Her recordings (transcribed) and her spoken exchanges are one corpus
#  behind the portal's search endpoint. READ-ONLY here, like every other
#  house tool: Hermes can find and quote, never add or edit.

ZEE_URL = os.environ.get("ZEE_URL", "http://casey:8000")
ZEE_AS = os.environ.get("ZEE_AS_EMAIL", os.environ.get("ADMIN_OWNERS", "")).split(",")[0].strip()


def _zee_search(q: str, limit: int, growth_only: bool) -> dict:
    url = (f"{ZEE_URL}/api/notes/search?q={_q(q)}&limit={int(limit)}"
           f"&growth={1 if growth_only else 0}")
    # The portal has no login of its own; it trusts the gate's header,
    # and inside the Docker network this call never crosses the gate.
    return json.loads(_get(url, {"X-Auth-Email": ZEE_AS}))


def _fmt_notes(d: dict, header: str) -> str:
    rows = d.get("results", [])
    if not rows:
        return header + ": nothing recorded that matches"
    out = [f"{header} -- {len(rows)} found"]
    for r in rows:
        when = _t.strftime("%Y-%m-%d", _t.localtime(r.get("ts", 0)))
        star = " *growth*" if r.get("growth") else ""
        text = " ".join((r.get("text") or "").split())[:400]
        out.append(f"[{when}] ({r.get('kind')}){star} {r.get('title', '')}"
                   + (f"\n    {text}" if text else ""))
    return "\n".join(out)


def zee_notes(query: str = "", limit: int = 12) -> str:
    """Search Zanetta's career recordings and conversations for a topic."""
    try:
        return _fmt_notes(_zee_search(query, limit, False),
                          f"Casey's notes matching '{query}'" if query
                          else "Casey's most recent notes")
    except Exception as e:  # noqa: BLE001
        return f"could not read Casey's notes: {e}"


def zee_growth(limit: int = 15) -> str:
    """The moments Zanetta has marked as growth -- what she has learned or
    achieved, in her own words, newest first."""
    try:
        return _fmt_notes(_zee_search("", limit, True),
                          "Casey's growth moments")
    except Exception as e:  # noqa: BLE001
        return f"could not read Casey's notes: {e}"


def zee_progress() -> str:
    """Where Zanetta is up to: entries by status, what is finished but not
    submitted, what has gone quiet, and what she is aiming at.

    Read-only, like every other house tool. The numbers come from the
    portal's own read, so this can never disagree with what her page and
    her spreadsheet say."""
    try:
        d = json.loads(_get(f"{ZEE_URL}/api/progress",
                            {"X-Auth-Email": ZEE_AS}))
    except Exception as e:  # noqa: BLE001
        return f"could not read Casey's progress: {e}"
    lines = d.get("lines") or []
    return "\n".join(lines) if lines else "nothing recorded yet"


# --- her jacket, her targets, and the one tool here that writes --------------
#
#  Jason, 2026-09-06: "allow it to remember things about her", once he had
#  confirmed she will not put PHI or anything classified into it.
#
#  HERMES KEEPS NO MEMORY OF ITS OWN, and that is the design rather than a
#  limitation. A fact learned in conversation is written into HER record,
#  where it persists, where she can see it, and where the portal, the
#  exports, ChatGPT and the next conversation all read the same copy. A
#  memory only one service holds is a memory she cannot correct -- and
#  this record ends up in official packages.

def zee_jacket() -> str:
    """Zanetta's career milestones -- commissioning, promotions, awards, pins.

    The jacket in order. This is what happened TO her, as distinct from
    zee_notes, which is what she has written about what she did.
    """
    try:
        d = json.loads(_get(f"{ZEE_URL}/api/jacket", {"X-Auth-Email": ZEE_AS}))
    except Exception as e:  # noqa: BLE001
        return f"could not read Casey's jacket: {e}"
    rows = d.get("milestones") or []
    if not rows:
        return "nothing on her timeline yet"
    out = []
    for m in rows:
        line = f"{m.get('dtg', '')}  {m.get('title', '')}"
        if m.get("detail"):
            line += f" -- {m['detail'][:160]}"
        out.append(line)
    return "\n".join(out)


def zee_targets() -> str:
    """Which awards Zanetta is chasing, and what is blocking each one."""
    try:
        d = json.loads(_get(f"{ZEE_URL}/api/targets", {"X-Auth-Email": ZEE_AS}))
    except Exception as e:  # noqa: BLE001
        return f"could not read Casey's targets: {e}"
    rows = d.get("targets") or []
    if not rows:
        return "she has not picked a target yet"
    out = []
    for t in rows:
        line = t.get("name", "")
        if t.get("my_status"):
            line += f" [{t['my_status']}]"
        for k, label in (("gate", "gate"), ("blocked_on", "blocked on"),
                         ("next_action", "next")):
            if t.get(k):
                line += f"\n    {label}: {t[k]}"
        if t.get("nominator_required"):
            line += "\n    someone else has to nominate her"
        out.append(line)
    return "\n".join(out)


def zee_remember(fact: str) -> str:
    """Record something you have just learned about Zanetta's career.

    Use this when she tells you something worth keeping -- a date, a
    credential, a result, who is nominating her. It goes into her record
    as a DRAFT for her to confirm: something said in passing is not the
    same as something she has decided belongs in a package.

    She has said she will not put anything patient-identifying or
    classified into this system. Do not be the thing that does.
    """
    fact = (fact or "").strip()
    if len(fact.split()) < 3:
        return "that is too short to be worth keeping"
    try:
        d = json.loads(_post(f"{ZEE_URL}/api/remember",
                             {"fact": fact, "where": "Hermes"},
                             {"X-Auth-Email": ZEE_AS}))
    except Exception as e:  # noqa: BLE001
        return f"could not save that to Casey's record: {e}"
    return (f"saved to her record as a draft (entry {d.get('id')}) -- "
            "she confirms it on her portfolio page")



# ---------------------------------------------------------------------------
#  The household index -- searching everything, and reading what it finds
# ---------------------------------------------------------------------------
# Jason, 2026-09-08: "I want to use Hermes and search items and build context
# out what it finds from the items."
#
# TWO TOOLS, NOT ONE, AND THE SPLIT IS THE WHOLE POINT. Search returns a list
# of matches with a snippet each -- enough to decide which ones matter, cheap
# enough to run several times while narrowing. Read pulls the full text of ONE
# item. An agent with only search either reasons from fragments or has to be
# handed whole documents nobody asked for; with both it looks, chooses, then
# reads, which is how a person uses a search box.
#
# IT SEES WHAT JASON SEES AND NOTHING MORE. The index filters every result by
# the address in X-Auth-Email, applied before ranking, so this reaches his
# documents, recipes, workouts and code -- and cannot reach Mom's or Casey's
# trees except where they have named him. The agent inherits his access rather
# than having its own, and there is no argument for asking on behalf of
# somebody else.
FIND_URL = os.environ.get("FIND_URL", "http://192.168.1.86:8087").rstrip("/")
# THE FALLBACK, AND ONLY THE FALLBACK. Every find_* function below takes the
# caller's address as its first argument, because the index filters results by
# it and answering as the wrong person is a disclosure, not a glitch.
#
# This constant used to BE the identity -- one address, Jason's, sent for
# everybody -- so Casey or Robin asking Hermes to find a document searched his
# tree and got his results. It survives only for the CLI, where there is no
# signed-in person and the operator is whoever is at the keyboard.
FIND_AS = os.environ.get("FIND_AS", "user@example.com")

_EM_TAG = re.compile(r"</?em>")


def find_search(as_email: str, query: str, kind: str = "", limit: int = 10) -> str:
    """Search recipes, workouts, documents and code. Returns matches with ids."""
    if not (query or "").strip():
        return "Give me something to search for."
    try:
        n = max(1, min(int(limit or 10), 25))
    except (TypeError, ValueError):
        n = 10
    url = "{}/api/search?q={}&limit={}".format(FIND_URL, _q(query), n)
    if kind in ("recipe", "workout", "document", "code"):
        url += "&kind=" + kind
    try:
        data = json.loads(_get(url, headers={"X-Auth-Email": as_email or FIND_AS}, timeout=30))
    except Exception as e:  # noqa: BLE001
        return "The index did not answer: {}".format(e)

    hits = data.get("hits") or []
    if not hits:
        return "Nothing of yours matches {!r}.".format(query)

    lines = ["{} match(es) for {!r}:".format(
        data.get("estimatedTotalHits", len(hits)), query)]
    for h in hits:
        snippet = (h.get("_formatted") or {}).get("body") or ""
        # The highlight markup is for a browser and is noise in a transcript.
        snippet = _EM_TAG.sub("", snippet).strip()
        lines.append("")
        lines.append("[{}] {}".format(h.get("kind"), h.get("name")))
        lines.append("  id: {}".format(h.get("id")))
        if snippet:
            lines.append("  {}".format(snippet[:240]))
    lines.append("")
    lines.append("Use find_read(id) for the full text of any of these.")
    return "\n".join(lines)


def find_read(as_email: str, item_id: str) -> str:
    """The full indexed text of one item, by the id find_search gave you."""
    if not (item_id or "").strip():
        return "Give me an id from find_search."
    # Fetched THROUGH the index rather than off disk: the index is the only
    # thing that knows whether this item is his to read, and reading around it
    # would be inventing a second answer to that question.
    # /api/item, not /api/search with an id tacked on. The search endpoint
    # has no id parameter and silently ignored it, so every read came back
    # with whatever the empty query ranked first and was then rejected --
    # which looked exactly like "that id does not exist".
    url = "{}/api/item/{}".format(FIND_URL, _q(item_id))
    try:
        h = json.loads(_get(url, headers={"X-Auth-Email": as_email or FIND_AS}, timeout=30))
    except Exception as e:  # noqa: BLE001
        if "404" in str(e):
            return ("No item with id {!r} that you can read. Run find_search "
                    "again -- ids change when the index rebuilds.".format(item_id))
        return "The index did not answer: {}".format(e)
    if not isinstance(h, dict) or not h.get("id"):
        return "No item with id {!r} that you can read.".format(item_id)
    body = (h.get("body") or "").strip()
    out = ["{}  [{}]".format(h.get("name"), h.get("kind")),
           "path: {}".format(h.get("path"))]
    if h.get("ingredients"):
        out.append("ingredients: {}".format(h["ingredients"]))
    if h.get("steps"):
        out.append("steps: {}".format(h["steps"]))
    if not body and not h.get("ingredients"):
        out.append("(indexed, but no readable text -- probably a scan)")
    if body:
        out.append("")
        # Capped: a 300-page manual would fill the context window with one
        # item and leave no room for the reasoning it was fetched for.
        out.append(body[:12000])
    return "\n".join(out)



def shelves(as_email: str = "") -> str:
    """The named shelves a question can be narrowed to.

    Takes the caller like the find_* tools do, and for the same reason: the
    shelf list is filtered by address, so answering as the wrong person shows
    somebody else's shelves.
    """
    try:
        data = json.loads(_get("{}/api/shelves".format(FIND_URL),
                               headers={"X-Auth-Email": as_email or FIND_AS}, timeout=20))
    except Exception as e:  # noqa: BLE001
        return "The book index did not answer: {}".format(e)
    if data.get("error"):
        return "The book index says: {}".format(data["error"])
    rows = data.get("shelves") or []
    if not rows:
        return "No shelves indexed."
    return "\n".join("{} ({} passages)".format(s.get("name"), s.get("passages"))
                     for s in rows)


def find_passages(as_email: str, question: str, limit: int = 6, book: str = "",
                  shelf: str = "", doc_id: str = "") -> str:
    """Passages from the textbooks, each with the page it is on.

    NARROW WHENEVER YOU CAN. The shelf is mostly IT books, so an unfiltered
    plainly-worded question matches its common words across a hundred
    networking manuals and ranks them above the textbook that answers it.
    `book` and `shelf` take names a person would actually say; `doc_id` is
    kept only so an id from an older call still works.
    """
    if not (question or "").strip():
        return "Give me something to look up."
    try:
        n = max(1, min(int(limit or 6), 20))
    except (TypeError, ValueError):
        n = 6
    url = "{}/api/passages?q={}&limit={}".format(FIND_URL, _q(question), n)
    if (doc_id or "").strip():
        url += "&doc=" + _q(doc_id)
    elif (book or "").strip():
        url += "&book=" + _q(book.strip())
    elif (shelf or "").strip():
        url += "&shelf=" + _q(shelf.strip())
    try:
        data = json.loads(_get(url, headers={"X-Auth-Email": as_email or FIND_AS},
                               timeout=30))
    except Exception as e:  # noqa: BLE001
        return "The book index did not answer: {}".format(e)

    if data.get("error"):
        return "The book index says: {}".format(data["error"])
    passages = data.get("passages") or []
    if not passages:
        # THE ANSWER, not a failure. Saying the shelf does not cover
        # something is the behaviour Jason asked for -- the alternative is
        # composing a plausible paragraph, which is the exact thing this
        # tool exists to avoid.
        return ("Nothing on the shelf covers {!r}. Say so rather than "
                "answering from memory.".format(question))

    lines = ["{} passage(s) for {!r}. QUOTE THESE; do not paraphrase a "
             "clinical fact, and cite the page shown.".format(
                 data.get("total", len(passages)), question)]
    for p in passages:
        lines.append("")
        lines.append("{} -- {}".format(p.get("doc"), p.get("cite")))
        lines.append("  {}".format((p.get("body") or "").strip()[:900]))
    return "\n".join(lines)

REGISTRY = {
    "find_passages": find_passages,
    "shelves": shelves,
    "house_status": house_status,
    "photos_stats": photos_stats,
    "tribute_status": tribute_status,
    "map_places": map_places,
    "pending_read": pending_read,
    "pending_add": pending_add,
    "logs_search": logs_search,
    "logs_errors": logs_errors,
    "git_commits": git_commits,
    "git_file": git_file,
    "zee_notes": zee_notes,
    "zee_growth": zee_growth,
    "zee_progress": zee_progress,
    "zee_jacket": zee_jacket,
    "zee_targets": zee_targets,
    "zee_remember": zee_remember,
    "find_search": find_search,
    "find_read": find_read,
}


# THE TOOLS THAT MUST RUN AS THE PERSON ASKING, not as whoever this process
# was configured for. Named here rather than in agent.py so that adding a
# fifth one cannot leave its identity behind in the other file.
#
# The index filters every result by the address it is handed, so calling any
# of these with the wrong address is a disclosure rather than a glitch.
PER_PERSON = {"find_search", "find_read", "find_passages", "shelves"}
