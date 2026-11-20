"""Configuration, and the two guards that matter.

WHAT THIS APP IS
----------------
Mealie next door is the library: it stores recipes, shows them, scales them.
This app is the front door for the messy part -- you have a LINK, and the link
is a food blog with eleven paragraphs about someone's grandmother before it
gets to the flour. It fetches the page, gets a recipe out of it, turns that
into a numbered guide with pictures, and hands the result to Mealie.

Everything here talks to LiteLLM at http://litellm:4000, which means the model
answering is whatever `config.yaml` currently points the job name at. Swap the
model there and this app changes with it, without a rebuild. Same deal as
Hermes and the Telegram bridge.

THE TWO GUARDS
--------------
1. FETCH_TIMEOUT / MAX_FETCH_BYTES. A recipe page is maybe 300KB. Without a
   ceiling, pasting a link to a 2GB ISO makes this container read it into
   memory until Docker kills the process -- and the failure looks like "the
   recipe importer is broken", not "you pasted the wrong link".

2. is_public_url(). READ THE COMMENT ON IT. This app fetches URLs a human
   types, from inside the Docker network, where http://litellm:4000 and
   http://ollama:11434 are reachable and the outside world is not. That is the
   textbook shape of SSRF. It is behind forward_auth so the human is signed in,
   but "signed in" is not "should be able to read the LiteLLM admin API by
   pasting a link", so the check is here regardless.
"""
import ipaddress
import os as os          # re-exported: photos.py reads KITCHEN_MAX_PHOTO_MB
                         # through core so every env name this app understands
                         # is reachable from one module.
