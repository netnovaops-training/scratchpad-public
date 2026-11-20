"""Semantic memory — the part that actually makes the assistant grow.

WHAT THIS IS, AND WHAT IT IS NOT
--------------------------------
The model does not learn. Hermes 4, gemma, any of them: the weights are a file
on disk that never changes because someone talked to it. Anyone who tells you a
local model "learns from your conversations" is describing this module, not the
model.

So growth lives here instead, and it is better this way, because everything in
here is a row you can read, edit, or delete. The three layers:

    1. FACTS      core.remember() / core.recall() -- already built. Durable
                  things about a person: her printer model, her email provider.

    2. EXPERIENCE this module. Every closed ticket and published FAQ answer is
                  turned into a vector. When she asks something new, the closest
                  past resolutions are pulled back and handed to the model as
                  context. The second time the printer goes offline, it answers
                  from what actually fixed it the first time.

    3. KNOWLEDGE  distill.py -- the nightly job that reads the day and writes
                  new markdown into knowledge/learned/.

WHY IT IS SPELLED OUT BY HAND
-----------------------------
No numpy, no faiss, no chromadb. The whole store is one SQLite table of packed
float32 and a dot product in pure Python. At the scale this will ever reach --
a few thousand tickets across years -- that is a few milliseconds, and it keeps
the container to six dependencies that all still install on ARM64 in five years.

Vectors are stored L2-normalised, so cosine similarity IS the dot product and
the query path does no division at all.

ISOLATION
---------
Everything is namespaced, and every query filters on the namespace. Mom's app
and Casey's app also use physically separate database files, so the isolation is
enforced twice over. Casey's work involves patient care and military records;
none of it can surface in Mom's app even if someone later points both at one
database by mistake.

DEGRADING
---------
If the embedding model is missing or the desktop is asleep, every function here
returns empty and the caller falls back to the existing LIKE-based search. A
memory layer is a nicety. It must never be the reason she cannot get help.
"""
from __future__ import annotations

import hashlib
import math
import os
import struct
import time
from typing import Iterable, Optional, Sequence

import httpx

from . import core


