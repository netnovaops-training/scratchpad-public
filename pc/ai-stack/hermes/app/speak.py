"""Say the same thing the way each person actually reads.

JASON, 2026-09-24, handing over his Master Communication Key: one summary,
rewritten per person -- Robin, Mom, Jordan, Z, Brandon, and himself.

THE RULES BELOW ARE HIS, NOT MINE. Each profile is transcribed from that
document: the sentence limits, the blacklists, the things to ask about. I
have not added a person, softened a limit, or invented a preference. Where
the document is specific -- "never exceed two sentences", "compress
non-technical updates into exactly one brief sentence" -- the prompt says so
in those words, because a rule stated vaguely is a rule a model rounds off.

TEMPERATURE ZERO ON EVERY PROFILE. These are reports of things that actually
happened, being handed to somebody who will act on them. An invented detail
is a false statement about somebody's real week. Sampling buys variety;
variety is worth nothing here and costs accuracy.

WHAT IS DELIBERATELY NOT HERE. The same document describes mood scaling and
crisis-keyword protocols -- watching messages for "hopeless" or "giving up"
and intervening. That is not implemented, and nothing in this file should be
read as implementing it. Building distress detection into a chat assistant
quietly, without it having been asked for and without Jason seeing exactly
how it behaves, would be worse than not building it. It is written down as
something to decide on deliberately, not something to inherit from a file.
"""
from __future__ import annotations

import json
import os
import urllib.request


def _http_only(target):
    """Return target (a URL string or urllib Request) if its scheme is http or https, else raise ValueError.

    urlopen also follows file:// and custom schemes (Bandit B310). Every caller here talks to an
    http(s) service, so anything else is a mistake or a planted URL. Added 2026-11-19.
    """
    url = str(getattr(target, "full_url", target))
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url[:80]}")
    return target


# THE SAME GATEWAY THE REST OF HERMES USES, read from the same two
# variables. My first version invented LLM_URL and LITELLM_MASTER_KEY, which
# are not set in this container -- every call came back HTTPError. Reusing
# ROUTER_BASE_URL / ROUTER_API_KEY means this cannot drift away from the
# router beside it.
BASE_URL = os.environ.get("ROUTER_BASE_URL", "http://litellm:4000/v1")
GATEWAY = BASE_URL.rstrip("/") + "/chat/completions"
KEY = os.environ.get("ROUTER_API_KEY", "sk-none")
# "fast" is a job name the gateway routes; DEFAULT_JOB is what the rest of
# the service asks for, so a summary lands on the same model as a chat turn
# unless it is overridden.
MODEL = os.environ.get("SUMMARY_MODEL", os.environ.get("DEFAULT_JOB", "chat"))
TIMEOUT = 120

PROFILES = {
    "robin": """Rewrite this for ROBIN.

He reads for what to DO. Give him the point and the next step, not the
reasoning that got there. He is well served by structure and poorly served
by walls of prose.

  - Lead with what changed or what is ready. One sentence.
  - Then at most five bullets, one thing each, in plain words.
  - If there is something for him to do, put it last under "For you:".
    If there is nothing, say nothing. Do not invent a task.
  - No tool names, server names, file names or version numbers. Say "the
    garden website", not the name of the software behind it.""",

    "mom": """Rewrite this for MOM (Davetta).

The goal is warmth and calm, not information transfer.

  - NEVER MORE THAN TWO SENTENCES. This is a hard limit.
  - One topic only. If the text covers several, pick the single best one
    and drop the rest entirely.
  - Say what the thing IS or DOES, never how it works. Omit all mechanics.
  - Share any accomplishment humbly and briefly.
  - Do NOT mention money, costs, financial pressure, stress of any kind, or
    anything difficult from the past. If the text is mostly about those,
    say something small and warm instead and leave the subject alone.
  - End with one positive emoji.""",

    "jordan": """Rewrite this for JORDAN.

  - Short natural paragraphs that read like a relaxed text from a friend.
    No bullet lists.
  - Keep it upbeat and easy. Explain anything complex inside the story
    rather than as a definition.
  - Share the update briefly, then turn the conversation back to him: end
    with exactly ONE gentle question about how he is doing or about his
    dog, Cinnamon.
  - Do not centre it on what the writer needs. No negativity about anyone.""",

    "z": """Rewrite this for Z (his sister).

Her time is short and she values directness.

  - THREE TO FOUR SENTENCES. Hard limit.
  - No complaining, venting, or excuses. Frame everything around what was
    done and what happens next.
  - Anchor it to progress and goals.
  - Anything technical gets ONE broad sentence -- she does not need the
    detail. (Healthcare subjects are the exception and can be precise.)""",

    "brandon": """Rewrite this for BRANDON.

  - AROUND FOUR TO FIVE SENTENCES in total.
  - Technical content can be genuinely high level -- he is a peer on AI and
    computing. Give him the whole idea and why it is shaped that way, not
    just the mechanics.
  - Compress anything NON-technical into exactly one brief sentence.
  - Connect the detail back to the bigger picture so the point is not lost.
  - End by asking about him: his family, how he is doing, or his work.""",

    "jason": """Rewrite this for JASON himself.

  - High technical detail. Name the actual things: services, files, error
    messages, exact numbers.
  - Sequential: what happened, in order.
  - Keep each topic separate rather than blending them.
  - Say plainly what is unfinished or unverified. Do not smooth it over.""",
}

KOREAN = ("WRITE IT IN KOREAN. Keep it plain and natural. Leave web "
          "addresses, app names and proper names in their original form -- "
          "those are the parts that have to be typed or tapped.")

BASE = """{profile}

ONLY USE WHAT IS IN THE TEXT BELOW. If it does not say something, leave it
out. Do not guess, do not round numbers up, and do not call anything
finished unless the text says it finished. Saying less is correct.

{lang}

TEXT:
---
{text}
---

Write only the message. No preamble, no explanation of what you did."""


def profiles():
    return sorted(PROFILES)


def rewrite(text, who="robin", korean=False):
    """`text` in one person's register. Returns (message, problem)."""
    body = (text or "").strip()
    if not body:
        return "", "there was nothing to summarise"
    who = (who or "robin").strip().lower()
    if who not in PROFILES:
        return "", (f"no profile for {who!r}. There is one for: "
                    + ", ".join(profiles()))

    payload = json.dumps({
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 700,
        "messages": [{"role": "user", "content": BASE.format(
            profile=PROFILES[who],
            lang=KOREAN if korean else "Write it in English.",
            text=body[:12000])}],
    }).encode()

    req = urllib.request.Request(GATEWAY, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    if KEY:
        req.add_header("Authorization", "Bearer " + KEY)
    try:
        with urllib.request.urlopen(_http_only(req), timeout=TIMEOUT) as r:  # nosec B310
            out = json.loads(r.read())
        return out["choices"][0]["message"]["content"].strip(), ""
    except Exception as e:  # noqa: BLE001
        return "", f"the model did not answer: {type(e).__name__}"


def summarise_for(text, who="robin", korean=False):
    """The tool entry point: one person's register, plus a usable reply.

    RETURNS THE MESSAGE READY TO SEND, and says so, because the thing Jason
    asked for is something he can forward -- not a description of what a
    summary would contain.
    """
    msg, why = rewrite(text, who=who, korean=korean)
    if why:
        return f"Could not write that summary: {why}"
    return (msg + chr(10) * 2
            + f"[This is written for {who}. Give it to them exactly as it is "
              "-- do not add to it, expand it, or explain it. It is already "
              "in the shape they read best.]")
