"""The conversation each person left off in, kept on the server.

JASON, 2026-09-24: "Is it possible to keep the session from the last chat
from the app that that user has without starting fresh."

WHY THIS IS NOT THE MEMORY STORE. MemoryStore holds FACTS somebody chose to
keep -- "my back gate code is 4417", "I prefer metric". This holds the THREAD:
the last thing asked and the last thing answered, so "what about the other
one?" still means something after the app has been closed and reopened. They
are different things with different lifetimes, and merging them would put
every passing remark into long-term memory.

WHY THE SERVER AND NOT THE PHONE. The app already sends its own history, and
that works until the app is closed, reinstalled, or opened on a second
device -- at which point the thread is gone with no warning. Keyed by the
address the gate verified, the thread follows the person instead of the
installation.

KEYED BY PERSON AND SURFACE, NOT BY PERSON ALONE. A question asked in the
garden app and a question asked in the chat app are two conversations; one
shared thread would have the garden app answering "yes, the one we discussed"
about something said in a completely different context.

THE CALLER'S OWN HISTORY STILL WINS. An app that sends history is authoritative
for that turn -- it knows what is on its screen, which is what the person can
actually see. This fills the gap only when nothing was sent.

TRUNCATED HARD, AND OLD THREADS EXPIRE. A thread is a convenience, not an
archive; the transcript is the archive. Keeping everything for ever would grow
a file per person for ever and feed a model a year of small talk.
"""
from __future__ import annotations

import json
import os
import re
import time

ROOT = os.environ.get("HERMES_THREAD_ROOT", "/memory/_threads")

# Turns kept. Twenty is what the HTTP handler already accepted from a caller,
# so a restored thread is never larger than one the app could have sent.
KEEP = 20
MAX_CHARS = 8000

# A thread older than this is not resumed. Picking up a fortnight-old
# conversation mid-sentence is more confusing than starting fresh, and the
# person has long since moved on.
STALE_AFTER = 14 * 24 * 3600


def _safe(s, fallback):
    """A filename that cannot leave the directory it belongs in.

    The address comes from the gate rather than the request body, so it is
    already trusted -- but it becomes a PATH here, and a component that is
    trusted for identity is not automatically safe for a filesystem.
    """
    out = re.sub(r"[^A-Za-z0-9._@-]", "_", str(s or ""))[:120]
    return out or fallback


def _path(email, surface):
    return os.path.join(ROOT, _safe(email, "unknown"),
                        _safe(surface, "app") + ".json")


def load(email, surface):
    """The turns this person left off in, or [] -- never raises.

    NEVER RAISES, because a thread is a nicety. A corrupt file, a full disk
    or a permissions change must cost somebody their scrollback, not their
    ability to ask a question.
    """
    try:
        with open(_path(email, surface), encoding="utf-8") as fh:
            blob = json.load(fh)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(blob, dict):
        return []
    if time.time() - float(blob.get("updated") or 0) > STALE_AFTER:
        return []
    out = []
    for t in (blob.get("turns") or [])[-KEEP:]:
        if not isinstance(t, dict):
            continue
        role, content = t.get("role"), t.get("content")
        if role in ("user", "assistant") and isinstance(content, str) and content:
            out.append({"role": role, "content": content[:MAX_CHARS]})
    return out


def save(email, surface, turns):
    """Write the thread back. Never raises, for the same reason."""
    turns = [t for t in turns
             if isinstance(t, dict)
             and t.get("role") in ("user", "assistant")
             and isinstance(t.get("content"), str) and t["content"]]
    if not turns:
        return False
    path = _path(email, surface)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # Written beside and renamed, so a crash mid-write leaves the
        # previous thread intact rather than a half-file that loads as
        # nothing. Rename is atomic on the same filesystem.
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"updated": time.time(),
                       "turns": [{"role": t["role"],
                                  "content": t["content"][:MAX_CHARS]}
                                 for t in turns[-KEEP:]]}, fh)
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001
        return False


def clear(email, surface):
    """Start fresh. Used when the person asks, never on its own."""
    try:
        os.remove(_path(email, surface))
        return True
    except Exception:  # noqa: BLE001
        return False