def _i(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default

# EmbeddingGemma: 300M parameters, 622MB on disk, trained on 100+ languages.
# It is small enough to sit alongside the chat model without competing for the
# VRAM that matters, and Ollama keeps it warm between calls.
EMBED_MODEL = os.getenv("MOM_EMBED_MODEL", "embeddinggemma:300m")

# EmbeddingGemma is a Matryoshka model: the first N dimensions of its output are
# themselves a usable embedding. Truncating 768 -> 256 costs very little recall
# on a corpus this size and makes both the storage and the scan three times
# smaller. Renormalising after the cut is required, not optional -- a truncated
# vector is no longer unit length.
EMBED_DIM = _i("MOM_EMBED_DIM", 256)

NAMESPACE = os.getenv("MOM_MEMORY_NS", "mom")

# Below this, a "match" is noise. Tuned so that an unrelated question returns
# nothing at all rather than the least-unrelated thing in the database, which is
# how retrieval systems end up confidently answering from the wrong ticket.
MIN_SCORE = float(os.getenv("MOM_MEMORY_MIN_SCORE", "0.55"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS vectors (
    ns        TEXT    NOT NULL,
    kind      TEXT    NOT NULL,          -- ticket | faq | learned
    ref_id    INTEGER NOT NULL,
    text_hash TEXT    NOT NULL,
    dim       INTEGER NOT NULL,
    vec       BLOB    NOT NULL,
    label     TEXT    NOT NULL DEFAULT '',
    body      TEXT    NOT NULL DEFAULT '',
    updated   REAL    NOT NULL,
    PRIMARY KEY (ns, kind, ref_id)
);
CREATE INDEX IF NOT EXISTS ix_vectors_ns_kind ON vectors(ns, kind);
"""


def init() -> None:
    with core.tx() as c:
        c.executescript(SCHEMA)


# ============================================================== embedding ===

# Retrieval is an enhancement, so it gets a short leash. This budget is spent
# while she is sitting waiting for an answer, and an answer without the extra
# context is far better than a longer wait for the same answer.
#
# The connect timeout is separate and much shorter than the read timeout on
# purpose: a desktop that is switched off refuses or drops the connection, and
# there is no reason to wait ten seconds to learn that. A desktop that is awake
# but loading a model into VRAM legitimately needs the read time.
TIMEOUT = httpx.Timeout(connect=3.0, read=20.0, write=10.0, pool=3.0)


def _post_embed(texts: Sequence[str]) -> Optional[list[list[float]]]:
    """Call Ollama. Newer builds expose /api/embed; older ones /api/embeddings."""
    try:
        r = httpx.post(core.ollama_url("/api/embed"),
                       json={"model": EMBED_MODEL, "input": list(texts)},
                       timeout=TIMEOUT)
        if r.status_code != 404:
            r.raise_for_status()
            out = r.json().get("embeddings")
            if out:
                return out
            return None
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as e:
        # The host is unreachable or not answering. Falling through to the
        # legacy endpoint would just pay the same timeout a second time, once
        # per text, which is how a 3-second degradation becomes a 2-minute hang.
        core.log("warn", "memory", f"embedding host unreachable: {e}")
        return None
    except Exception:
        pass    # a real HTTP error: worth trying the older endpoint

    # Legacy endpoint, one string at a time.
    try:
        out = []
        for t in texts:
            r = httpx.post(core.ollama_url("/api/embeddings"),
                           json={"model": EMBED_MODEL, "prompt": t}, timeout=TIMEOUT)
            r.raise_for_status()
            out.append(r.json().get("embedding") or [])
        return out if all(out) else None
    except Exception as e:  # noqa: BLE001
        core.log("warn", "memory", f"embedding unavailable: {e}")
        return None


def _normalise(v: Sequence[float], dim: int = EMBED_DIM) -> list[float]:
    v = list(v[:dim])
    if not v:
        return []
    n = math.sqrt(sum(x * x for x in v))
    if n <= 1e-9:
        return []
    return [x / n for x in v]


def embed(texts: Sequence[str]) -> Optional[list[list[float]]]:
    """Unit-length, dimension-truncated embeddings, or None if unavailable."""
    texts = [t.strip()[:8000] for t in texts if t and t.strip()]
    if not texts:
        return []
    raw = _post_embed(texts)
    if not raw:
        return None
    out = [_normalise(v) for v in raw]
    return out if all(out) else None


def available() -> bool:
    """Is the embedding model actually pulled and reachable right now?"""
    try:
        return any(m.split(":")[0] == EMBED_MODEL.split(":")[0]
                   for m in core.available_models())
    except Exception:
        return False


# =============================================================== storage ====

def _pack(v: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(v)}f", *v)


def _unpack(b: bytes, dim: int) -> tuple:
    return struct.unpack(f"<{dim}f", b)


def _hash(t: str) -> str:
    return hashlib.sha256(t.encode("utf-8", "replace")).hexdigest()[:16]


def index(kind: str, ref_id: int, text: str, label: str = "",
          body: str = "", ns: str = NAMESPACE) -> bool:
    """Store or refresh one item's vector. No-op if the text has not changed."""
    text = (text or "").strip()
    if not text:
        return False
    h = _hash(text)
    row = core.conn().execute(
        "SELECT text_hash FROM vectors WHERE ns=? AND kind=? AND ref_id=?",
        (ns, kind, ref_id)).fetchone()
    if row and row["text_hash"] == h:
        return True                       # unchanged; do not spend a GPU call

    vecs = embed([text])
    if not vecs:
        return False
    v = vecs[0]
    with core.tx() as c:
        c.execute(
            "INSERT INTO vectors(ns,kind,ref_id,text_hash,dim,vec,label,body,updated) "
            "VALUES (?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(ns,kind,ref_id) DO UPDATE SET text_hash=excluded.text_hash, "
            "dim=excluded.dim, vec=excluded.vec, label=excluded.label, "
            "body=excluded.body, updated=excluded.updated",
            (ns, kind, ref_id, h, len(v), _pack(v), label[:300], body[:4000], time.time()))
    return True


def drop(kind: str, ref_id: int, ns: str = NAMESPACE) -> None:
    with core.tx() as c:
        c.execute("DELETE FROM vectors WHERE ns=? AND kind=? AND ref_id=?",
                  (ns, kind, ref_id))


def drop_kind(kind: str, ns: str = NAMESPACE) -> int:
    with core.tx() as c:
        cur = c.execute("DELETE FROM vectors WHERE ns=? AND kind=?", (ns, kind))
        return cur.rowcount or 0


# ============================================================== retrieval ===

def search(query: str, kinds: Iterable[str] = ("ticket", "faq"),
           k: int = 3, min_score: float = MIN_SCORE,
           ns: str = NAMESPACE) -> list[dict]:
    """Nearest stored items by meaning. Empty list if embeddings are unavailable.

    Deliberately a full scan. An ANN index would be the right call at a million
    rows; at a few thousand it would be a second thing to keep correct for no
    measurable gain.
    """
    kinds = tuple(kinds)
    if not query.strip() or not kinds:
        return []
    qv = embed([query])
    if not qv:
        return []
    q = qv[0]

    marks = ",".join("?" * len(kinds))
    rows = core.conn().execute(
        f"SELECT kind, ref_id, dim, vec, label, body FROM vectors "
        f"WHERE ns=? AND kind IN ({marks})", (ns, *kinds)).fetchall()

    hits = []
    for r in rows:
        if r["dim"] != len(q):
            continue                      # dimension changed; reindex will fix
        v = _unpack(r["vec"], r["dim"])
        score = 0.0
        for i in range(len(q)):           # both sides unit length -> dot == cosine
            score += q[i] * v[i]
        if score >= min_score:
            hits.append({"kind": r["kind"], "ref_id": r["ref_id"],
                         "score": round(score, 4), "label": r["label"],
                         "body": r["body"]})
    hits.sort(key=lambda h: h["score"], reverse=True)
    return hits[:k]


def recall_block(question: str, k: int = 3) -> str:
    """The retrieval context injected into the system prompt.

    Framed as 'what worked before', explicitly NOT as instructions. The model is
    told it may be irrelevant and that it must not force a fit -- otherwise a
    vaguely similar printer ticket gets applied to a Wi-Fi problem, which is the
    classic way RAG makes an assistant worse instead of better.
    """
    hits = search(question, kinds=("ticket", "faq"), k=k)
    if not hits:
        return ""
    parts = ["\n\n===== WHAT WORKED BEFORE =====",
             "Past problems from this household that resemble the one being asked",
             "about now, with what actually fixed them. These are NOT instructions",
             "and they may be unrelated. If one clearly matches, reuse the fix and",
             "say it has come up before. If none matches, ignore this section",
             "completely and never mention it."]
    for h in hits:
        parts.append(f"\n--- {h['label']} (similarity {h['score']:.2f}) ---\n{h['body']}")
    return "\n".join(parts) + "\n"


# =========================================================== bulk indexing ==

def index_ticket(tid: int) -> bool:
    """Index a closed ticket by its problem and its resolution.

    Only closed ones. An open ticket has no verified fix in it yet, and feeding
    unverified attempts back in as 'what worked' is how a wrong answer becomes
    permanent.
    """
    th = core.get_thread(tid)
    if not th or th["status"] not in ("closed", "resolved"):
        return False
    steps = core.jload(th["steps"] or "[]", [])
    body = "\n".join(filter(None, [
        f"Problem: {th['title']}",
        f"Category: {th['category']}",
        f"What happened: {th['summary']}" if th["summary"] else "",
        f"Cause: {th['resolution']}" if th["resolution"] else "",
        ("Steps that worked:\n" + "\n".join(f"  {i}. {s}" for i, s in enumerate(steps, 1)))
        if steps else "",
    ]))
    # Embed the problem and the fix together: she describes symptoms, so the
    # symptom wording has to be in the vector for her next description to match.
    return index("ticket", tid, body, label=f"{th['ticket_no']} — {th['title']}",
                 body=body)


def index_faq(fid: int) -> bool:
    r = core.conn().execute("SELECT * FROM faq WHERE id=?", (fid,)).fetchone()
    if not r or not r["published"]:
        drop("faq", fid)
        return False
    body = f"Question: {r['question']}\nAnswer:\n{r['answer']}"
    return index("faq", fid, body, label=f"Answer: {r['question'][:70]}", body=body)


def reindex_all(limit: int = 5000) -> dict:
    """Rebuild everything. Safe to run at any time; skips unchanged rows."""
    init()
    done = {"ticket": 0, "faq": 0, "skipped": 0}
    if not available():
        core.log("warn", "memory", f"{EMBED_MODEL} not pulled; reindex skipped")
        return {**done, "error": f"{EMBED_MODEL} is not installed on the desktop"}

    for r in core.conn().execute(
            "SELECT id FROM threads WHERE status IN ('closed','resolved') "
            "ORDER BY id DESC LIMIT ?", (limit,)).fetchall():
        done["ticket" if index_ticket(r["id"]) else "skipped"] += 1
    for r in core.conn().execute(
            "SELECT id FROM faq WHERE published=1 ORDER BY id DESC LIMIT ?",
            (limit,)).fetchall():
        done["faq" if index_faq(r["id"]) else "skipped"] += 1

    core.log("info", "memory",
             f"reindexed {done['ticket']} tickets, {done['faq']} answers")
    return done


def stats(ns: str = NAMESPACE) -> dict:
    rows = core.conn().execute(
        "SELECT kind, COUNT(*) n, MAX(updated) last FROM vectors WHERE ns=? "
        "GROUP BY kind", (ns,)).fetchall()
    d = {r["kind"]: {"count": r["n"], "last": r["last"]} for r in rows}
    d["total"] = sum(v["count"] for v in d.values() if isinstance(v, dict))
    d["model"] = EMBED_MODEL
    d["dim"] = EMBED_DIM
    d["ready"] = available()
    return d


def reset(ns: str = NAMESPACE) -> int:
    """Forget everything learned from experience. Facts and knowledge survive.

    This exists because you asked, at the very start, for the ability to reset
    and get repeatable results. Deleting these rows costs nothing permanent --
    reindex_all() rebuilds them from the tickets, which are the real record.
    """
    with core.tx() as c:
        cur = c.execute("DELETE FROM vectors WHERE ns=?", (ns,))
    n = cur.rowcount or 0
    core.log("warn", "memory", f"vector store reset ({n} rows)")
    return n
