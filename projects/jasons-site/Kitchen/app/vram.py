"""The vision-model toggle, and the bigger recipe/KB-parsing model's
automatic evict-and-restore. Jason's own words: "give me the option to
turn it on and off when I am done with image generation" (vision, manual)
-- and separately, for parsing: "it just needs to run only when parsing
the recipe and the kbs... we can evict it on the fly" (automatic, no
switch to remember).

WHY THIS EXISTS SEPARATELY FROM art.py's room()/release()
-----------------------------------------------------------
Those manage the SEPARATE `imagine` microservice's own GPU budget (the
txt2img drawing model). This manages Ollama's -- a completely different
process on the same card, holding the household's everyday chat/coding
models (fast, chat, reasoning, ...) resident. A real vision model
(qwen3-vl:8b, ~6GB) does not fit alongside those on a 12GB card with any
real headroom, so using it well means explicitly clearing the deck first,
not hoping Ollama's own LRU eviction sorts it out mid-request.

THE MECHANISM, CONFIRMED LIVE (2026-08-24)
--------------------------------------------
Ollama's own /api/generate accepts `keep_alive: 0`, which unloads that
model from VRAM immediately -- verified directly: evicted a resident
model, confirmed it left `/api/ps`, reloaded it back. This is not
documented behavior this file is guessing at; it was tested against the
real, running Ollama container before being relied on here.

OWNER-ONLY, ON PURPOSE
------------------------
Jason's own words: "I only have me access to that." Every route that
calls into this module gates on _is_owner() in main.py -- nothing here
gates itself, the same way art.py's draw() trusts its caller. This module
is a mechanism, not a policy.
"""
import asyncio
import contextvars
from contextlib import asynccontextmanager

import httpx

from . import core

OLLAMA = core.os.getenv("KITCHEN_OLLAMA_URL", "http://ollama:11434").rstrip("/")

# The household's everyday models, warmed back up when vision mode turns
# off -- so "I'm done with image generation" actually restores instant
# responses elsewhere, not just frees VRAM and leaves everything else cold
# for the next person who asks Hermes something. Matches litellm/config.yaml's
# own real model names, not the job names (Ollama's /api/generate wants the
# actual model, not a LiteLLM job).
_EVERYDAY = ["llama3.2:3b", "qwen3:8b"]
VISION_MODEL = "qwen3-vl:8b"

# The bigger recipe/KB-parsing model. WAS qwen2.5:14b, now Phi-4 -- Jason's
# own choice after comparing both live (2026-08-24): Phi-4 confirmed
# JSON-reliable (clean parse on the first try, no markdown fences, no
# prose) and 100% GPU at this size, same as qwen2.5:14b was. qwen2.5:14b
# itself was NOT deleted -- it's its own job now (`big-context` in
# litellm/config.yaml), kept available per Jason's own words ("keep
# qwen... it has other uses in my codebase"), just no longer what THIS
# job calls. Whichever model sits here, the one hard constraint stays:
# it must return STRICT JSON reliably (chat_json, not free text) --
# crawl.py's own hard-won comment on this job explains why qwen3 was
# ruled out entirely (a THINKING model that burns its budget reasoning
# and returns nothing parseable when asked for JSON); anything considered
# for this slot needs the same live JSON-reliability check Phi-4 and
# qwen2.5:14b both already passed, not assumed from a bigger parameter
# count alone.
PARSE_MODEL = "phi4"


class VramError(Exception):
    """Ollama would not do what was asked. Shown to Jason, nobody else."""


async def _unload(model: str) -> bool:
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(f"{OLLAMA}/api/generate",
                                  json={"model": model, "keep_alive": 0})
        return r.status_code == 200
    except httpx.RequestError:
        return False


async def _warm(model: str) -> bool:
    """Load a model and leave it resident (no keep_alive -- Ollama's own
    default, matching how the household's everyday models already sit
    loaded the rest of the time)."""
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(f"{OLLAMA}/api/generate",
                                  json={"model": model, "prompt": "", "stream": False})
        return r.status_code == 200
    except httpx.RequestError:
        return False


async def free() -> list:
    """Unload everything Ollama currently has resident -- same effect as
    the admin panel's own "Free the GPU for drawing" button (its Rust
    code calls Ollama directly, the same /api/generate keep_alive=0
    trick, just from a different process). Jason's own request
    (2026-08-25): "like in /kitchen when I can generate a photo for step
    by step... can I do this for a recipe in /recipe" -- a step-photo
    draw request reaching Kitchen straight from recipe-app has no admin
    page in front of it to click "free the GPU" first, so this makes the
    same room itself before handing off to imagine. Returns the names
    actually unloaded, for a caller that wants to report what happened;
    never raises -- a failed unload just means imagine's own room check
    reports "still busy," not a broken request."""
    out = []
    for name in await loaded():
        if await _unload(name):
            out.append(name)
    return out


