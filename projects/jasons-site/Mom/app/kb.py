"""The knowledge base: problems that are already solved.

WHAT THIS IS FOR
----------------
Some questions have one right answer and it does not change. The printer says
offline. The tablet will not join the Wi-Fi. The email app wants a password it
has never asked for before. Once you have worked one of those out, nothing is
gained by working it out again -- and a 12B model reasoning its way to your
answer from scratch is slower, less consistent, and occasionally wrong in a way
your written answer never is.

So this module holds the answers you have already written down, and gets to
them BEFORE the model is asked anything. Two levels:

    DIRECT      a strong match on an article marked `"direct": true` is
                answered from the file. No model call at all. It is instant,
                it is byte-for-byte what you wrote, and it works while the GPU
                is asleep -- see the note on that below, it is the best part.

    CONTEXT     a decent-but-not-certain match is handed to the model as a
                known-good procedure to follow, ranked above anything it
                learned on its own. It still answers in its own voice and can
                adapt to what she actually said.

WHY IT IS A FILE IN THE ADMIN AND NOT A TABLE IN HERE
-----------------------------------------------------
`guides_book.py` already does the database version of this, and it is the right
tool when you are sitting down to write a handbook entry properly. This is the
other case: you just fixed something, you want it written down in the next
ninety seconds, and you are on a phone. The admin's slipstream page takes a
paste of JSON, so "ask a model to turn what I just did into an article, paste
it in" is the whole workflow. The file is then readable by anything else that
wants it, which a SQLite row in a Docker volume is not.

THE SLEEPING GPU
----------------
This app wakes a desktop to answer. That is seconds of waiting on a good day
and a queued-until-later message on a bad one. A direct KB hit skips all of it
-- the answer is on screen before the wake request would have been sent. For
the questions she asks most often, which are exactly the ones worth writing an
article about, the app stops depending on the GPU at all.

WHY MATCHING IS PLAIN CODE AND NOT EMBEDDINGS
---------------------------------------------
memory.py already does semantic search, and it is better at "these two
sentences mean the same thing". It also needs the embedding model, which needs
the desktop, which is the thing this is trying to avoid. Word overlap against
symptoms YOU wrote is worse at nuance and far better at being available -- and
because you write the symptom lines, you can fix a miss by adding the phrase
she actually used. A missed match costs one normal model answer. There is no
failure mode here worse than "it did what it used to do".

SAFETY
------
Nothing here bypasses anything. `guides_book.emergency_check` still runs first,
`safety.screen_user` still runs before this, and the direct answer is still put
through `safety.screen_assistant` on the way out, exactly like a model's would
be. An article can be wrong; it cannot be dangerous in a way the screens do not
already catch.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from . import core

# Written by the admin, mounted read-only. The path is the same shape the
# landing page and the phone line use, so there is one answer to "where does
# site data live".
KB_FILE = Path(os.getenv("MOM_KB_FILE", "/data/site/mom-kb.json"))

# How often the file's timestamp is worth re-checking. A stat() is cheap; doing
# one per question is fine, and this keeps it honest anyway.
POLL_SECONDS = float(os.getenv("MOM_KB_POLL", "10"))


def _f(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


# The two thresholds, and the reasoning behind where they sit.
#
# Scores below are normalised to roughly 0..1. DIRECT is deliberately high: a
# wrong direct answer is the worst thing this module can do, because it is
# confident, instant, and skips the model that might have noticed the question
# was actually about something else. Being too cautious costs a few seconds of
# GPU time; being too eager costs her trust.
DIRECT_SCORE = _f("MOM_KB_DIRECT_SCORE", 0.62)
CONTEXT_SCORE = _f("MOM_KB_CONTEXT_SCORE", 0.28)

# Words that carry no signal about which problem this is. Without this, "how do
# I" matches every article that happens to contain "do".
_STOP = {
    "the", "a", "an", "and", "or", "but", "if", "is", "it", "its", "i", "im",
    "my", "me", "you", "your", "to", "of", "in", "on", "at", "for", "with",
    "do", "does", "did", "doing", "how", "what", "why", "when", "where", "can",
    "cant", "cannot", "wont", "will", "would", "should", "could", "have", "has",
    "had", "get", "got", "am", "are", "was", "were", "be", "been", "this",
    "that", "there", "here", "not", "no", "now", "please", "help", "again",
    "any", "some", "all", "about", "from", "just", "very", "so", "then", "than",
}

# A cache of the parsed file, so the common path is a dict lookup rather than a
# read and a parse.
_cache: dict[str, Any] = {"stamp": None, "checked": 0.0, "articles": []}


# =============================================================== loading =====

def _words(text: str) -> set[str]:
    """Lowercase word set, minus the noise. Two letters or fewer is noise."""
    return {w for w in re.findall(r"[a-z0-9']+", (text or "").lower())
            if len(w) > 2 and w not in _STOP}


def _as_list(v: Any) -> list[str]:
    """Accept a list, or a single string, or nothing.

    Written this way because these files are hand-made and pasted from models,
    and `"keywords": "printer, offline"` is a completely reasonable thing for
    either to produce. Refusing it would mean an article that silently never
    matches, which is the hardest kind of mistake to notice.
    """
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        parts = [p.strip() for p in v.split(",")] if "," in v else [v.strip()]
        return [p for p in parts if p]
    return []


def _prepare(raw: dict) -> Optional[dict]:
    """Turn one entry from the file into something matchable.

    Returns None for an entry with no title and no answer -- there is nothing
    useful to be done with it and listing it would only make the count lie.
    """
    title = str(raw.get("title") or "").strip()
    answer = str(raw.get("answer") or "").strip()
    steps = _as_list(raw.get("steps"))
    if not title or not (answer or steps):
        return None

    symptoms = _as_list(raw.get("symptoms"))
    keywords = _as_list(raw.get("keywords")) + _as_list(raw.get("tags"))

    return {
        "id": str(raw.get("id") or "").strip() or title.lower().replace(" ", "-")[:60],
        "title": title,
        "category": str(raw.get("category") or "other").strip().lower(),
        "answer": answer,
        "steps": steps,
        "symptoms": symptoms,
        "keywords": keywords,
        "source": str(raw.get("source") or "").strip(),
        "note": str(raw.get("note") or raw.get("notes") or "").strip(),
        # Unix seconds, stamped by the standalone /mama/kb app when this was
        # published (or imported, for an article that predates publishing
        # existing at all). Only ever used to sort "most recent" -- nothing
        # here depends on it being exact.
        "published": raw.get("published") or raw.get("imported") or 0,
        # Opt IN, not opt out. An article added in a hurry gets the safe
        # behaviour -- shown to the model, not read out as final -- and only
        # becomes an instant answer once you have said it is good enough.
        "direct": bool(raw.get("direct")),
        # Precomputed so matching never re-tokenises the whole file per
        # question. The title is included because it is usually the clearest
        # description of the problem in the whole article.
        "_title_words": _words(title),
        "_symptom_words": [_words(s) for s in symptoms],
        "_key_words": {w for k in keywords for w in _words(k)},
    }


def load(force: bool = False) -> list[dict]:
    """The articles, re-read when the file changes.

    A missing file is the normal state before anything has been written, and is
    not logged as a problem. A file that is present and broken IS logged --
    someone is looking at an admin page that says the article is there, and the
    log has to be what explains why the app disagrees.
    """
    now = time.time()
    if not force and now - _cache["checked"] < POLL_SECONDS:
        return _cache["articles"]
    _cache["checked"] = now

    try:
        st = KB_FILE.stat()
        stamp = (st.st_mtime, st.st_size)
    except OSError:
        if _cache["stamp"] is not None:
            core.log("info", "kb", "the knowledge base file is gone; using none")
        _cache["stamp"] = None
        _cache["articles"] = []
        return []

    if not force and stamp == _cache["stamp"]:
        return _cache["articles"]

    try:
        raw = json.loads(KB_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        # Keep whatever was last good. A half-written file being read at the
        # instant the admin saves it must not empty the knowledge base.
        core.log("error", "kb", f"could not read {KB_FILE}: {e}")
        return _cache["articles"]

    items = raw.get("articles") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        core.log("error", "kb", "the knowledge base has no 'articles' list; ignoring it")
        return _cache["articles"]

    articles = [a for a in (_prepare(i) for i in items if isinstance(i, dict)) if a]
    _cache["stamp"] = stamp
    _cache["articles"] = articles
    core.log("info", "kb", f"loaded {len(articles)} articles from {KB_FILE}")
    return articles


def count() -> int:
    return len(load())


def get(article_id: str) -> Optional[dict]:
    return next((a for a in load() if a["id"] == article_id), None)


# ============================================================== matching =====

def score(article: dict, asked: set[str]) -> float:
    """How well one article answers this question, roughly 0..1.

    THE SYMPTOM LINE IS THE STRONGEST SIGNAL, on purpose. Symptoms are the
    phrases YOU predicted she would use -- "it says offline", "nothing comes
    out" -- so an article that matches most of one of them is almost certainly
    the right article. Coverage is measured against the SYMPTOM's words, not
    the question's: a long, chatty question should not dilute a symptom it
    contains word for word.
    """
    if not asked:
        return 0.0

    best_symptom = 0.0
    for words in article["_symptom_words"]:
        if not words:
            continue
        hit = len(words & asked) / len(words)
        best_symptom = max(best_symptom, hit)

    title = article["_title_words"]
    title_hit = len(title & asked) / len(title) if title else 0.0

    keys = article["_key_words"]
    key_hit = len(keys & asked) / len(keys) if keys else 0.0

    # Weighted so that a full symptom match alone clears DIRECT, a title match
    # alone does not, and keywords can only ever push a near-miss over the line.
    s = 0.62 * best_symptom + 0.28 * title_hit + 0.10 * key_hit

    # A question that mentions nothing this article talks about is a zero, not
    # a small number. Without this, an article with two keywords scores on any
    # question containing one common word.
    if best_symptom == 0.0 and title_hit == 0.0 and key_hit == 0.0:
        return 0.0
    return min(s, 1.0)


def best(question: str) -> tuple[Optional[dict], float]:
    """The single best article for this question, and how good the match is."""
    asked = _words(question)
    if not asked:
        return None, 0.0
    ranked = sorted(((score(a, asked), a) for a in load()),
                    key=lambda p: -p[0])
    if not ranked or ranked[0][0] <= 0.0:
        return None, 0.0
    return ranked[0][1], ranked[0][0]


def search(query: str, limit: int = 20) -> list[dict]:
    """Articles worth showing a human, best first. For the admin screens."""
    asked = _words(query)
    if not asked:
        return load()[:limit]
    ranked = sorted(((score(a, asked), a) for a in load()), key=lambda p: -p[0])
    return [a for s, a in ranked if s > 0][:limit]


# =============================================================== answering ===

def render(article: dict) -> str:
    """One article as the markdown she actually reads.

    Deliberately plain. The steps are numbered because she follows them with a
    finger on the screen, and the closing line exists because an answer that
    just stops leaves her unsure whether that was all of it.
    """
    parts = [f"## {article['title']}"]
    if article["answer"]:
        parts.append(article["answer"])
    if article["steps"]:
        parts.append("\n".join(f"{i}. {s}" for i, s in enumerate(article["steps"], 1)))
    if article["note"]:
        parts.append(f"_{article['note']}_")
    parts.append(
        "**Did that sort it?** Tell me if it didn't and I'll work through it "
        "with you properly."
    )
    return "\n\n".join(parts)


def direct_answer(question: str) -> Optional[dict]:
    """A written answer good enough to send without asking the model.

    Returns the article plus its score, or None. Two conditions, both required:
    the match has to be strong, AND you have to have marked the article as safe
    to send verbatim. Either one alone is not enough -- a strong match on a
    half-written article is exactly the case this is protecting against.
    """
    article, s = best(question)
    if not article or s < DIRECT_SCORE or not article["direct"]:
        return None
    return {"article": article, "score": s}


def recall_block(question: str, k: int = 2) -> str:
    """Known fixes, as a block for the system prompt.

    Framed the same way `guides_book.recall_block` frames the handbook: this is
    YOUR answer, prefer it, quote the steps, and say so plainly if it does not
    actually cover what she asked rather than stretching it to fit. That last
    instruction is what stops a near-match becoming a confidently wrong answer.
    """
    asked = _words(question)
    if not asked:
        return ""
    ranked = sorted(((score(a, asked), a) for a in load()), key=lambda p: -p[0])
    hits = [(s, a) for s, a in ranked if s >= CONTEXT_SCORE][:k]
    if not hits:
        return ""

    parts = [
        "\n\n===== ALREADY SOLVED — KNOWN FIXES =====",
        f"Written down by {core.HELPER_NAME} after these exact problems were "
        "fixed before, in this house, on these machines.",
        "PREFER this over anything you would work out yourself. Quote the steps "
        "as written and in the same order. Do not improve them.",
        "If it does not actually cover what she asked, ignore it completely and "
        "say so plainly rather than stretching it to fit.",
    ]
    for _s, a in hits:
        body = render(a).replace("**Did that sort it?** Tell me if it didn't and "
                                 "I'll work through it with you properly.", "").strip()
        parts.append(f"\n--- {a['title']} ---\n{body}")
    return "\n".join(parts) + "\n"


def stats() -> dict:
    """For the admin panel: is there a knowledge base, and is it being used."""
    arts = load()
    return {
        "file": str(KB_FILE),
        "exists": KB_FILE.exists(),
        "total": len(arts),
        "direct": sum(1 for a in arts if a["direct"]),
        "categories": sorted({a["category"] for a in arts}),
    }
