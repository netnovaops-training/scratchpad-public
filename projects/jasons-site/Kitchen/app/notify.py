"""Telling Jason something happened, without Kitchen knowing what Telegram
is. Same shape as ai-stack/kb/notify.py -- see that file's own docstring
for the full reasoning (this app has no business holding a bot token,
and "quiet hours"/wording changes should never need a Kitchen rebuild).
n8n decides what a notification actually becomes; Kitchen only ever says
what happened and hands over a link.

Jason's own requests (2026-08-25):
- "give me a /llm-findings page, I would like a notice when it's done
  and a link on from telegram."
- "when I get a user who is not me logged on and using my page can
  telegram send me an alert when they are accessing my pages."
- "when a new recipe is sent I want a notification if it's not made by
  me on what recipe it is so I know to get images."

FAILING IS NOT AN ERROR -- same rule as kb/notify.py. Every call here is
best-effort and swallowed; a page render or a Keep must never fail
because a notification could not be sent.
"""
import logging
import os
import threading
import time

import httpx

log = logging.getLogger("kitchen.notify")

# Empty means notifications are off -- the correct default, same as
# kb.notify.WEBHOOK: a stack with no n8n workflow set up for this should
# not log a failed request on every page view.
WEBHOOK = os.getenv("KITCHEN_WEBHOOK_URL", "").strip()
TIMEOUT = float(os.getenv("KITCHEN_WEBHOOK_TIMEOUT", "6"))
PUBLIC = os.getenv("KITCHEN_PUBLIC_URL", "https://example.org").rstrip("/")


def _send(event: str, text: str, url: str = "") -> None:
    """Fire-and-forget, on its own thread -- NOT kb/notify.py's own
    plain synchronous httpx.post(). This is called from _page() in
    main.py, a SYNC helper invoked directly (not awaited) from inside
    async route handlers -- a blocking network call there would stall
    Kitchen's whole event loop, every concurrent request, for up to
    TIMEOUT seconds, for a message nobody in the request is waiting on.
    A daemon thread means a slow or hung webhook costs nothing but
    itself."""
    if not WEBHOOK:
        return
    payload = {"event": event, "text": text, "url": url}

    def _post():
        try:
            httpx.post(WEBHOOK, json=payload, timeout=TIMEOUT)
        except Exception as e:  # noqa: BLE001
            log.warning("could not reach the webhook (%s): %s", WEBHOOK, e)

    threading.Thread(target=_post, daemon=True).start()


def dedupe_done(tallies: list) -> None:
    """The LLM food/tool matching pass finished. `tallies` is a list of
    dedupe.run()'s own return dicts, one per kind."""
    lines = [f"{t['kind']}: {t['tier1_merged']} auto-merged, "
            f"{t['tier2_queued']} queued for review" for t in tallies]
    _send("dedupe.done", "LLM matching finished.\n" + "\n".join(lines),
         f"{PUBLIC}/admin/llm-findings")


# Cooldown so a household member clicking around does not page Jason once
# per click -- Jason's own words were "logged on and using my page", not
# "every page they load". In-memory only (resets on a restart, which just
# means the next visit after a deploy alerts again -- harmless).
_VISITOR_COOLDOWN = float(os.getenv("KITCHEN_VISITOR_COOLDOWN", str(30 * 60)))
_last_seen: dict = {}


def visitor(email: str, name: str, page: str) -> None:
    """Someone who is not Jason is using the site."""
    now = time.time()
    if now - _last_seen.get(email, 0) < _VISITOR_COOLDOWN:
        return
    _last_seen[email] = now
    _send("visitor", f"{name} is on {page}", f"{PUBLIC}{page}")


def new_recipe(title: str, by_name: str, path: str) -> None:
    """A recipe was kept by someone who is not Jason -- his own words,
    "so I know to get images." `path` is site-RELATIVE (e.g.
    "/kitchen/r/abc123"), same as visitor()'s own `page` -- turned into a
    real clickable link here, not left to the caller to remember, since
    a Telegram message with a relative link is not a link at all off
    this LAN."""
    _send("recipe.new", f"New recipe from {by_name}: {title}", f"{PUBLIC}{path}")
