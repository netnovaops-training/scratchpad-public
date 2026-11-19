"""
Turn a person's activity log into facts worth keeping.

WHY THIS EXISTS
Jason asked for a record: "keep a log of what each person is doing so that
Hermes has a record going forward". A raw log delivers that, but a raw log
is not memory -- by next month it is tens of thousands of words that no
model can read on every turn and no person will ever scroll.

So the log is the record, and this is the part that reads it back and asks:
what, out of all that, is worth remembering about this person? The answers
land in facts/ marked `source: distilled`, which the index renders as
_(inferred)_ so that anything Hermes concluded on its own is visibly
different from something the person told it directly.

WHAT IT DELIBERATELY WILL NOT KEEP
The prompt refuses one-off task chatter, anything transient, and anything
about a person's health, money or relationships. A household assistant that
quietly builds a dossier on family members is not the thing being asked for
here -- the useful memory is preferences, projects, and how somebody likes
to be helped.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .memory import MemoryStore

DISTIL_PROMPT = """You are reading a log of conversations between an assistant \
and ONE person, to decide what is worth remembering about them long term.

Return a JSON array. Each element: {"summary": "...", "body": "...", "kind": "..."}

- summary: a short noun phrase, under 70 characters. This becomes an index line.
- body: one to three sentences of detail, written so it is useful months later.
- kind: one of preference | project | context | skill

KEEP things that stay true and change how you should help this person:
  their preferences and how they like answers ("prefers short answers, no preamble")
  ongoing projects and goals ("is studying for the CompTIA A+ exam")
  durable context ("does not eat beef or pork")
  what they know and do not know ("new to the command line")

DO NOT KEEP:
  one-off requests, or the answer to any single question
  anything that will be untrue next week
  anything about health, money, relationships, or another person
  anything you are inferring rather than reading

If nothing in the log meets that bar, return []. An empty array is a good \
answer and is expected often. Do not invent facts to fill the list.

THE LOG:
"""


def _parse_array(text: str) -> list[dict[str, Any]]:
    """Get a JSON array out of a model reply that may be wrapped in prose."""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-z]*\n?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\[.*\]", text, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except ValueError:
            return []
    return [d for d in data if isinstance(d, dict) and d.get("summary")] \
        if isinstance(data, list) else []


def distil(store: MemoryStore, router, *, job: str = "chat",
           chars: int = 6000) -> list[str]:
    """Read the recent log, write any durable facts, return their summaries.

    Existing facts are shown to the model so it does not re-record what is
    already known -- without that, every run adds a near-duplicate and the
    index becomes a transcript.
    """
    log = store.recent_log(chars=chars)
    if not log.strip():
        return []

    known = [f.summary for f in store.facts()]
    prompt = DISTIL_PROMPT + log
    if known:
        prompt += ("\n\nALREADY KNOWN -- do not repeat these, only add what is "
                   "genuinely new:\n" + "\n".join(f"- {k}" for k in known))

    try:
        reply = router.complete_text(prompt, job=job)
    except Exception:  # noqa: BLE001  distilling is optional; nothing saved on any model failure
        return []

    written = []
    for item in _parse_array(reply):
        summary = str(item.get("summary", "")).strip()[:70]
        if not summary:
            continue
        store.remember(
            summary,
            str(item.get("body") or summary).strip(),
            kind=str(item.get("kind") or "context"),
            source="distilled",
        )
        written.append(summary)
    return written
