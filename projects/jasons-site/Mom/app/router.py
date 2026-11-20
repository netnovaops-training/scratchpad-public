"""Model router — decides which local model (or models) answers each request.

THE IDEA
--------
Not every question needs the same model, and not every model is good at the same
thing. A photo of a router's blinking lights needs eyes. A one-word category
label needs speed, not intelligence. A scam text message needs the most careful
reasoning available. Sending all three to one 14B model is wasteful in two
directions at once: too slow for the trivial jobs, and blind for the visual one.

So every request is classified by CODE (never by the model -- see below), routed
to one or more specialists, and every decision is written down so the dashboard
can show you exactly what happened and why.

WHY CODE PICKS THE ROUTE, NOT A MODEL
-------------------------------------
The obvious design is to let the model decide which tool to call. It is also the
design that quietly loses records. A 12B model that forgets to emit a
close_ticket() call does not raise an error -- it produces a friendly paragraph
and the ticket stays open forever, and you only find out weeks later when you go
looking. Routing is a decision with consequences, so routing is Python. The
models answer questions; they do not run the system.

CAPACITY IS THE WHOLE DESIGN CONSTRAINT
---------------------------------------
One RTX 4070 is 12 GB. Whisper holds ~0.9 GB of that permanently and Windows
takes ~0.7 GB, which leaves about 10.4 GB for language models INCLUDING their
KV caches. That budget is what decides whether "more than one model helps at
once" costs nothing or costs 25 seconds:

    RESIDENT   several small models loaded together, all instant, no swapping.
               Requires the total to stay under budget.
    SWAPPED    one large model plus others loaded on demand. Better answers on
               the common path, a pause on the uncommon one.

Set MOM_MODEL_PROFILE to pick. Nothing else in the app changes -- the profile
only changes which names this module hands to Ollama, so switching is one line
in .env and a restart, not a rewrite.

ENSEMBLE
--------
When more than one specialist can genuinely contribute, they run in sequence and
their output is merged by a final pass. This is not "ask three models and pick
the longest". Each one gets a DIFFERENT job:

    the vision model     describes what is actually in her photo
    the reasoning model  works out what to do about it
    the safety screen    is not a model at all, it is safety.py, and it runs
                         before and after regardless

Ensembles are used only where a second perspective adds something. Running two
general models on the same question and merging them mostly produces longer,
blander answers, and on this hardware it doubles the wait for the privilege.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import httpx

from . import core

PROFILE = os.getenv("MOM_MODEL_PROFILE", "resident").strip().lower()

# -1 pins models in VRAM until Ollama exits. Correct for the resident profile:
# the desktop hibernates between her questions anyway, so "forever" really
# means "until it next goes to sleep".
_KEEP_ALIVE_RAW = os.getenv("MOM_OLLAMA_KEEP_ALIVE", "-1")


def _keep_alive():
    """Ollama parses keep_alive as a Go duration.

    A STRING "-1" fails with `missing unit in duration` and the request comes
    back 400 Bad Request, which reads exactly like a broken model. As a NUMBER,
    -1 means "stay loaded forever" -- which is the whole point of the resident
    profile. Duration strings such as "30m" are valid and pass through as-is.
    """
    v = (_KEEP_ALIVE_RAW or "").strip()
    try:
        return int(v)
    except (TypeError, ValueError):
        return v or "5m"


KEEP_ALIVE = _keep_alive()

# --- the roles the app knows how to fill -------------------------------------
# Names are overridable individually, so you can swap one specialist without
# touching the others.
def _m(env: str, default: str) -> str:
    return os.getenv(env, "").strip() or default


PROFILES = {
    # One strong text model. Vision loads on demand and evicts it, costing a
    # pause on image turns only. Best answers on the 95% path.
    "swapped": {
        "reason": _m("MOM_MODEL_REASON", "hermes-mom"),
        "quick":  _m("MOM_MODEL_QUICK",  "hermes-mom"),
        "vision": _m("MOM_MODEL_VISION", "qwen3-vl:4b"),
        "embed":  _m("MOM_MODEL_EMBED",  "embeddinggemma:300m"),
    },
    # Everything co-resident. Nothing ever swaps, every specialist is instant,
    # and several can genuinely run together. Text reasoning is a step down.
    "resident": {
        "reason": _m("MOM_MODEL_REASON", "hermes3:8b"),
        "quick":  _m("MOM_MODEL_QUICK",  "qwen3-vl:2b"),
        "vision": _m("MOM_MODEL_VISION", "qwen3-vl:2b"),
        "embed":  _m("MOM_MODEL_EMBED",  "embeddinggemma:300m"),
    },
    # THROUGH THE ROUTER, by job name. Used when MOM_ROUTER_URL is set.
    #
    # These are not models, they are jobs -- what answers each one is decided
    # in litellm/config.yaml, in one place, for every app on the machine. That
    # is what lets /mama reach anything the router gains, including the cloud
    # escalation, without this file changing again.
    "router": {
        "reason": _m("MOM_JOB_REASON", "mama"),
        "quick":  _m("MOM_JOB_QUICK",  "fast"),
        "vision": _m("MOM_JOB_VISION", "vision"),
        "embed":  _m("MOM_JOB_EMBED",  "embed"),
    },
    # Everything on one model. The fallback that always works.
    "single": {
        "reason": _m("MOM_MODEL_REASON", core.OLLAMA_MODEL),
        "quick":  _m("MOM_MODEL_QUICK",  core.OLLAMA_MODEL),
        "vision": _m("MOM_MODEL_VISION", core.OLLAMA_MODEL),
        "embed":  _m("MOM_MODEL_EMBED",  "embeddinggemma:300m"),
    },
}


def models() -> dict:
    return PROFILES.get(PROFILE, PROFILES["single"])


SCHEMA = """
CREATE TABLE IF NOT EXISTS routing (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    username   TEXT NOT NULL DEFAULT '',
    thread_id  INTEGER,
    channel    TEXT NOT NULL DEFAULT 'web',   -- web | voice | photo
    intent     TEXT NOT NULL DEFAULT '',
    role       TEXT NOT NULL,                 -- reason | quick | vision | embed
    model      TEXT NOT NULL,
    ms         INTEGER NOT NULL DEFAULT 0,
    in_tokens  INTEGER NOT NULL DEFAULT 0,
    out_tokens INTEGER NOT NULL DEFAULT 0,
    ok         INTEGER NOT NULL DEFAULT 1,
    detail     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_routing_ts ON routing(ts);
CREATE INDEX IF NOT EXISTS ix_routing_thread ON routing(thread_id);
"""


def init() -> None:
    with core.tx() as c:
        c.executescript(SCHEMA)


def record(role: str, model: str, ms: int, username: str = "",
           thread_id: Optional[int] = None, channel: str = "web",
           intent: str = "", ok: bool = True, detail: str = "",
           in_tokens: int = 0, out_tokens: int = 0) -> None:
    """Every model call, logged. This table is what the dashboard draws."""
    try:
        with core.tx() as c:
            c.execute(
                "INSERT INTO routing(ts,username,thread_id,channel,intent,role,"
                "model,ms,in_tokens,out_tokens,ok,detail) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), username, thread_id, channel, intent, role, model,
                 int(ms), int(in_tokens), int(out_tokens), 1 if ok else 0,
                 detail[:500]))
    except Exception:
        pass    # telemetry must never break a support request


# ================================================================= intent ===

# Intent decides the route, so it is regex and keywords, not a model call. It
# runs in microseconds, it is testable, and it cannot hallucinate a category.
INTENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("safety",   ("scam", "fraud", "suspicious", "phish", "hacked", "virus",
                  "ransom", "gift card", "wire transfer", "bitcoin", "refund",
                  "microsoft called", "locked me out", "stole")),
    ("printer",  ("printer", "print", "scanner", "scan", "ink", "toner", "jam")),
    ("internet", ("wifi", "wi-fi", "internet", "router", "modem", "network",
                  "offline", "no connection", "slow internet")),
    ("phone",    ("phone", "android", "samsung", "galaxy", "pixel", "tablet",
                  "app store", "play store", "texts", "messages")),
    ("account",  ("password", "log in", "login", "sign in", "locked out",
                  "email", "gmail", "proton", "account", "two factor")),
    ("cooking",  ("recipe", "cook", "bake", "oven", "roast", "seasoning",
                  "how long do i", "dinner", "ingredient", "substitute")),
    ("computer", ("computer", "laptop", "windows", "pc", "screen", "keyboard",
                  "mouse", "slow", "frozen", "update", "blue screen")),
]


def classify_intent(text: str, has_image: bool = False) -> str:
    t = (text or "").lower()
    for name, words in INTENT_RULES:
        if any(w in t for w in words):
            return name
    return "photo" if has_image else "other"


# ============================================================== specialists ==

@dataclass
class Plan:
    """What is going to happen, decided before anything runs."""
    intent: str
    channel: str
    steps: list[dict] = field(default_factory=list)   # [{role, model, why}]

    @property
    def collaborative(self) -> bool:
        return len({s["role"] for s in self.steps}) > 1

    def describe(self) -> str:
        return " → ".join(f"{s['role']}({s['model']})" for s in self.steps)


def plan(text: str, has_image: bool = False, channel: str = "web") -> Plan:
    """Choose the specialists. Pure function -- no I/O, trivially testable."""
    m = models()
    intent = classify_intent(text, has_image)
    steps: list[dict] = []

    if has_image:
        steps.append({
            "role": "vision", "model": m["vision"],
            "why": "She sent a picture, so something has to actually look at it "
                   "and turn it into words before anything can reason about it."})

    steps.append({
        "role": "reason", "model": m["reason"],
        "why": ("A possible scam gets the most careful model available."
                if intent == "safety" else
                "Works out the answer and the steps.")})

    return Plan(intent=intent, channel=channel, steps=steps)


# --------------------------------------------------------------- execution --

def describe_image(image_paths: list[str], question: str,
                   username: str = "", thread_id: Optional[int] = None) -> str:
    """Turn her photo into a factual description the reasoning model can use.

    Deliberately narrow. The vision model is asked to REPORT, never to advise:
    it reads error text, counts indicator lights, notes what is on the screen.
    Advice comes from the reasoning model, which is the one with the rules and
    the safety knowledge base loaded. A vision model improvising troubleshooting
    steps from a photo is exactly the kind of confident wrong answer that gets
    someone to unplug the wrong thing.
    """
    import base64
    m = models()
    encoded = []
    for p in image_paths[:3]:
        try:
            with open(p, "rb") as fh:
                encoded.append(base64.b64encode(fh.read()).decode())
        except Exception as e:  # noqa: BLE001
            core.log("warn", "router", f"could not read image {p}: {e}")
    if not encoded:
        return ""

    prompt = (
        "Describe exactly what is visible in this picture, for a technician who "
        "cannot see it.\n\n"
        "Report ONLY what is actually there:\n"
        "- Any text, error message, or number, transcribed word for word\n"
        "- Any indicator lights: how many, what colour, steady or blinking\n"
        "- What device or screen this appears to be\n"
        "- Anything unplugged, damaged, or obviously wrong\n\n"
        "Do NOT suggest fixes. Do NOT guess at causes. If the picture is too "
        "blurry or dark to read something, say so plainly.\n\n"
        f"She said: {(question or '(nothing)')[:500]}")

    t0 = time.time()
    try:
        out = core.chat_raw(
            messages=[{"role": "user", "content": prompt, "images": encoded}],
            model=m["vision"], temperature=0.1)
        ms = int((time.time() - t0) * 1000)
        record("vision", m["vision"], ms, username, thread_id, "photo",
               "photo", ok=True, out_tokens=len(out) // 4)
        return out.strip()
    except Exception as e:  # noqa: BLE001
        ms = int((time.time() - t0) * 1000)
        record("vision", m["vision"], ms, username, thread_id, "photo",
               "photo", ok=False, detail=str(e))
        core.log("warn", "router", f"vision failed: {e}")
        return ""


def run(messages: list[dict], role: str = "reason", temperature: float = 0.3,
        username: str = "", thread_id: Optional[int] = None,
        intent: str = "", channel: str = "web", use_tools: bool = False) -> str:
    """Call one specialist and record what it cost.

    With use_tools, the model may look things up before answering -- see
    tools.py, and the note there about why the budget is two rounds and not
    twelve. Every round is recorded separately, so the models page shows what
    the loop actually cost rather than hiding it inside one number.
    """
    m = models()
    model = m.get(role, m["reason"])

    # n8n gets a say, but only when the router is doing the work -- the job
    # names it returns mean nothing to a direct Ollama call.
    if core.ROUTER_URL and CHOOSE_URL:
        model = choose_job(
            role, model,
            question=(messages[-1].get("content", "") if messages else ""),
            has_image=any(isinstance(x.get("content"), list) for x in messages),
            category=intent, channel=channel)

    t0 = time.time()
    try:
        if core.ROUTER_URL:
            # THROUGH THE ROUTER. `model` here is a job name, not a model.
            # Falls back to the direct Ollama path on any failure -- a router
            # that is down must not stop her getting an answer.
            try:
                msg = core.via_router(
                    messages, job=model, temperature=temperature,
                    tools=(_tool_spec() if use_tools else None))
                if msg.get("tool_calls") and use_tools:
                    out = _tool_rounds_router(messages, msg, model, temperature)
                else:
                    out = msg.get("content") or ""
                if out:
                    record(role, model, int((time.time() - t0) * 1000), username,
                           thread_id, channel, intent, ok=True)
                    return out
                core.log("warn", "router", f"{model} returned nothing; going direct")
            except Exception as e:  # noqa: BLE001
                core.log("warn", "router", f"router unavailable ({e}); going direct")
            model = models().get(role) if PROFILE != "router" else _direct_model(role)

        if use_tools:
            out = _with_tools(messages, model, temperature, username,
                              thread_id, intent, channel, role)
        else:
            out = core.chat(messages, temperature=temperature, model=model)
        ms = int((time.time() - t0) * 1000)
        record(role, model, ms, username, thread_id, channel, intent,
               ok=True, in_tokens=sum(len(x.get("content", "")) for x in messages) // 4,
               out_tokens=len(out) // 4)
        return out
    except Exception as e:
        ms = int((time.time() - t0) * 1000)
        record(role, model, ms, username, thread_id, channel, intent,
               ok=False, detail=str(e))
        raise


# ---------------------------------------------------------------------------
#  Letting n8n pick the model
# ---------------------------------------------------------------------------
#  WHY ASK SOMETHING ELSE WHICH MODEL TO USE
#
#  litellm/config.yaml already maps a job to a model, and that is the right
#  place for "what does `mama` mean". What it cannot express is a RULE:
#
#      a photo question always goes to vision
#      anything over 4000 characters goes to long-context
#      after 11pm use the small model, the GPU is doing backups
#      if it mentions money or a bank, escalate to the cloud model
#      while the phone line is busy, never touch a 7B
#
#  Those are decisions that change with how the house is used, and editing
#  YAML and restarting a router is the wrong shape for something you want to
#  fiddle with. n8n is already running and its whole job is "when X, do Y",
#  with rules you change by dragging a box.
#
#  SO: n8n DECIDES, LITELLM EXECUTES. This asks a webhook which job to use and
#  then calls the router with the answer. n8n never sees the question's full
#  text and never does inference -- it returns a word.
#
#  IT IS NOT ON THE CRITICAL PATH, and that is the load-bearing part. The
#  timeout is short, every failure is swallowed, and the fallback is the job
#  this app would have chosen anyway. n8n being down, slow, half-configured or
#  mid-edit must never be why somebody does not get an answer -- which is
#  exactly the risk you take by putting a workflow engine in front of a model.
CHOOSE_URL = os.getenv("MOM_CHOOSE_URL", "").rstrip("/")
CHOOSE_TIMEOUT = float(os.getenv("MOM_CHOOSE_TIMEOUT", "1.5"))


def choose_job(role: str, default: str, *, question: str = "",
               has_image: bool = False, category: str = "",
               channel: str = "web") -> str:
    """Ask n8n which job to use. Returns `default` on anything unexpected.

    Only FACTS ABOUT the question are sent, not the question -- its length,
    whether it came with a photo, what it was classified as. That is enough for
    every rule above and means the workflow log does not become a copy of
    everything she has ever asked.
    """
    if not CHOOSE_URL:
        return default
    try:
        r = httpx.post(CHOOSE_URL, timeout=CHOOSE_TIMEOUT, json={
            "app": "mama",
            "role": role,
            "default": default,
            "chars": len(question or ""),
            "has_image": has_image,
            "category": category,
            "channel": channel,
            "phone_busy": _phone_busy(),
            "hour": time.localtime().tm_hour,
            "available": sorted(set(PROFILES.get("router", {}).values())),
        })
        r.raise_for_status()
        job = str((r.json() or {}).get("job") or "").strip()
    except Exception as e:  # noqa: BLE001
        core.log("info", "choose", f"n8n did not answer ({e.__class__.__name__}); "
                                   f"using {default}")
        return default

    if not job:
        return default
    # A job n8n invented is worse than no answer: the router would 400 and the
    # question would fail for a reason nobody could see. Only accept a name
    # that is actually configured.
    if job not in _known_jobs():
        core.log("warn", "choose", f"n8n asked for '{job}', which is not a job; "
                                   f"using {default}")
        return default
    if job != default:
        core.log("info", "choose", f"n8n chose {job} over {default}")
    return job


_JOBS_CACHE: dict = {"at": 0.0, "names": set()}


def _known_jobs() -> set:
    """What the router actually offers, asked once a minute.

    Cached because this runs on the way to every answer and the list changes
    about twice a year -- but not cached forever, or adding a job would need a
    restart of this app to become usable.
    """
    now = time.time()
    if now - _JOBS_CACHE["at"] < 60 and _JOBS_CACHE["names"]:
        return _JOBS_CACHE["names"]
    names = set()
    try:
        headers = ({"Authorization": f"Bearer {core.ROUTER_KEY}"}
                   if core.ROUTER_KEY else {})
        r = httpx.get(f"{core.ROUTER_URL}/v1/models", headers=headers, timeout=4)
        r.raise_for_status()
        names = {m.get("id", "") for m in r.json().get("data", [])}
    except Exception:  # noqa: BLE001
        pass
    if names:
        _JOBS_CACHE.update(at=now, names=names)
    return names or _JOBS_CACHE["names"]


def _phone_busy() -> bool:
    """Whether a call is live. A 7B loading mid-call is a caller waiting."""
    url = os.getenv("MOM_PHONE_HEALTH", "")
    if not url:
        return False
    try:
        return bool(httpx.get(url, timeout=1).json().get("busy"))
    except Exception:  # noqa: BLE001
        return False


def _tool_spec():
    from . import tools
    return tools.SPEC


def _direct_model(role: str) -> str:
    """What to use when the router is configured but not answering.

    The job names mean nothing to Ollama, so falling back needs a real model.
    These mirror the `resident` profile's intent -- reliable over clever --
    using only what is installed.
    """
    return {
        "reason": os.getenv("MOM_MODEL_REASON", "qwen2.5:7b"),
        "quick":  os.getenv("MOM_MODEL_QUICK",  "llama3.2:3b"),
        "vision": os.getenv("MOM_MODEL_VISION", "llama3.2-vision:11b"),
        "embed":  os.getenv("MOM_MODEL_EMBED",  "nomic-embed-text"),
    }.get(role, "qwen2.5:7b")


def _tool_rounds_router(messages: list[dict], first: dict, job: str,
                        temperature: float) -> str:
    """The same bounded loop as _with_tools, but through the router."""
    from . import tools

    convo = list(messages)
    msg = first
    for _ in range(tools.MAX_ROUNDS):
        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or ""
        convo.append({"role": "assistant", "content": msg.get("content") or "",
                      "tool_calls": calls})
        for call in calls[:4]:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            result = tools.run_tool(name, fn.get("arguments"))
            core.log("info", "tools", f"{name} -> {len(result)} chars")
            # OpenAI shape wants the id it called with, or the model cannot
            # tell which answer belongs to which question.
            convo.append({"role": "tool", "tool_call_id": call.get("id", ""),
                          "name": name, "content": result})
        msg = core.via_router(convo, job=job, temperature=temperature,
                              tools=tools.SPEC)
    # Out of rounds: answer from what was found, with no tools offered.
    return (core.via_router(convo, job=job, temperature=temperature)
            .get("content") or "")


def _with_tools(messages: list[dict], model: str, temperature: float,
                username: str, thread_id, intent: str, channel: str,
                role: str) -> str:
    """Let the model look things up, at most tools.MAX_ROUNDS times.

    FALLS BACK RATHER THAN FAILS. If Ollama does not understand `tools` -- an
    older build, or a model without the capability -- this drops straight back
    to a plain call. Somebody asking why her printer is offline must not get an
    error page because a feature she has never heard of is unavailable.
    """
    from . import tools

    convo = list(messages)
    for _ in range(tools.MAX_ROUNDS):
        try:
            msg = core.chat_tools(convo, model=model, temperature=temperature,
                                  tools=tools.SPEC)
        except Exception as e:  # noqa: BLE001
            core.log("warn", "tools", f"tool call unsupported, answering plainly: {e}")
            return core.chat(messages, temperature=temperature, model=model)

        calls = msg.get("tool_calls") or []
        if not calls:
            return msg.get("content") or ""

        # The assistant's own turn has to go back in, or the tool results below
        # are replies to a question that is not in the transcript.
        convo.append({"role": "assistant", "content": msg.get("content") or "",
                      "tool_calls": calls})
        for call in calls[:4]:
            fn = (call.get("function") or {})
            name = fn.get("name", "")
            result = tools.run_tool(name, fn.get("arguments"))
            core.log("info", "tools", f"{name} -> {len(result)} chars")
            convo.append({"role": "tool", "name": name, "content": result})

    # Out of rounds. Answer from what was found -- WITHOUT the tools offered
    # again, so this call cannot ask for more and must produce prose.
    return core.chat(convo, temperature=temperature, model=model)


def prewarm(roles: tuple[str, ...] = ("reason", "vision", "embed"),
            block: bool = False) -> None:
    """Load the specialists into VRAM before she needs them.

    This exists because of Wake-on-LAN. The desktop hibernates, so VRAM is empty
    every time it comes back -- pinning models with OLLAMA_KEEP_ALIVE=-1 keeps
    them resident while the machine is awake, but hibernation drops all of it.
    After each wake, the first request would otherwise pay for loading 7.2 GB
    from disk on top of the boot wait she has already sat through.

    The trick is that there is free time right there and nothing was using it.
    When she records a voice note the order is: wake the PC, transcribe with
    Whisper, then reason about what she said. Whisper takes several seconds and
    does not touch the language models at all, so we warm them in the background
    during transcription and the load has already happened by the time it
    matters.

    Fire-and-forget by default. A warm-up that fails is not an error -- the real
    request that follows will simply load the model itself, the slow way.
    """
    import threading

    m = models()

    def _warm():
        for role in roles:
            name = m.get(role)
            if not name:
                continue
            t0 = time.time()
            try:
                if role == "embed":
                    httpx.post(core.ollama_url("/api/embed"),
                               json={"model": name, "input": "warm"}, timeout=120)
                else:
                    httpx.post(core.ollama_url("/api/generate"),
                               json={"model": name, "prompt": "hi", "stream": False,
                                     "keep_alive": KEEP_ALIVE,
                                     "options": {"num_predict": 1}}, timeout=180)
                core.log("info", "router",
                         f"prewarmed {role} ({name}) in {int(time.time()-t0)}s")
            except Exception as e:  # noqa: BLE001
                core.log("warn", "router", f"prewarm {role} failed: {e}")

    if block:
        _warm()
    else:
        threading.Thread(target=_warm, daemon=True).start()


# ================================================================ dashboard ==

def health() -> list[dict]:
    """Which configured specialists are actually installed on the desktop."""
    try:
        have = set(core.available_models())
    except Exception:
        have = set()
    bare = {h.split(":")[0] for h in have}
    out = []
    for role, name in models().items():
        installed = name in have or name.split(":")[0] in bare
        out.append({"role": role, "model": name, "installed": installed})
    return out


def usage(days: int = 7) -> dict:
    since = time.time() - days * 86400
    rows = core.conn().execute(
        "SELECT role, model, COUNT(*) n, AVG(ms) avg_ms, "
        "SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) fails "
        "FROM routing WHERE ts>=? GROUP BY role, model ORDER BY n DESC",
        (since,)).fetchall()
    by_intent = core.conn().execute(
        "SELECT intent, COUNT(*) n FROM routing WHERE ts>=? AND intent!='' "
        "GROUP BY intent ORDER BY n DESC", (since,)).fetchall()
    return {
        "profile": PROFILE,
        "days": days,
        "by_model": [dict(r) for r in rows],
        "by_intent": [dict(r) for r in by_intent],
        "total": sum(r["n"] for r in rows),
    }


def recent(limit: int = 100) -> list:
    return core.conn().execute(
        "SELECT * FROM routing ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
