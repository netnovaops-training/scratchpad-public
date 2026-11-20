"""The handbook: things YOU write for her, on any subject.

Separate from the FAQ, deliberately. The FAQ is written BY the assistant when a
ticket closes -- it is a record of what happened. This is written BY YOU, ahead
of time, about anything: how to check the oven temperature, what to do for a
burn, the recipe she always rings you about, which button on the remote.

TWO AUDIENCES, ONE SOURCE
-------------------------
Every entry is both:

    something she can browse and search at /Mom/guides
    something the ASSISTANT retrieves and answers from

That second one is the point. A handbook she has to remember to go and read is
a handbook she will not read. Indexing each entry into the same vector store
the assistant already uses means that when she asks "how long do I cook a
chicken", your answer surfaces -- in your words, with your oven -- instead of a
generic one.

WHY IT IS NOT JUST MORE knowledge/*.md
--------------------------------------
Those files are RULES: absolute, loaded into every single conversation, and
they cost context on every request. A hundred recipes there would crowd out the
safety rules on a 16K context. Handbook entries are retrieved only when
relevant, so the book can grow to any size without making every answer worse.

FIRST AID
---------
Anything in the firstaid or health category renders with an emergency banner
above it, and emergency_check() below runs BEFORE retrieval on every question.
A handbook entry must never sit between her and a 911 call: if what she
describes sounds like an emergency, she is told to call, full stop, and no
lookup happens at all.
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

from . import core

# Beyond the IT categories in tickets.py, because the handbook is not about IT.
CATEGORIES = {
    "firstaid":  "First aid and emergencies",
    "health":    "Health and medicines",
    "cooking":   "Cooking and food safety",
    "recipe":    "Recipes",
    "howto":     "How to do things",
    "home":      "Around the house",
    "phone":     "Phone and tablet",
    "computer":  "Computer",
    "internet":  "Wi-Fi and internet",
    "printer":   "Printer",
    "account":   "Accounts and passwords",
    "safety":    "Scams and staying safe",
    "money":     "Money and bills",
    "people":    "Family and numbers",
    "other":     "Everything else",
}

# Categories where a wrong answer could actually hurt someone. These render
# with the emergency banner and are never the ONLY thing an answer relies on.
SENSITIVE = {"firstaid", "health"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS guides (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    category   TEXT NOT NULL DEFAULT 'other',
    title      TEXT NOT NULL,
    summary    TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL,
    tags       TEXT NOT NULL DEFAULT '',
    published  INTEGER NOT NULL DEFAULT 1,
    pinned     INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_guides_cat ON guides(category);
"""


def init() -> None:
    with core.tx() as c:
        c.executescript(SCHEMA)


# ================================================================ emergency ==

# Matched against her own words BEFORE anything else happens. Deliberately
# broad: a false positive costs her one extra sentence telling her to call for
# help, and a false negative could cost far more than that.
EMERGENCY = re.compile(
    r"\b(can'?t breathe|not breathing|stopped breathing|choking|chest pain|"
    r"heart attack|stroke|unconscious|passed out|won'?t wake|unresponsive|"
    r"bleeding (a lot|badly|everywhere)|won'?t stop bleeding|severe bleeding|"
    r"broken bone|bone is sticking|head injury|hit (my|her|his) head hard|"
    r"overdose|took too many|poison|swallowed|seizure|convulsing|fitting|"
    r"suicid|kill myself|end my life|want to die|"
    r"house is on fire|smoke everywhere|gas leak|smell gas|carbon monoxide|"
    r"i think i'?m dying|call an ambulance)\b", re.I)


def emergency_check(text: str) -> Optional[str]:
    """If this sounds like an emergency, return what to say and stop.

    Runs before retrieval and before the model. No handbook entry, however
    carefully written, should ever be what stands between her and a 999 or 911
    call -- and an assistant that starts explaining first aid while someone is
    not breathing is actively dangerous.
    """
    if not text or not EMERGENCY.search(text):
        return None
    helper = core.HELPER_NAME
    phone = core.HELPER_PHONE
    call_line = f"\n\nThen call {helper}" + (f" on {phone}" if phone else "") + "."
    return (
        "## Please call for help right now\n\n"
        "**Hang up on me and dial 911** (or 999 in the UK). Do that first, "
        "before anything else.\n\n"
        "If someone is with you, ask them to call while you stay with the "
        "person who needs help.\n\n"
        "I am not able to help with an emergency, and I do not want you "
        "spending another second reading a screen." + call_line)


# =================================================================== CRUD ====

def create(category: str, title: str, body: str, summary: str = "",
           tags: str = "", published: bool = True, pinned: bool = False) -> int:
    now = time.time()
    with core.tx() as c:
        gid = c.execute(
            "INSERT INTO guides(category,title,summary,body,tags,published,pinned,"
            "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (category if category in CATEGORIES else "other", title[:200],
             summary[:400], body, tags[:300], 1 if published else 0,
             1 if pinned else 0, now, now)).lastrowid
    reindex(int(gid))
    core.log("info", "guides", f"created #{gid}: {title[:60]}")
    return int(gid)