async def loaded() -> list:
    """What Ollama actually has resident right now, for the toggle's own
    status line -- never guessed at, always the real /api/ps answer."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{OLLAMA}/api/ps")
        if r.status_code != 200:
            return []
        return [m.get("name", "") for m in r.json().get("models") or []]
    except httpx.RequestError:
        return []


async def vision_on() -> bool:
    """Clear the deck for the vision model: evict the everyday models,
    then warm the vision model so the FIRST real request is not also
    the one paying its ~load time. Best-effort on the evictions -- a
    model that was not loaded anyway "fails" to unload harmlessly."""
    for m in await loaded():
        await _unload(m)
    return await _warm(VISION_MODEL)


async def vision_off() -> bool:
    """Free the vision model's VRAM back, and warm the everyday model
    back up -- see the module docstring for why this is not just an
    unload."""
    await _unload(VISION_MODEL)
    return await _warm(_EVERYDAY[0])


# REENTRANCY DEPTH, not a bool -- a real recipe import calls the parse job
# TWICE in a row (importer.py's main extraction, then
# crawl.fill_ingredients_from_steps' own follow-up call), and each has its
# own `async with vram.parsing()`. Without this, the inner one would evict
# PARSE_MODEL and restore the household models right before the outer one
# needed it again -- correct, but a wasted reload in the middle of one
# logical import. Only the OUTERMOST context actually evicts on entry and
# restores on exit; anything nested just rides along.
#
# contextvars.ContextVar, NOT A PLAIN MODULE GLOBAL -- CAUGHT WHILE
# ADDING _lock BELOW: a bare `int` here is shared by every concurrent
# caller, not just nested calls within ONE logical operation. Two
# genuinely unrelated imports landing at the same moment would each
# bump the SAME counter -- the second one to run could see depth > 0
# (left behind by the first) and wrongly conclude it's "nested," skipping
# _lock entirely and racing in without ever waiting its turn. A
# ContextVar gives each asyncio Task its own independent view (and still
# threads correctly through real nested awaits inside one task), so
# "nested within my own operation" and "a different operation is
# running concurrently" are no longer the same bit of shared state.
_parse_depth: "contextvars.ContextVar[int]" = contextvars.ContextVar("vram_parse_depth", default=0)

# CAUGHT LIVE (2026-08-25): a real dedupe run opened dozens of SEPARATE
# vram.parsing() contexts, one per core-word bucket, each one "outermost"
# on its own -- Phi-4 (14B) got evicted and reloaded on every single
# bucket instead of once for the whole pass (fixed at the call site,
# dedupe.py, by wrapping its own loop in one outer context). This lock
# is the SAME fix at the root: two genuinely UNRELATED callers (a recipe
# import landing while a dedupe pass is mid-flight, say) racing for
# vram.parsing() concurrently had no coordination at all before this --
# whichever finished first would evict PARSE_MODEL out from under the
# other, mid-call. Only the outermost entry in each logical operation
# acquires it (see `outermost` below), so nesting within ONE operation
# still costs nothing; it is unrelated CONCURRENT operations that now
# wait their turn instead of fighting. Jason's own request (2026-08-25):
# "can my phi be used if another one is not chatting... otherwise let
# the user know it's waiting to be imported" -- see busy() below for the
# non-blocking check callers use to say so before they start waiting.
_lock = asyncio.Lock()


def busy() -> bool:
    """True if something is already using PARSE_MODEL right now -- a
    caller about to await vram.parsing() can check this FIRST to tell
    whoever's waiting "something else has the model, hang on" instead of
    just going quiet until it's their turn. Never blocks."""
    return _lock.locked()


@asynccontextmanager
async def parsing():
    """Wraps ONE parsing call (a recipe import, a KB fill-in pass): evicts
    whatever the household's everyday models are using, so PARSE_MODEL gets
    the card to itself the way a 14B model on a 12GB card actually needs to
    -- then evicts PARSE_MODEL and restores the everyday model again
    afterward, automatically, no switch to remember. Safe to nest -- see
    `_parse_depth` above.

    Used as: `async with vram.parsing(): ...the actual llm.chat_json call...`

    NOT warmed ahead of time the way vision_on() warms the vision model --
    a parse is a one-off, isolated call (unlike a batch of image draws),
    so there is no "first request" to spare the load time for. The
    eviction still happens up front, before the call, so PARSE_MODEL has
    the full card the moment it starts loading rather than fighting the
    everyday models for VRAM mid-load.

    Best-effort throughout, same as vision_on()/off(): a slow or failed
    evict/restore never blocks the parse itself, which runs regardless --
    see the `finally`.
    """
    depth = _parse_depth.get()
    outermost = depth == 0
    if outermost:
        # Waits here if someone else's operation already holds it --
        # this IS the "otherwise let the user know it's waiting" case
        # from the caller's side; see busy() above for the non-blocking
        # check a caller can make first to say so before it starts
        # waiting silently.
        await _lock.acquire()
    token = _parse_depth.set(depth + 1)
    if outermost:
        for m in await loaded():
            if m != PARSE_MODEL:
                await _unload(m)
    try:
        yield
    finally:
        _parse_depth.reset(token)
        if outermost:
            await _unload(PARSE_MODEL)
            await _warm(_EVERYDAY[0])
            _lock.release()
