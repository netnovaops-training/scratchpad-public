"""Ticket lifecycle: classify, document, resolve, publish an FAQ entry, email it.

She never sees the word "ticket" unless she goes looking. From her side she asks
a question and gets an answer. Everything in this module happens behind that.

The flow:

    she asks  ->  classify (what kind of problem is this?)
              ->  answer   (handled in main.py, using the knowledge base)
              ->  she says it worked / it didn't
              ->  on "worked":  document -> FAQ entry -> email you -> close
              ->  on "didn't" x2: escalate -> email you, flagged

Classification is deliberately cheap: one short model call returning JSON. If it
fails, the ticket still exists with category "other" -- a wrong label is a minor
annoyance, a lost ticket is not.
"""
import json
import os

import httpx
import re
import time
from datetime import datetime
from typing import Optional

from . import core, kb, mailer, otobo

CATEGORIES = {
    "computer":  "Windows PC or laptop",
    "phone":     "Android phone or tablet",
    "internet":  "Wi-Fi, router, or connection",
    "printer":   "Printer or scanner",
    "account":   "Email, passwords, or online accounts",
    "safety":    "Scam, suspicious message, or security",
    "cooking":   "Cooking, recipes, or food",
    "life":      "Everyday life, household, or admin",
    "other":     "Anything else",
}

# Categories that are genuinely IT support. Used for escalation urgency and for
# the FAQ's default grouping; cooking questions still get a ticket because you
# asked for a complete record, but they are never escalated to you as urgent.
IT_CATEGORIES = {"computer", "phone", "internet", "printer", "account", "safety"}


def new_ticket_no() -> str:
    """Human-readable and sortable: TH-260728-014"""
    today = datetime.now().strftime("%y%m%d")
    n = core.conn().execute(
        "SELECT COUNT(*) c FROM threads WHERE ticket_no LIKE ?", (f"TH-{today}-%",)
    ).fetchone()["c"]
    return f"TH-{today}-{n + 1:03d}"


def ensure_ticket(thread_id: int) -> str:
    """Give a thread a ticket number the first time it needs one."""
    row = core.get_thread(thread_id)
    if row and row["ticket_no"]:
        return row["ticket_no"]
    no = new_ticket_no()
    with core.tx() as c:
        c.execute("UPDATE threads SET ticket_no=?, status='open' WHERE id=?", (no, thread_id))
    core.log("info", "ticket", f"{no} opened (thread {thread_id})")

    # SYNCED THE MOMENT IT OPENS, not just when it closes. This is the one
    # place "she started a ticket" actually happens, so it is the one place
    # that can tell OTOBO about it -- and an OTOBO Invoker configured on
    # TicketCreate (see ai-stack/n8n/workflows/ticket-waiting.json) is what
    # turns that into a Telegram message while she is still typing, not after
    # the fact. queue()+flush() rather than just queue(): a ticket sitting
    # "queued" until the next scheduled flush could be hours away, and the
    # whole point here is "right now".
    otobo.queue(thread_id)
    otobo.flush(limit=1)
    return no


def _json_from(text: str):
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


CLASSIFY_PROMPT = """\
Classify the question below into exactly one category and write a short title.

Categories (use the key exactly):
{cats}

Return ONLY JSON, no prose, no markdown fence:
{{"category": "<key>", "title": "<max 8 words, plain language>", "urgent": true|false}}

Set "urgent" true ONLY if someone may be being scammed right now, money may be
at risk, or an account may already be compromised.

QUESTION:
{q}
"""


def classify(thread_id: int, question: str) -> dict:
    cats = "\n".join(f"  {k} = {v}" for k, v in CATEGORIES.items())
    try:
        out = core.chat([
            {"role": "system", "content": "You classify support questions. JSON only."},
            {"role": "user", "content": CLASSIFY_PROMPT.format(cats=cats, q=question[:1500])},
        ], temperature=0.1)
        data = _json_from(out) or {}
    except Exception as e:  # noqa: BLE001
        core.log("warn", "ticket", f"classify failed: {e}")
        data = {}

    cat = str(data.get("category", "")).strip().lower()
    if cat not in CATEGORIES:
        cat = "other"
    title = str(data.get("title", "")).strip()[:80] or question[:80]
    urgent = bool(data.get("urgent")) and cat in IT_CATEGORIES

    with core.tx() as c:
        c.execute("UPDATE threads SET category=?, title=? WHERE id=?", (cat, title, thread_id))
    return {"category": cat, "title": title, "urgent": urgent}