def update(gid: int, category: str, title: str, body: str, summary: str = "",
           tags: str = "", published: bool = True, pinned: bool = False) -> None:
    with core.tx() as c:
        c.execute("UPDATE guides SET category=?,title=?,summary=?,body=?,tags=?,"
                  "published=?,pinned=?,updated_at=? WHERE id=?",
                  (category if category in CATEGORIES else "other", title[:200],
                   summary[:400], body, tags[:300], 1 if published else 0,
                   1 if pinned else 0, time.time(), gid))
    reindex(gid)


def delete(gid: int) -> None:
    with core.tx() as c:
        c.execute("DELETE FROM guides WHERE id=?", (gid,))
    try:
        from . import memory
        memory.drop("guide", gid)
    except Exception:
        pass


def get(gid: int):
    return core.conn().execute("SELECT * FROM guides WHERE id=?", (gid,)).fetchone()


def listing(category: str = "", query: str = "", published_only: bool = True,
            limit: int = 500) -> list:
    sql, args, where = "SELECT * FROM guides", [], []
    if published_only:
        where.append("published=1")
    if category:
        where.append("category=?")
        args.append(category)
    if query.strip():
        where.append("(title LIKE ? OR body LIKE ? OR tags LIKE ? OR summary LIKE ?)")
        args += [f"%{query.strip()}%"] * 4
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pinned DESC, category, title LIMIT ?"
    args.append(limit)
    try:
        return core.conn().execute(sql, args).fetchall()
    except Exception:
        return []


def counts(published_only: bool = True) -> dict:
    sql = "SELECT category, COUNT(*) n FROM guides"
    if published_only:
        sql += " WHERE published=1"
    sql += " GROUP BY category"
    try:
        return {r["category"]: r["n"] for r in core.conn().execute(sql).fetchall()}
    except Exception:
        return {}


# ================================================================ retrieval ==

def reindex(gid: int) -> bool:
    """Make one entry findable by meaning, not just by matching words."""
    r = get(gid)
    if not r:
        return False
    from . import memory
    if not r["published"]:
        memory.drop("guide", gid)
        return False
    text = "\n".join(filter(None, [r["title"], r["summary"], r["tags"], r["body"]]))
    return memory.index("guide", gid, text[:8000],
                        label=f"{CATEGORIES.get(r['category'], r['category'])}: {r['title']}",
                        body=f"{r['title']}\n\n{r['body']}"[:4000])


def reindex_all() -> int:
    init()
    n = 0
    for r in core.conn().execute("SELECT id FROM guides WHERE published=1").fetchall():
        if reindex(r["id"]):
            n += 1
    core.log("info", "guides", f"reindexed {n} handbook entries")
    return n


def recall_block(question: str, k: int = 2) -> str:
    """Handbook entries relevant to what she just asked.

    Framed as YOUR words rather than the model's, and marked to be preferred
    over anything the model would otherwise invent -- because when you have
    written down which oven she has, that beats a general answer every time.
    """
    from . import memory
    hits = memory.search(question, kinds=("guide",), k=k)
    if not hits:
        return ""
    parts = ["\n\n===== FROM THE HOUSEHOLD HANDBOOK =====",
             f"Written by {core.HELPER_NAME} for this person specifically.",
             "PREFER this over anything general you would otherwise say, because",
             "it reflects her actual home, her actual appliances and her actual",
             "preferences. Quote the steps as written. If it does not cover what",
             "she asked, ignore it and say so plainly rather than stretching it."]
    for h in hits:
        parts.append(f"\n--- {h['label']} ---\n{h['body']}")
    return "\n".join(parts) + "\n"


# =============================================================== importing ===

def import_markdown(text: str, default_category: str = "other") -> int:
    """Bulk-add entries from one markdown file.

    Each `## Heading` starts a new entry. An optional `<!-- category: cooking -->`
    on the line after the heading sets the category. Written this way so you can
    keep the whole handbook in one file, edit it in any editor, and paste it in
    -- rather than clicking through a form fifty times.
    """
    init()
    made = 0
    blocks = re.split(r"^##\s+", text, flags=re.M)
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        lines = block.split("\n")
        title = lines[0].strip()
        rest = "\n".join(lines[1:]).strip()
        cat = default_category
        m = re.search(r"<!--\s*category:\s*([a-z]+)\s*-->", rest, re.I)
        if m:
            cat = m.group(1).lower()
            rest = rest.replace(m.group(0), "").strip()
        if not title or not rest:
            continue
        summary = rest.split("\n")[0][:200]
        create(cat, title, rest, summary=summary)
        made += 1
    return made
