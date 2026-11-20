"""Turning a link, or a ticket's own transcript, into a fix in Mama's voice.

WHY THIS EXISTS
----------------
Jason works a ticket from Telegram after it opens, before Mama needs to be
involved at all. Two ways in: paste a link that already has the answer, or
just ask the model to work it out from what she already said. Either way,
the OUTPUT is the same shape -- a short, numbered fix written the way
Mom/knowledge/20-how-to-help.md already says everything to her should read,
because whatever comes out of this lands as a message in HER conversation,
not a separate document Jason reads himself.

WHY IT IS NOT JUST core.chat() WITH A DIFFERENT PROMPT
--------------------------------------------------------
Two real pieces of work: fetching a page like a browser (see Kitchen's
core.py and Hermes' tools.py, which both needed the same fix for the same
reason -- a bare httpx GET gets 403'd by anything with basic bot detection),
and writing the result in a voice this app has already spent real effort
tuning. Duplicating the header fix here rather than importing it: Mom,
Kitchen and Hermes are three separate containers with no shared package.
"""
from __future__ import annotations

import re

import httpx

from . import core

_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/203.0.113.2 Safari/537.36")
_BROWSER_HEADERS = {
    "User-Agent": _UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
             "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

_TAG = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
_ANY_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
_BLANK = re.compile(r"\n\s*\n+")


class LinkFixError(Exception):
    """Could not fetch or could not usefully summarise. Never fatal to the
    ticket itself -- the caller shows this to Jason in Telegram and the
    ticket is left exactly as it was."""


def _page_text(html: str, limit: int = 12000) -> str:
    html = _TAG.sub(" ", html)
    text = _ANY_TAG.sub(" ", html)
    import html as _htmlmod
    text = _htmlmod.unescape(text)
    text = _WS.sub(" ", text)
    text = _BLANK.sub("\n", text)
    return text.strip()[:limit]


def fetch_page(url: str) -> str:
    """Plain text off a page, browser-shaped request. Raises LinkFixError
    with a message worth showing Jason directly, not a raw exception."""
    try:
        r = httpx.get(url, headers=_BROWSER_HEADERS, timeout=20,
                     follow_redirects=True)
    except httpx.RequestError as e:
        raise LinkFixError(f"couldn't reach that link ({type(e).__name__})")
    if r.status_code == 403:
        raise LinkFixError(
            "that site blocked the request (403) -- even browser-shaped "
            "headers don't get past everything")
    if r.status_code >= 400:
        raise LinkFixError(f"that link answered with {r.status_code}")
    return _page_text(r.text)


SYSTEM = """You write short, plain-language fixes for a non-technical woman
who is not confident with technology. She gets scared she'll break
something. Follow these rules exactly:

1. One sentence saying what's going on, in plain words. Never jargon.
2. Two or three numbered steps. Not more.
3. For anything she has to find or tap, say what it LOOKS like, roughly
   WHERE it is, and what she'll see when it worked.
4. End with one line asking what she sees now, so she knows to reply.
5. If the source material doesn't actually cover her situation, say so
   plainly instead of guessing -- a wrong step is worse than no step.
6. No jargon, no preamble, no "as an AI", no headers or markdown -- just
   the sentence, the numbered steps, and the closing question."""


def from_link(url: str, question: str) -> str:
    """Fetch `url`, and turn whatever it says into a fix for `question` --
    the words she originally used to open the ticket. Raises LinkFixError
    on a fetch failure; a fetch that succeeds but doesn't help returns
    that fact plainly (rule 5 above), not an exception."""
    text = fetch_page(url)
    user = (f"She asked: {question}\n\n"
           f"A page that might have the answer:\n{text}")
    return core.chat([
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ], temperature=0.2)


def from_transcript(thread_id: int) -> str:
    """No link -- work it out from what she has already said. Raises
    LinkFixError if there is nothing to work from."""
    th = core.get_thread(thread_id)
    if not th:
        raise LinkFixError("that ticket is gone")
    msgs = core.list_messages(thread_id)
    transcript = "\n\n".join(
        f"{'SHE SAID' if m['role'] == 'user' else 'ASSISTANT SAID'}: {m['content']}"
        for m in msgs if m["role"] in ("user", "assistant"))[:8000]
    if not transcript.strip():
        raise LinkFixError("nothing in this ticket yet to work from")
    user = f"The conversation so far:\n\n{transcript}\n\nWrite the fix."
    return core.chat([
        {"role": "system", "content": SYSTEM},
        {"role": "user", "content": user},
    ], temperature=0.3)