DOCUMENT_PROMPT = """\
Write the closing documentation for this support conversation.

Be factual. Use ONLY what is in the transcript -- never invent a step that was
not actually given, and never claim an outcome that was not actually confirmed.

Return ONLY JSON, no prose, no markdown fence:
{{
  "summary":     "<2-3 sentences: what the problem was and what fixed it>",
  "steps":       ["<the steps that actually worked, in order, plain language>"],
  "root_cause":  "<one sentence, or 'Not established' if it genuinely was not>",
  "faq_question":"<how she would ask this again in her own words, one line>",
  "faq_answer":  "<the fix, as short numbered steps she could follow alone>",
  "prevention":  "<one sentence on avoiding it next time, or empty string>"
}}

TRANSCRIPT:
{transcript}
"""


def document_and_close(thread_id: int, escalated: bool = False) -> dict:
    """Produce the record, publish an FAQ entry, email it, close the ticket."""
    th = core.get_thread(thread_id)
    if not th:
        return {}
    ticket_no = th["ticket_no"] or ensure_ticket(thread_id)
    msgs = core.list_messages(thread_id)
    transcript = "\n\n".join(
        f"{'SHE ASKED' if m['role'] == 'user' else 'ANSWER'}:\n{m['content']}"
        for m in msgs if m["role"] in ("user", "assistant"))[:14000]

    # SHE WAS ANSWERED FROM SOMETHING ALREADY DOCUMENTED, VERBATIM --
    # kb.direct_answer() (main.py's ask() flow) tags that reply
    # model="kb:<article id>". Closing on THAT confirmation doesn't need a
    # model call to reword what the article already says in your own words;
    # it needs the article's own content recorded as the resolution, which
    # is "close the ticket with what's documented" done literally rather
    # than paraphrased a second time.
    kb_article = None
    last_answer = next((m for m in reversed(msgs) if m["role"] == "assistant"), None)
    if not escalated and last_answer and (last_answer["model"] or "").startswith("kb:"):
        kb_article = kb.get(last_answer["model"][len("kb:"):])

    doc = {}
    if kb_article:
        doc = {
            "summary": f"Resolved with an already-documented fix: "
                      f"{kb_article.get('title', '')}.",
            "steps": kb_article.get("steps") or [],
            "root_cause": "Not established",
            "faq_question": kb_article.get("title", th["title"]),
            "faq_answer": kb_article.get("answer", ""),
            "prevention": "",
        }
    else:
        try:
            out = core.chat([
                {"role": "system", "content": "You write support documentation. JSON only. "
                                              "Never invent steps or outcomes."},
                {"role": "user", "content": DOCUMENT_PROMPT.format(transcript=transcript)},
            ], temperature=0.2)
            doc = _json_from(out) or {}
        except Exception as e:  # noqa: BLE001
            core.log("error", "ticket", f"{ticket_no} documentation failed: {e}")

    summary = str(doc.get("summary", "")).strip() or "(not generated)"
    steps = doc.get("steps") or []
    if not isinstance(steps, list):
        steps = [str(steps)]
    steps = [str(s).strip() for s in steps if str(s).strip()][:12]
    root = str(doc.get("root_cause", "")).strip()
    prevention = str(doc.get("prevention", "")).strip()

    # AN ESCALATED TICKET IS DOCUMENTED BUT NOT CLOSED.
    #
    # It used to close on escalation, and that is how a problem quietly
    # disappears: she says twice that it did not work, an email goes out, the
    # ticket is marked done, and the only trace is a message in your inbox
    # among a hundred others. Nothing is left in the queue to nag anybody.
    #
    # So escalation now leaves it OPEN with the work recorded on it. It is off
    # her screen either way -- she asked a question and got an answer, she does
    # not see a queue -- but it stays on YOURS until somebody actually fixed
    # it. `closed_at` stays null for the same reason: it is the field that
    # answers "was this ever resolved", and stamping it on an unsolved ticket
    # makes that question unanswerable.
    status = "escalated" if escalated else "closed"
    now = time.time()
    with core.tx() as c:
        if escalated:
            c.execute("UPDATE threads SET status=?, summary=?, resolution=?, "
                      "steps=?, resolved=0 WHERE id=?",
                      (status, summary, root, json.dumps(steps), thread_id))
        else:
            c.execute("UPDATE threads SET status=?, summary=?, resolution=?, "
                      "steps=?, resolved=1, closed_at=? WHERE id=?",
                      (status, summary, root, json.dumps(steps), now, thread_id))

    faq_id = None
    if not escalated:
        faq_id = publish_faq(
            thread_id, ticket_no, th["category"] or "other",
            str(doc.get("faq_question", "")).strip() or th["title"],
            str(doc.get("faq_answer", "")).strip(), steps)
        # AND into the knowledge base, as a draft for you to read.
        #
        # The FAQ row above lives in this app's own database. The knowledge
        # base is a different store -- the one /mama checks BEFORE waking the
        # desktop, so an article there answers instantly and works while the
        # GPU is asleep. A fix that worked is exactly what belongs in it.
        #
        # Only on a solved ticket. An escalation is a record of something that
        # did NOT work, and putting that in the place the app quotes answers
        # from is worse than putting it nowhere. And only when the fix is not
        # ALREADY that article -- kb_article means the answer came straight
        # from the knowledge base, so drafting a second copy of the same
        # article back into the same knowledge base would just be noise.
        if not kb_article:
            offer_to_kb(ticket_no, th, doc, steps)

    reindex_ticket(thread_id)

    body = render_email(ticket_no, th, summary, steps, root, prevention,
                        transcript, escalated)
    subject = (f"[{'ESCALATED' if escalated else 'CLOSED'}] {ticket_no} — "
               f"{th['title'][:60]}")
    mailer.queue(subject, body, ticket_id=thread_id, ticket_no=ticket_no)
    with core.tx() as c:
        c.execute("UPDATE threads SET emailed_at=? WHERE id=?", (now, thread_id))

    core.log("info", "ticket", f"{ticket_no} {status}")
    return {"ticket_no": ticket_no, "summary": summary, "steps": steps, "faq_id": faq_id}


