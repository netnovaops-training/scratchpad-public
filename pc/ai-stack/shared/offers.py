"""Ask once, keep it everywhere: the household's shared yes/no queue.

JASON, 2026-09-24: if the chat solves his mother's IT problem, ask whether
that was the answer and put it in the KB. If it finds somebody a recipe
they loved and it is not in /recipe, ask whether to keep it. If Robin asks a
gardening question, build the answer into something he can come back to.
"And so on and so forth between my other containers."

THIS IS NOT A NEW IDEA HERE, IT IS THE EXISTING ONE GENERALISED. The
garden already works exactly this way: garden-ingest proposes a vocabulary
change, nothing is written, a person says yes on /garden-review, and only
then does it land. That pattern has been in production since September and
it is the right shape -- so this is the same table with the target made a
column instead of being the whole service.

WHY ONE QUEUE AND NOT ONE PER CONTAINER. Three queues means three places
to check, and a queue nobody checks is worse than no queue: it collects
things that look handled and are not. One page, everything waiting,
whoever it is waiting on.

NOTHING IS WRITTEN UNTIL SOMEBODY SAYS SO, and that is the whole point
rather than a safety feature bolted on. A chat that silently added its own
answers to the knowledge base would fill it with the confident mistakes
nobody caught, and those are far more expensive to remove than to refuse.

THE OFFER CARRIES THE ANSWER, NOT A PROMISE TO FETCH IT. By the time
somebody decides, the conversation that produced it may be hours gone. So
the payload is complete at the moment of asking -- the whole recipe, the
whole fix -- and accepting it is a write, not a re-derivation.

WHO IS ASKED IS RECORDED, because "did that solve it?" is a question only
the person with the problem can answer. Mama's ticket is not Jason's to
close.
"""
from __future__ import annotations

import json

KINDS = {
    # kind -> (where it goes when accepted, what to call it out loud)
    "kb":      ("kb", "a guide in the knowledge base"),
    "recipe":  ("recipe-app", "a recipe in the library"),
    "garden":  ("garden-wiki", "a page in Robin's garden wiki"),
    "note":    ("paperless", "a filed document"),
}

STATUS = ("pending", "kept", "declined", "failed")

SCHEMA = """
CREATE TABLE IF NOT EXISTS offer (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  kind        TEXT NOT NULL,
  title       TEXT NOT NULL,
  summary     TEXT NOT NULL DEFAULT '',
  payload     TEXT NOT NULL,
  asked_of    TEXT NOT NULL DEFAULT '',
  surface     TEXT NOT NULL DEFAULT '',
  session     TEXT NOT NULL DEFAULT '',
  status      TEXT NOT NULL DEFAULT 'pending',
  decided_by  TEXT NOT NULL DEFAULT '',
  decided_at  TEXT,
  result      TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL DEFAULT (datetime('now'))
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS ix_offer_open ON offer(status, asked_of)",
    "CREATE INDEX IF NOT EXISTS ix_offer_made ON offer(created_at)",
]


def ensure(cx):
    cx.execute(SCHEMA)
    for ix in INDEXES:
        cx.execute(ix)
    cx.commit()


def offer(cx, kind, title, payload, asked_of="", summary="",
          surface="", session=""):
    """Record something worth keeping. Writes nothing to the target.

    Returns the offer id. Deliberately cheap and deliberately inert: making
    an offer must never be the thing that fails a conversation.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown kind: {kind}")
    title = (title or "").strip()[:200]
    if not title:
        raise ValueError("an offer needs a title")

    cur = cx.execute(
        "INSERT INTO offer (kind, title, summary, payload, asked_of, "
        "surface, session) VALUES (?,?,?,?,?,?,?)",
        (kind, title, (summary or "").strip()[:600],
         json.dumps(payload), (asked_of or "").strip().lower(),
         surface, session))
    cx.commit()
    return cur.lastrowid


def pending_for(cx, who="", limit=50):
    """What is waiting. Empty `who` means everything.

    ORDERED OLDEST FIRST, unlike almost every other list here. A queue
    shown newest-first quietly buries the thing that has been waiting
    longest, which is the one most likely to be forgotten.
    """
    sql = ("SELECT id, kind, title, summary, asked_of, created_at "
           "FROM offer WHERE status='pending'")
    args = []
    if who:
        sql += " AND (asked_of=? OR asked_of='')"
        args.append(who.strip().lower())
    sql += " ORDER BY created_at ASC LIMIT ?"
    args.append(limit)
    return [dict(r) for r in cx.execute(sql, args).fetchall()]


def get(cx, offer_id):
    row = cx.execute("SELECT * FROM offer WHERE id=?", (offer_id,)).fetchone()
    return dict(row) if row else None


def decide(cx, offer_id, keep, who, apply=None):
    """Yes or no on one offer.

    `apply` is the function that actually writes to the target container.
    It is passed in rather than imported so this module knows nothing about
    any of them -- adding a fifth destination is a line in KINDS and a new
    applier, not a change here.

    A FAILED WRITE IS RECORDED AS FAILED, not as kept. The most useless
    thing this could do is tell somebody their fix was saved when the
    knowledge base was down.
    """
    row = get(cx, offer_id)
    if not row:
        return {"ok": False, "error": "no such offer"}
    if row["status"] != "pending":
        return {"ok": False, "error": f"already {row['status']}"}

    if not keep:
        cx.execute("UPDATE offer SET status='declined', decided_by=?, "
                   "decided_at=datetime('now') WHERE id=?", (who, offer_id))
        cx.commit()
        return {"ok": True, "status": "declined"}

    result, status = "", "kept"
    if apply:
        try:
            result = str(apply(row["kind"], json.loads(row["payload"])))[:600]
        except Exception as e:  # noqa: BLE001
            result, status = f"{type(e).__name__}: {e}"[:600], "failed"

    cx.execute("UPDATE offer SET status=?, decided_by=?, "
               "decided_at=datetime('now'), result=? WHERE id=?",
               (status, who, result, offer_id))
    cx.commit()
    return {"ok": status == "kept", "status": status, "result": result}


def counts(cx):
    rows = cx.execute("SELECT status, COUNT(*) n FROM offer "
                      "GROUP BY status").fetchall()
    return {r["status"]: r["n"] for r in rows}