import socket
from pathlib import Path
from urllib.parse import urlparse

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.getenv("KITCHEN_DATA_DIR", "/data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = DATA_DIR / "kitchen.db"

# Where this app is mounted. Caddy strips /kitchen before proxying, so without
# this every link it writes would point at the site root. Same lesson the
# booking app learned the hard way -- see BOOK_ROOT_PATH in its core.py.
ROOT_PATH = os.getenv("KITCHEN_ROOT_PATH", "").rstrip("/")

BUSINESS = os.getenv("KITCHEN_TITLE", "The Kitchen")
TAGLINE = os.getenv("KITCHEN_TAGLINE", "Paste a link. Get a recipe you can actually follow.")

# --- the model router -------------------------------------------------------
LLM_BASE = os.getenv("KITCHEN_LLM_BASE", "http://litellm:4000/v1").rstrip("/")
LLM_KEY = os.getenv("KITCHEN_LLM_KEY", "") or os.getenv("LITELLM_MASTER_KEY", "")

# JOB NAMES, not model names. `long-context` because a food blog is long and
# the whole point of reading one is that the recipe is buried in the middle;
# a 8k window would silently truncate the page before the method.
LLM_READ = os.getenv("KITCHEN_LLM_READ", "long-context")
# `chat` for the short, creative asks -- substitutions, ideas. Fast and it
# does not need the page in front of it.
LLM_TALK = os.getenv("KITCHEN_LLM_TALK", "chat")

# SHORT, STRUCTURED ANSWERS. Labels, and anything else that is really
# classification rather than conversation.
#
# `fast` (llama3.2:3b) ON PURPOSE, and not because it is cheap. The `chat` job
# points at qwen3:8b, which is a THINKING model: asked for a JSON object it
# spends its budget reasoning and returns an EMPTY string. Measured on this
# machine, same prompt:
#
#     fast   max=200    1.2s   {"labels":["breakfast classic"]}
#     chat   max=200    3.5s   ''
#     chat   max=900   13.4s   ''
#
# So this is not a quality trade. The small model gives a right answer in a
# second and the big one gives nothing at all. Point this at a bigger model
# only if you have checked it returns content rather than thinking.
LLM_LABEL = os.getenv("KITCHEN_LLM_LABEL", "fast")

# Real photo/image understanding -- Jason's own reference pictures, a step
# photo, the recipe's banner. OWNER-ONLY: this points at a real vision model
# (qwen3-vl:8b, ~6GB) that does not stay loaded alongside everything else on
# a 12GB card, so calling it competes with the household's other features
# unless it is explicitly switched on first -- see art.py's vision_mode
# on()/off() and main.py's owner-gated toggle route.
LLM_VISION = os.getenv("KITCHEN_LLM_VISION", "vision")
LLM_TIMEOUT = float(os.getenv("KITCHEN_LLM_TIMEOUT", "180"))

# --- the library next door --------------------------------------------------
MEALIE_URL = os.getenv("KITCHEN_MEALIE_URL", "http://mealie:9000").rstrip("/")
MEALIE_TOKEN = os.getenv("KITCHEN_MEALIE_TOKEN", "")
# The public address you reach Mealie on, for the "open in Mealie" links. It is
# NOT MEALIE_URL: that one is a Docker hostname your browser cannot resolve.
MEALIE_PUBLIC = os.getenv("KITCHEN_MEALIE_PUBLIC", "").rstrip("/")
# This app's OWN public address -- for the rare link meant to leave the page
# it was generated on (a recipe request link, texted to Mama outside the
# app entirely). Every other link in this app is relative (ROOT_PATH), which
# is right for a link someone clicks WHILE already on the site; this one is
# for a link that has to work standing alone in a text message. Same
# default mealie-skin already uses for the identical idea from its side.
PUBLIC_URL = os.getenv("KITCHEN_PUBLIC_URL",
                       "https://example.org/kitchen").rstrip("/")

# The site's own root, for the ONE kind of link this app hands to somebody
# who is not signed in at all -- a share link (see store.py's `shares` table
# and main.py's /share/{token}). PUBLIC_URL above is not it: that one still
# points inside /kitchen, which is behind forward_auth, so a stranger
# clicking it would just be bounced to a sign-in page they have no account
# for. /share/* has no forward_auth in front of it in the Caddyfile at all --
# see that block's own comment for why that is safe.
SITE_ROOT = os.getenv("KITCHEN_SITE_ROOT", "https://example.org").rstrip("/")

# Server-to-server only, over the internal Docker network -- checked before
# anything else on /internal/*. Same shape as kb's KB_TICKET_SECRET: the
# route is never proxied through Caddy's public /kitchen/* path (nothing
# maps this container's port to the host either), so this is a second lock
# on top of "unreachable from the internet" rather than the only one.
INTERNAL_SECRET = os.getenv("KITCHEN_INTERNAL_SECRET", "")

# How long a handed-over Mealie session lasts. MATCH MEALIE'S OWN `tokenTime`,
# which it publishes at /api/app/about and which defaults to 48 hours. Setting
# this longer than Mealie's does not extend anything -- it just leaves a dead
# token in a live cookie, which shows up as a library that half-loads rather
# than as a sign-in prompt, and that is a much harder thing to recognise.
MEALIE_TOKEN_HOURS = int(os.getenv("KITCHEN_MEALIE_TOKEN_HOURS", "48"))

# Off only to test by hitting the container directly over http. Caddy
# terminates TLS in front of this, so true is correct in every real setup.
COOKIE_SECURE = os.getenv("KITCHEN_COOKIE_SECURE", "true").lower() != "false"

# --- Tandoor, the library's replacement ------------------------------------
# Same shape as the Mealie block above: TANDOOR_URL is the Docker hostname
# this app talks to server-to-server, TANDOOR_PUBLIC is the address a
# person's own browser is sent to.
TANDOOR_URL = os.getenv("KITCHEN_TANDOOR_URL", "http://tandoor:80").rstrip("/")
TANDOOR_PUBLIC = os.getenv("KITCHEN_TANDOOR_PUBLIC", "").rstrip("/")

# The one admin account, made through Tandoor's own one-time setup wizard.
# Used only to log Jason himself in AND, once, to hand-provision Mama's and
# Casey's accounts (Tandoor's public sign-up is closed by default and stays
# that way -- see handoff_tandoor.py's own docstring for why household
# members are provisioned ahead of time rather than on first visit).
TANDOOR_ADMIN_USER = os.getenv("KITCHEN_TANDOOR_ADMIN_USER", "")
TANDOOR_ADMIN_PASSWORD = os.getenv("KITCHEN_TANDOOR_ADMIN_PASSWORD", "")

# Django's own SESSION_COOKIE_AGE, checked live against the running
# container (31536000s, one year -- Tandoor's own default). Matched here for
# the same reason MEALIE_TOKEN_HOURS matches Mealie's tokenTime: setting
# this longer than the real session lives just leaves a dead cookie behind.
TANDOOR_SESSION_SECONDS = int(os.getenv("KITCHEN_TANDOOR_SESSION_SECONDS", "31536000"))

# --- fetching ---------------------------------------------------------------
FETCH_TIMEOUT = float(os.getenv("KITCHEN_FETCH_TIMEOUT", "25"))
MAX_FETCH_BYTES = int(os.getenv("KITCHEN_MAX_FETCH_BYTES", str(4 * 1024 * 1024)))

# WHY THIS LOOKS LIKE CHROME RATHER THAN SAYING WHAT IT IS
#
# It used to be honest -- "JasonsKitchen/1.0" with a link back here -- and that
# is the nicer thing to send. It also does not work. Measured on food52, same
# page, same second:
#
#     the honest header set    429 Too Many Requests
#     a browser's header set   200 OK
#
# The 429 is not about load. It is a bot filter that scores the whole request:
# a missing Sec-Fetch-Mode, no Accept-Language, an Accept header no browser
# sends. One page fetched because a person pasted a link is exactly the traffic
# these filters exist to allow, and it was being turned away for having said so.
#
# This is not a scraper. It fetches one page when somebody asks for one, obeys
# the size cap in MAX_FETCH_BYTES, and never crawls a site on its own. Sending
# the headers a browser sends is describing that accurately.
USER_AGENT = os.getenv(
    "KITCHEN_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/203.0.113.2 Safari/537.36",
)

# The rest of what a real navigation sends. A filter looks at the SET, not just
# the User-Agent -- a Chrome UA with none of these is a more obvious bot than
# an honest one.
BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-CH-UA": '"Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-CH-UA-Mobile": "?0",
    "Sec-CH-UA-Platform": '"Windows"',
}