# ---------------------------------------------------------------------------
#  Handing a solved fix to the knowledge base
# ---------------------------------------------------------------------------
KB_URL = os.getenv("MOM_KB_URL", "http://kb:8014/mama/kb/from-ticket")
KB_SECRET = os.getenv("MOM_KB_SECRET", "")


def offer_to_kb(ticket_no: str, th, doc: dict, steps: list) -> None:
    """POST a solved ticket to the kb service as a DRAFT.

    WHY IT IS POSTED RATHER THAN WRITTEN
    This app mounts Admin/data read-only, on purpose: the apps read site data
    and the admin writes it. The kb service owns mom-kb-drafts.json, so it is
    the thing that writes to it. One writer per file.

    WHY IT IS A DRAFT
    Nothing the model wrote reaches her unread. The article arrives at
    /mama/kb/manage next to the pages you imported by hand, and becomes an
    answer only when you publish it. Same reason `direct` is opt-in.

    NEVER RAISES. The ticket is already documented, closed and emailed by the
    time this runs. Losing the draft costs you re-adding an article; failing
    the close would cost the ticket.
    """
    if not KB_URL:
        return

    # HER WORDS, for the matcher. kb.py in this app scores a question against
    # `symptoms` by plain word overlap -- no model, no embedding, so it works
    # while the GPU is asleep. That only pays off if the symptoms are phrases
    # she would actually type, which is what faq_question is written to be.
    symptoms = [s for s in (
        str(doc.get("faq_question", "")).strip(),
        (th["title"] or "").strip(),
    ) if s]

    payload = {
        "ticket_no": ticket_no,
        "title": str(doc.get("faq_question", "")).strip() or th["title"],
        "answer": str(doc.get("faq_answer", "")).strip()
                  or str(doc.get("summary", "")).strip(),
        "steps": steps,
        "symptoms": symptoms,
        "category": th["category"] or "other",
        "note": str(doc.get("prevention", "")).strip(),
        "source": f"ticket {ticket_no}",
    }
    if not payload["answer"]:
        # Nothing useful to write down. Better no article than an empty one:
        # an article with no answer still matches her question and then says
        # nothing, which is worse than the model having a go.
        return

    headers = {"Content-Type": "application/json"}
    if KB_SECRET:
        headers["X-KB-Secret"] = KB_SECRET
    try:
        r = httpx.post(KB_URL, json=payload, headers=headers, timeout=10)
        if r.status_code >= 400:
            core.log("warn", "kb", f"{ticket_no} rejected by the kb: "
                                   f"{r.status_code} {r.text[:120]}")
        else:
            core.log("info", "kb", f"{ticket_no} is waiting as draft {r.text.strip()}")
    except Exception as e:  # noqa: BLE001
        core.log("warn", "kb", f"{ticket_no} could not reach the kb: {e}")


