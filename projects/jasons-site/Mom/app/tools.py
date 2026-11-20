"""Letting the model go and look something up, instead of guessing.

WHAT THIS CHANGES
-----------------
Until now every answer was one call: the app pre-fetched what it thought would
be useful -- a couple of known fixes, a couple of past tickets, a guide -- put
it all in the prompt, and asked once. That works well when the pre-fetch
guessed right and badly when it did not, because the model had no way to say
"that is not what I needed, let me look for something else".

Now it can. It gets four tools, uses the ones it wants, and answers from what
comes back.

WHY TWO ROUNDS AND NOT TWELVE
-----------------------------
Hermes -- the agent next door -- allows twelve rounds of tool calls, and that
is right for Hermes: you are sat at a terminal, you can see it working, and a
job worth doing is worth ninety seconds.

This is the opposite situation. An 82-year-old is looking at a page that says
"thinking…", on a 7B model where one round trip is nine to ten seconds. Twelve
rounds is a two-minute wait with no way to tell whether it has hung. So the
budget here is TWO, which costs at most about twenty seconds on top of the
answer, and then the model answers with whatever it has.

Running out of rounds is not an error and produces no apology. It answers from
what it found, which is the same thing a person does.

WHY THE DETERMINISTIC PATH STILL RUNS FIRST
-------------------------------------------
None of this replaces kb.direct_answer. A strong match on an article you have
marked as safe is still sent verbatim with no model involved at all -- instant,
in your words, and it works while the GPU is asleep. The tool loop is for the
questions that fall past it, which is the only place a model was ever needed.

FOUR TOOLS ARE LOCAL AND CHEAP. ONE IS NOT, ON PURPOSE, LAST.
---------------------------------------------------------------
search_known_fixes, search_past_help, search_guides and check_this_machine are
files and a SQLite table on this machine, each a few milliseconds -- a wasted
call among these four costs the round trip to the model and nothing else.

search_online is the one exception, added deliberately, and it is described
to the model as the LAST resort specifically because it is not free in the
way the other four are: it is the one tool here that leaves this network, and
the one whose answer was written by nobody in this household. It exists
because a fix that has never come up before still deserves an answer instead
of a shrug -- but every answer built from it says so in plain words, the same
rule nutrition.py and otobo_handoff.py already follow for anything that was
looked up rather than known: never let something from outside look as
trustworthy as something from in here.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

import httpx

from . import core, guides_book, kb, memory

# Two rounds. See the note above -- this is a person waiting, not a terminal.
MAX_ROUNDS = int(core._i("MOM_TOOL_ROUNDS", 2) if hasattr(core, "_i") else 2)

# Off turns search_online invisible to the model, not merely refused when
# called -- a model cannot reach for a tool it was never offered. Defaults
# on: this was added because the four local tools alone were not enough,
# and a household that decides otherwise can turn it off with one line in
# .env and a restart, same as MOM_AUTO_WAKE and everything else here.
ONLINE_SEARCH_ENABLED = core._b("MOM_ONLINE_SEARCH", "1")

# What each tool hands back is capped. A tool that returns four thousand words
# pushes the actual question out of the context window, and then the model
# answers the search results instead of her.
MAX_CHARS = 1500


# ---------------------------------------------------------------------------
#  The tools themselves
# ---------------------------------------------------------------------------
def _known_fixes(query: str = "", **_) -> str:
    """Problems already solved and written down, by you."""
    found = kb.search(query or "", limit=3)
    if not found:
        return "Nothing written down about that yet."
    out = []
    for a in found:
        steps = a.get("steps") or []
        out.append(
            f"{a.get('title', '')}\n{a.get('answer', '')}"
            + ("\n" + "\n".join(f"- {s}" for s in steps[:8]) if steps else "")
        )
    return "\n\n".join(out)[:MAX_CHARS]


def _past_help(query: str = "", **_) -> str:
    """What was done last time something like this came up."""
    hits = memory.search(query or "", kinds=("ticket", "faq"), k=3)
    if not hits:
        return "Nothing similar has come up before."
    lines = []
    for h in hits:
        # `body` and `label` are what memory.search returns. Reading a "text"
        # key it has never produced does not raise -- it quietly hands the
        # model an empty string, so the tool reported three hits with nothing
        # in them and the answer was built on air.
        body = (h.get("body") or "").strip().replace("\n", " ")
        label = (h.get("label") or h.get("kind") or "?").strip()
        lines.append(f"[{label}] {body[:400]}")
    return "\n".join(lines)[:MAX_CHARS]


def _guides(query: str = "", **_) -> str:
    """The handbook you wrote, as opposed to the quick fixes."""
    rows = guides_book.listing(query=query or "", limit=3)
    if not rows:
        return "No guide covers that."
    # listing() hands back sqlite3.Row, which indexes like a dict but has no
    # .get -- so the dict-style call raised AttributeError on every result and
    # the tool worked only when it found nothing. Subscripting is what Row
    # supports, and dict(r) is what makes the absent-column case answerable.
    out = []
    for row in rows:
        r = dict(row)
        out.append(f"{r.get('title', 'Untitled')}\n"
                   f"{(r.get('summary') or '')[:300]}")
    return "\n\n".join(out)[:MAX_CHARS]


def _devices(**_) -> str:
    """Whether the machine that answers is actually awake right now.

    The one tool that is about NOW rather than about what is written down. It
    matters because "the computer is slow" and "the computer is asleep" get
    very different answers, and only this can tell them apart.
    """
    try:
        st = core.backend_status()
    except Exception as e:  # noqa: BLE001
        return f"Could not check ({e.__class__.__name__})."
    return json.dumps(st)[:MAX_CHARS]


# DuckDuckGo's own HTML results page -- checked against the real, live page
# before writing this: `result__a` is the title link, `result__snippet` is
# the summary text. No API key exists for this; it is the one search DDG
# offers with no signup, which is the whole reason it was picked.
_DDG_URL = "https://html.duckduckgo.com/html/"
_DDG_RESULT = re.compile(
    r'class="result__a"[^>]*>(?P<title>.*?)</a>.*?'
    r'class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL)
_TAG = re.compile(r"<[^>]+>")


def _strip_tags(html: str) -> str:
    import html as _html
    return _html.unescape(_TAG.sub("", html)).strip()


def _online(query: str = "", **_) -> str:
    """THE LAST RESORT -- see the module docstring for why this one tool is
    different from the four above it. No API key: DuckDuckGo's own
    HTML-only results page, meant for exactly this kind of lightweight,
    unauthenticated fetch.
    """
    q = (query or "").strip()
    if not q:
        return "No search terms given."
    try:
        r = httpx.get(_DDG_URL, params={"q": q}, timeout=10,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; MamaHelp/1.0)"})
        r.raise_for_status()
    except Exception as e:  # noqa: BLE001
        return f"Could not reach the internet ({e.__class__.__name__})."
    hits = []
    for m in _DDG_RESULT.finditer(r.text):
        title = _strip_tags(m.group("title"))
        snippet = _strip_tags(m.group("snippet"))
        if title:
            hits.append(f"{title}\n{snippet}")
        if len(hits) >= 3:
            break
    if not hits:
        return "Nothing useful found online for that."
    # THE LABEL IS PART OF THE RETURN VALUE, NOT DECORATION. Whatever comes
    # back from this tool sits in the same context as four tools that are
    # all this household's own records -- without a plain marker the model
    # has no way to tell a DuckDuckGo snippet from something Jason wrote,
    # and neither will she.
    return ("[found on the open internet, not in anything this household "
           "has written down]\n\n" + "\n\n".join(hits))[:MAX_CHARS]


TOOLS: dict[str, Callable[..., str]] = {
    "search_known_fixes": _known_fixes,
    "search_past_help": _past_help,
    "search_guides": _guides,
    "check_this_machine": _devices,
}
if ONLINE_SEARCH_ENABLED:
    TOOLS["search_online"] = _online

SPEC: list[dict[str, Any]] = [
    {"type": "function", "function": {
        "name": "search_known_fixes",
        "description": "Search the problems already solved and written down "
                       "for this household. Use this FIRST for anything that "
                       "sounds like it has happened before.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string",
                      "description": "A few words describing the problem, in "
                                     "the way she would say it."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "search_past_help",
        "description": "Search past conversations and closed tickets for a "
                       "similar problem and what actually fixed it.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "search_guides",
        "description": "Search the written handbook of longer how-to guides.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "check_this_machine",
        "description": "Check whether the computer that answers is awake and "
                       "which models are loaded. Use only when the question is "
                       "about things being slow or not responding.",
        "parameters": {"type": "object", "properties": {}}}},
]
if ONLINE_SEARCH_ENABLED:
    SPEC.append({"type": "function", "function": {
        "name": "search_online",
        "description": "Search the open internet. LAST RESORT ONLY -- try "
                       "search_known_fixes, search_past_help and "
                       "search_guides first, in that order, and only reach "
                       "for this when all three came back empty. Say plainly "
                       "in your answer when something came from here rather "
                       "than from this household's own records.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}}, "required": ["query"]}}})


def run_tool(name: str, arguments: Any) -> str:
    """Call one tool. NEVER raises -- a broken tool must not lose the answer."""
    fn = TOOLS.get(name)
    if not fn:
        # The model asked for something that does not exist. Telling it so is
        # better than silence: it will usually pick a real one next round.
        return f"There is no tool called {name}."
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {"query": arguments}
    if not isinstance(arguments, dict):
        arguments = {}
    try:
        return str(fn(**arguments))
    except Exception as e:  # noqa: BLE001
        core.log("warn", "tools", f"{name} failed: {e}")
        return f"That lookup did not work ({e.__class__.__name__})."