class Blocked(Exception):
    """A URL we will not fetch. The message is shown to the user verbatim."""


def is_public_url(url: str) -> str:
    """Return the URL if it is safe to fetch, else raise Blocked.

    WHY EVERY LINE OF THIS EXISTS
    -----------------------------
    This container sits on the Docker network. From here, `http://litellm:4000`
    is the router's admin API, `http://ollama:11434` will pull and delete
    models, `http://admin:8011` is the site admin, and `http://169.254.169.254`
    is the cloud metadata endpoint on anything that is not this PC. All of them
    are one pasted link away from being fetched BY THIS APP, with this app's
    network position, if nothing checks.

    So: resolve the hostname first, then test the ADDRESS. Testing the string
    is not enough -- `http://recipes.example.com` can resolve to 127.0.0.1 and
    a blocklist of names never sees it coming.

    This is not airtight. A DNS record that answers publicly on the first
    lookup and privately on the second (DNS rebinding) beats it, because httpx
    resolves again when it connects. Closing that means pinning the resolved
    address into the connection, which is a lot of machinery for a form only
    signed-in people can reach. This stops the accident and the casual probe,
    which is what it is for.
    """
    p = urlparse(url.strip())
    if p.scheme not in ("http", "https"):
        raise Blocked("That needs to be an http:// or https:// link.")
    if not p.hostname:
        raise Blocked("I could not find a website address in that.")

    try:
        infos = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except socket.gaierror:
        raise Blocked(f"No such website: {p.hostname}")

    for info in infos:
        addr = ipaddress.ip_address(info[4][0])
        if (addr.is_private or addr.is_loopback or addr.is_link_local
                or addr.is_reserved or addr.is_multicast or addr.is_unspecified):
            raise Blocked(
                f"{p.hostname} points inside this network, so I will not fetch it. "
                "Links to recipes on the internet only."
            )
    return url.strip()