def render_email(ticket_no, th, summary, steps, root, prevention, transcript,
                 escalated: bool) -> str:
    # Her timezone, not the Pi's: this is a record of her day.
    when = core.local_time(th["created_at"], th["username"],
                           "%d %b %Y at %-I:%M %p")
    lines = [
        f"# {ticket_no} — {th['title']}",
        "",
        f"**Status:** {'ESCALATED — needs you' if escalated else 'Closed'}  ",
        f"**Category:** {CATEGORIES.get(th['category'], th['category'])}  ",
        f"**Opened:** {when}  ",
        f"**For:** {th['username']}",
        "",
        "## What happened",
        "",
        summary,
        "",
    ]
    if root:
        lines += ["## Cause", "", root, ""]
    if steps:
        lines += ["## Steps that worked", ""]
        lines += [f"{i}. {s}" for i, s in enumerate(steps, 1)]
        lines += [""]
    if prevention:
        lines += ["## Next time", "", prevention, ""]
    if escalated:
        lines += ["## Why this came to you", "",
                  "The assistant could not resolve it after more than one attempt, "
                  "or it involved something it is not allowed to walk her through. "
                  "The full conversation is below.", ""]
    lines += ["---", "", "## Full conversation", "", "```", transcript[:8000], "```", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------- FAQ ---

def publish_faq(ticket_id: int, ticket_no: str, category: str,
                question: str, answer: str, steps: list) -> Optional[int]:
    if not answer and not steps:
        return None
    if not answer:
        answer = "\n".join(f"{i}. {s}" for i, s in enumerate(steps, 1))
    now = time.time()
    with core.tx() as c:
        fid = c.execute(
            "INSERT INTO faq(ticket_id, ticket_no, category, question, answer, "
            "published, created_at, updated_at) VALUES (?,?,?,?,?,1,?,?)",
            (ticket_id, ticket_no, category, question[:300], answer, now, now)).lastrowid
    reindex_faq(int(fid))
    core.log("info", "faq", f"published #{fid} from {ticket_no}")
    return int(fid)


def list_faq(published_only: bool = True, query: str = "", limit: int = 200):
    sql = "SELECT * FROM faq"
    args: list = []
    where = []
    if published_only:
        where.append("published=1")
    if query.strip():
        where.append("(question LIKE ? OR answer LIKE ? OR category LIKE ?)")
        q = f"%{query.strip()}%"
        args += [q, q, q]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    args.append(limit)
    return core.conn().execute(sql, args).fetchall()


def get_faq(fid: int):
    return core.conn().execute("SELECT * FROM faq WHERE id=?", (fid,)).fetchone()


def update_faq(fid: int, question: str, answer: str, category: str,
               published: bool) -> None:
    with core.tx() as c:
        c.execute("UPDATE faq SET question=?, answer=?, category=?, published=?, "
                  "edited=1, updated_at=? WHERE id=?",
                  (question[:300], answer, category, 1 if published else 0,
                   time.time(), fid))
    reindex_faq(fid)


def delete_faq(fid: int) -> None:
    with core.tx() as c:
        c.execute("DELETE FROM faq WHERE id=?", (fid,))
        c.execute("DELETE FROM search_index WHERE kind='faq' AND ref_id=?", (fid,))


# ------------------------------------------------------------------ search ---

def reindex_faq(fid: int) -> None:
    r = get_faq(fid)
    if not r:
        return
    text = " ".join([r["question"], r["answer"], r["category"], r["ticket_no"]]).lower()
    with core.tx() as c:
        c.execute("INSERT INTO search_index(kind, ref_id, text, updated) VALUES ('faq',?,?,?) "
                  "ON CONFLICT(kind, ref_id) DO UPDATE SET text=excluded.text, "
                  "updated=excluded.updated", (fid, text, time.time()))


def reindex_ticket(tid: int) -> None:
    th = core.get_thread(tid)
    if not th:
        return
    msgs = core.list_messages(tid)
    text = " ".join([th["ticket_no"] or "", th["title"], th["summary"] or "",
                     th["resolution"] or "", th["category"] or ""] +
                    [m["content"] for m in msgs]).lower()
    with core.tx() as c:
        c.execute("INSERT INTO search_index(kind, ref_id, text, updated) "
                  "VALUES ('ticket',?,?,?) ON CONFLICT(kind, ref_id) DO UPDATE SET "
                  "text=excluded.text, updated=excluded.updated",
                  (tid, text[:60000], time.time()))


def search_tickets(query: str = "", status: str = "", category: str = "",
                   limit: int = 100):
    """Search closed and open tickets. Empty query returns the most recent."""
    sql = ("SELECT t.* FROM threads t "
           "LEFT JOIN search_index s ON s.kind='ticket' AND s.ref_id=t.id WHERE 1=1")
    args: list = []
    if query.strip():
        sql += " AND (s.text LIKE ? OR t.ticket_no LIKE ?)"
        args += [f"%{query.strip().lower()}%", f"%{query.strip().upper()}%"]
    if status:
        sql += " AND t.status=?"
        args.append(status)
    if category:
        sql += " AND t.category=?"
        args.append(category)
    sql += " ORDER BY t.updated_at DESC LIMIT ?"
    args.append(limit)
    return core.conn().execute(sql, args).fetchall()


def ticket_counts() -> dict:
    rows = core.conn().execute(
        "SELECT status, COUNT(*) n FROM threads GROUP BY status").fetchall()
    d = {r["status"] or "open": r["n"] for r in rows}
    d["total"] = sum(d.values())
    d["faq"] = core.conn().execute("SELECT COUNT(*) n FROM faq").fetchone()["n"]
    return d
