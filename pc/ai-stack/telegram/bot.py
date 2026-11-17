"""
Telegram bridge.

Long polling only -- the container dials out to Telegram, Telegram never dials
in. No webhook, no open port, no reverse proxy, nothing to expose. This is why
the whole stack can stay bound to 127.0.0.1 and still be reachable from your
phone.

Access is an explicit numeric allowlist. Anyone else who finds the bot gets a
flat refusal and their id logged so you can see who knocked.
"""

from __future__ import annotations

import asyncio
import html
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

# Used by /usage, /health, /models and /history. It was missing for a while and
# every one of those commands died with `NameError: httpx is not defined`,
# which read as "the service is down" rather than "this file doesn't import
# its HTTP client" -- hence a long detour debugging n8n and LiteLLM, which
# were both fine. The Hermes CLI had it; this file didn't.
import httpx

import json
import re
import secrets

from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from shared.router import Router, pick_job
from shared.transcript import Transcript
from shared.health import format_health
from shared.history import METRICS as HISTORY_METRICS, resolve as resolve_metric
from shared.printing import (
    describe_job,
    format_printer_status,
    reject_reason,
    safe_name,
)
from shared.models_health import (
    format_inventory_text,
    parse_health,
    parse_installed,
    parse_routes,
    reconcile,
)
from shared.usage import (
    diagnose_metrics,
    format_text,
    gpu_from_ollama,
    gpu_from_stats,
    summarise_llm,
)
from shared.voice import Transcriber, Voice

# The same agent Hermes runs in the terminal, with the same tool belt. Not a
# reimplementation -- literally the same module, copied into this image by the
# Dockerfile, so a fix to a tool lands on both surfaces at once.
from hermes_agent import tools
from hermes_agent.agent import SYSTEM_PROMPT as HERMES_SYSTEM
from hermes_agent.agent import Agent

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("telegram-bridge")

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
# --- career mode -------------------------------------------------------------
#  Where a career question goes instead of the house agent. See the
#  header of the /career command below for why it is routed rather than
#  answered here.
ZEE_URL = os.environ.get("ZEE_URL", "http://casey:8000").rstrip("/")
# telegram id -> the address her portal knows her by, so what she says is
# filed under HER name. "123456=casey@example.com,789=jason@example.com"
ZEE_USERS = {}
for _pair in os.environ.get("ZEE_TELEGRAM_USERS", "").split(","):
    if "=" in _pair:
        _k, _v = _pair.split("=", 1)
        _k = _k.strip()
        if _k.isdigit() and _v.strip():
            ZEE_USERS[int(_k)] = _v.strip()

ALLOWED = {
    int(x) for x in os.environ.get("TELEGRAM_ALLOWED_USER_IDS", "").replace(" ", "").split(",") if x
}
DEFAULT_JOB = os.environ.get("DEFAULT_JOB", "chat")
VOICE_REPLIES = os.environ.get("TELEGRAM_VOICE_REPLIES", "false").lower() == "true"
TOOLS_ON = os.environ.get("TELEGRAM_TOOLS", "true").lower() == "true"

# Whether write_file, edit_file and run_shell are allowed WITHOUT sending
# /unlock first.
#
# The default is false, and that's a deliberate call rather than timidity.
# Telegram is your phone. The allowlist proves a Telegram ACCOUNT is yours; it
# cannot prove the person holding the handset is. Left unlocked, anyone who
# picks up an unlocked phone has a shell on the machine that answers your mum's
# business line. Reading is harmless and stays on; writing and running are one
# word away when you actually want them, and expire when the chat resets.
UNLOCKED_BY_DEFAULT = os.environ.get("TELEGRAM_UNLOCKED", "false").lower() == "true"


# /usage reads LiteLLM DIRECTLY, deliberately not through n8n, so token
# counts survive n8n being unwell. Only the GPU half needs n8n (nothing else
# here can run nvidia-smi, since nothing else has the Docker socket), and it
# falls back to asking Ollama what it has loaded.
#
# For charts and history, open Netdata: http://localhost:19999
LITELLM_BASE = "http://litellm:4000"
LITELLM_METRICS_URL = f"{LITELLM_BASE}/metrics"
OLLAMA_TAGS_URL = "http://ollama:11434/api/tags"
N8N_STATS_URL = "http://n8n:5678/webhook/stats"
OLLAMA_PS_URL = "http://ollama:11434/api/ps"

# The recorder: one row every two minutes, one month kept. Asked over HTTP
# rather than by opening its SQLite file, so there is exactly one writer and
# nothing here to lock.
METRICS_URL = os.environ.get("METRICS_URL", "http://metrics:8000")

# /tickets: Mama's open tickets, resolved or closed by button. Mom's own
# internal API, not through Caddy -- see the long comment above
# /internal/tickets/open in Mom/app/main.py for why that is fine here.
MOM_URL = os.environ.get("MOM_URL", "http://mom:8001")
MOM_TICKET_API_SECRET_KEY = os.environ.get("MOM_TICKET_API_SECRET_KEY", "")

# --- printing -------------------------------------------------------------
# The queue lives under /transcripts because that folder is ALREADY mounted
# into this container and visible from Windows. No new share, no new port,
# no printer protocol to guess at: this side writes a file, the Windows side
# picks it up and prints it with the real Brother driver.
#
# Nothing is printed until you tap Approve. That matters because a phone
# share-sheet is easy to fire by accident, and the cost of a mistake here is
# measured in toner and paper you are not at home to stop.
PRINT_DIR = Path(os.environ.get("PRINT_QUEUE", "/transcripts/print-queue"))
PRINT_PENDING = PRINT_DIR / "pending"
PRINT_APPROVED = PRINT_DIR / "approved"
PRINTER_STATUS = Path(os.environ.get("PRINTER_STATUS",
                                     "/transcripts/printer-status.json"))

# Same key the router already uses. Harmless when /metrics is open, and the
# difference between working and a silent 401 when it isn't.
METRICS_HEADERS = {
    "Authorization": f"Bearer {os.environ.get('ROUTER_API_KEY', '')}"
}

MAX_HISTORY = 30
TG_LIMIT = 4000

# Hermes' own prompt, plus the two things that differ on a phone.
SYSTEM_AGENT = HERMES_SYSTEM + (
    "\n\nYou are being read on a PHONE over Telegram. Keep replies short and "
    "plain. Avoid long code blocks unless asked for code. No preamble.\n"
    "If a tool comes back 'refused', say plainly what was blocked and that "
    "/unlock allows it for this chat. Do not try to work around it."
)

router = Router(default_job=DEFAULT_JOB)
voice = Voice()
# The EXPRESSIVE engine (chatterbox in the voice-narrate container) -- the
# one the Pee-Wee tribute narrates with. Slower and worth it; /sample uses
# this so Jason can audition tellings from his phone. Ten-minute timeout
# because beauty renders at roughly a minute per minute of speech.
voice_narrate = Voice(
    base_url=os.environ.get("NARRATE_TTS_BASE_URL", "http://voice-narrate:8091/v1"),
    timeout=600.0,
)
stt = Transcriber()

SYSTEM = (
    "You are a private assistant reachable over Telegram. Replies are read on a "
    "phone: be brief and concrete. Use plain text; avoid heavy markdown and long "
    "code blocks unless asked for code. No preamble."
)


# -- per-chat state -----------------------------------------------------------


class ChatState:
    def __init__(self, chat_id: int) -> None:
        self.chat_id = chat_id
        self.job = DEFAULT_JOB
        # Same reasoning as the CLI: auto-routing on unless AUTO_JOB=false.
        self.auto = os.environ.get("AUTO_JOB", "true").lower() == "true"
        self.voice_replies = VOICE_REPLIES
        self.voice_name = voice.voice
        # Output controls (/setvoice): which engine renders this chat's
        # spoken replies, and an optional pace override.
        self.voice_engine = "fast"
        self.voice_speed = None
        self.tools_on = TOOLS_ON
        # Career mode: this chat's words go to the career portal instead
        # of the house agent. Off by default -- turning it on is a
        # deliberate act, so nobody's shopping list ends up in a
        # promotion file.
        self.career = False
        self.unlocked = UNLOCKED_BY_DEFAULT
        # What the agent did this turn, so the reply can say so.
        self.tool_log: list[str] = []
        self.history: list[dict] = [{"role": "system", "content": SYSTEM}]
        self.transcript = Transcript(surface="telegram", title=f"chat {chat_id}")
        self.agent = self._new_agent()
        # Set by the "🔗 Paste a link" ticket button; consumed by the very
        # next text message, which is treated as the link rather than a
        # normal chat turn. See on_text() and on_ticket_button().
        self.awaiting_link_for: int | None = None

    def _new_agent(self) -> Agent:
        return Agent(
            router=router,
            transcript=self.transcript,
            job=self.job,
            auto_job=self.auto,
            system_prompt=SYSTEM_AGENT,
            confirm=self._confirm,
            on_tool=self._on_tool,
        )

    def _confirm(self, name: str, args: dict) -> bool | str:
        """Gate the three tools that can change something."""
        if self.unlocked:
            return True
        return (
            f"'{name}' is locked in this Telegram chat. Reading is allowed; "
            "writing and running commands are not until the user sends /unlock."
        )

    def _on_tool(self, name: str, args: dict, result: str, seconds: float = 0.0) -> None:
        hint = args.get("path") or args.get("command") or args.get("pattern") or args.get("url") or ""
        hint = str(hint)
        if len(hint) > 60:
            hint = hint[:57] + "..."
        blocked = result.startswith("refused:")
        took = f" {seconds:.1f}s" if seconds >= 0.1 else ""
        self.tool_log.append(f"{'blocked' if blocked else 'ran'} {name} {hint}{took}".strip())

    def new_session(self) -> None:
        self.history = [{"role": "system", "content": SYSTEM}]
        self.transcript = Transcript(surface="telegram", title=f"chat {self.chat_id}")
        # Re-locks on /new. A session that stays unlocked forever is just
        # "unlocked" with extra steps.
        self.unlocked = UNLOCKED_BY_DEFAULT
        self.agent = self._new_agent()

    def sync_agent(self) -> None:
        """Push route changes onto the agent."""
        self.agent.job = self.job
        self.agent.auto_job = self.auto

    def trim(self) -> None:
        if len(self.history) > MAX_HISTORY + 1:
            self.history = [self.history[0]] + self.history[-MAX_HISTORY:]
        self.agent.trim(MAX_HISTORY)


STATES: dict[int, ChatState] = {}


def state_for(chat_id: int) -> ChatState:
    if chat_id not in STATES:
        STATES[chat_id] = ChatState(chat_id)
    return STATES[chat_id]


# -- guard --------------------------------------------------------------------


def authorised(update: Update) -> bool:
    user = update.effective_user
    if user is None:
        return False
    if not ALLOWED:
        log.error("TELEGRAM_ALLOWED_USER_IDS is empty - refusing everyone")
        return False
    if user.id in ALLOWED:
        return True
    log.warning("blocked user id=%s username=%s", user.id, user.username)
    return False


async def deny(update: Update) -> None:
    uid = update.effective_user.id if update.effective_user else "?"
    await update.effective_message.reply_text(
        f"Not authorised. Your Telegram id is {uid} - add it to "
        f"TELEGRAM_ALLOWED_USER_IDS in .env if this is your bot."
    )


# -- helpers ------------------------------------------------------------------


async def send_long(update: Update, text: str) -> None:
    text = text or "(empty response)"
    for i in range(0, len(text), TG_LIMIT):
        await update.effective_message.reply_text(text[i : i + TG_LIMIT])


async def send_voice(update: Update, text: str, st: ChatState) -> None:
    if not text.strip():
        return
    try:
        reply_engine = voice_narrate if st.voice_engine == "expressive" else voice
        # Both engines serve every voice in ./voices. If the chosen one is
        # down (a restart, a hiccup), the sibling answers -- slower or less
        # expressive beats silent.
        sibling = voice if reply_engine is voice_narrate else voice_narrate

        def _render(eng):
            # steps=4 keeps Telegram the QUICK path (the container default
            # is 16 for every other project); other engines ignore it
            return eng.speak(text[:1500], voice=st.voice_name, fmt="ogg",
                             normalize=True, speed=st.voice_speed, steps=4)

        try:
            data = await asyncio.to_thread(_render, reply_engine)
        except Exception:  # noqa: BLE001
            log.warning("voice engine %s failed; trying the sibling",
                        st.voice_engine)
            data = await asyncio.to_thread(_render, sibling)
        await update.effective_message.reply_voice(voice=io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        log.warning("tts failed: %s", e)
        await update.effective_message.reply_text(f"(voice unavailable: {e})")


# -- commands -----------------------------------------------------------------


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    await update.effective_message.reply_text(
        "Hermes, on your phone. Same agent as the CLI, same workspace folder.\n\n"
        f"job: {st.job}\n"
        f"agent tools: {'on' if st.tools_on else 'off (plain chat)'}\n"
        f"writing/shell: {'UNLOCKED' if st.unlocked else 'locked (send /unlock)'}\n"
        f"voice replies: {'on' if st.voice_replies else 'off'}\n"
        f"voice input: {'on' if stt.enabled else 'off (no STT key set)'}\n\n"
        "It can read and search your workspace right away. Writing files and "
        "running commands need /unlock first.\n\n"
        "/help for everything else."
    )


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    await update.effective_message.reply_text(
        "This is Hermes - the same agent as the CLI, same workspace folder.\n\n"
        "/tools - agent mode on/off (off = plain chat, quicker)\n"
        "/unlock - allow writing files and running commands here\n"
        "/lock - back to reading only\n"
        "/jobs - list model routes\n"
        "/job <name> - switch route (fast, chat, coding, reasoning, ...)\n"
        "/auto - toggle picking the route automatically per message\n"
        "/voice - toggle spoken replies\n"
        "/voices - list cloned voices\n"
        "/setvoice <name> [fast|expressive] [pace] - choose voice + output\n"
        "/new - start a fresh conversation\n"
        "/transcript - where this conversation is being logged\n"
        "/export - get this conversation as a markdown file\n"
        "/status - stack health\n"
        "/health - the whole picture: GPU, temps, VRAM, disk, RAM, network, uptime\n"
        "/stats - right now at a glance, with buttons into each section\n"
        "/stats full - every detail, paged. /stats gpu for one section\n"
        "/print - printer status; send me any PDF or photo to print it at home\n"
        "/tickets - Mama's open tickets, with a Close button on each\n"
        "/trends - the last 24h: what's rising, what broke (also /history)\n"
        "/trends vram 48 - one metric as a chart, however far back (a month is kept)\n"
        "/usage - GPU and token usage as text (works even if n8n is down)\n"
        "/models - check every configured model is actually installed\n"
        "/models check <route> - really call one route (loads it into VRAM)\n"
        "memory, disk, restarts, gpu, trend, models, latency, tokens, business. "
        "You'll also hear from this bot on its own if anything goes down."
    )


MENU_BUTTONS = [
    [("Right now", "stats"), ("Everything", "statsfull")],
    [("What's wrong?", "health"), ("Last 24h", "trends")],
    [("GPU + tokens", "usage"), ("Models", "models")],
    [("Printer", "print"), ("Containers", "status")],
]


async def cmd_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Buttons for the things you actually look at.

    The command list in Telegram's menu is alphabetical and full of things
    like /setvoice that you use twice a year. This is the short list, phrased
    as questions rather than nouns, so there is something to TAP instead of a
    name to remember.
    """
    if not authorised(update):
        return await deny(update)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=f"menu:{cmd}")
         for label, cmd in row]
        for row in MENU_BUTTONS
    ])
    await update.effective_message.reply_text(
        "What do you want to see?\n"
        "(send me a file any time to print it)",
        reply_markup=kb)


async def on_menu_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Run the command behind a menu button."""
    q = update.callback_query
    if not authorised(update):
        await q.answer("not allowed", show_alert=True)
        return
    await q.answer()
    cmd = (q.data or "").split(":", 1)[-1]
    if cmd == "statsfull":
        return await _send_full(update)
    handler = {
        "stats": cmd_stats,
        "health": cmd_health, "trends": cmd_history, "usage": cmd_usage,
        "print": cmd_print, "status": cmd_status, "models": cmd_models,
    }.get(cmd)
    if handler is None:
        return await q.edit_message_text("That button is from an older message.")
    # ctx.args is what CommandHandler would normally populate. The menu never
    # passes arguments, so an empty list is both correct and what the
    # handlers already cope with.
    ctx.args = []
    await handler(update, ctx)


async def cmd_tools(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Agent mode on/off. Off is plain chat -- quicker, and enough most of the time."""
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    st.tools_on = not st.tools_on
    await update.effective_message.reply_text(
        f"agent tools: {'on' if st.tools_on else 'off (plain chat)'}\n"
        + ("reading is allowed; /unlock to also write and run commands"
           if st.tools_on and not st.unlocked else "")
    )


async def cmd_unlock(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Allow write_file, edit_file and run_shell for this chat."""
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    st.unlocked = True
    st.transcript.event("telegram_unlocked")
    log.warning("chat %s UNLOCKED mutating tools", st.chat_id)
    await update.effective_message.reply_text(
        "unlocked: writing files and running commands is allowed in this chat.\n"
        "/lock to undo. /new also re-locks."
    )


async def cmd_lock(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    st.unlocked = False
    st.transcript.event("telegram_locked")
    await update.effective_message.reply_text("locked: reading only.")


async def cmd_jobs(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    jobs = await asyncio.to_thread(router.available_jobs)
    lines = [("* " if j == st.job else "  ") + j for j in jobs]
    await update.effective_message.reply_text("routes:\n" + "\n".join(lines))


async def cmd_job(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    if not ctx.args:
        return await update.effective_message.reply_text(f"current route: {st.job}")
    st.job = ctx.args[0]
    st.auto = False
    st.transcript.event("job_changed", job=st.job)
    await update.effective_message.reply_text(f"route -> {st.job}")


async def cmd_auto(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    st.auto = not st.auto
    await update.effective_message.reply_text(
        f"auto route selection: {'on' if st.auto else 'off'}"
    )


async def cmd_voice(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    st.voice_replies = not st.voice_replies
    ok = await asyncio.to_thread(voice.available)
    note = "" if ok else "\n(warning: tts service isn't reachable right now)"
    await update.effective_message.reply_text(
        f"voice replies: {'on' if st.voice_replies else 'off'}{note}"
    )


async def cmd_sample(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/sample <text> -- hear it in Randy's EXPRESSIVE voice (the tribute's
    narration engine). /sample fast <text> renders the same words through
    the quick phone-line engine instead, for A/B against the good one."""
    if not allowed(update):
        return
    args = (update.effective_message.text or "").split(maxsplit=1)
    text = args[1].strip() if len(args) > 1 else ""
    engine = voice_narrate
    label = "expressive (chatterbox)"
    if text.lower().startswith("fast "):
        text, engine, label = text[5:], voice, "fast (luxtts)"
    # an optional voice name right after /sample (or after "fast") picks
    # who speaks -- /sample Randy-tuned ... or /sample fast Jason ...
    # Default stays Randy, the tribute's narrator.
    sample_voice = "Randy"
    head = text.split(maxsplit=1)
    if head:
        try:
            known = {v.lower(): v for v in
                     await asyncio.to_thread(voice.list_voices)}
        except Exception:  # noqa: BLE001
            known = {}
        if head[0].lower() in known:
            sample_voice = known[head[0].lower()]
            text = head[1].strip() if len(head) > 1 else ""
    if not text:
        await update.effective_message.reply_text(
            "Give me words: /sample The record store on South Pearl Street...\n"
            "A/B the engines: /sample fast <same words>\n"
            "Pick who speaks: /sample Randy-tuned <words> or "
            "/sample fast Jason <words>\n"
            "(see /voices for the list)")
        return
    await update.effective_message.reply_text(
        f"rendering with the {label} engine, voice {sample_voice}"
        + (" -- about a minute per few sentences, worth the wait..." if engine is voice_narrate else "..."))
    try:
        data = await asyncio.to_thread(
            engine.speak, text[:1200], voice=sample_voice, fmt="ogg", normalize=True)
        await update.effective_message.reply_voice(voice=io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        await update.effective_message.reply_text(f"that render failed: {e}")


async def cmd_voices(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    vs = await asyncio.to_thread(voice.list_voices)
    await update.effective_message.reply_text(
        "voices: " + (", ".join(vs) if vs else "none - add a clip to ./voices/")
    )


async def cmd_setvoice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    if not ctx.args:
        return await update.effective_message.reply_text(
            f"current voice: {st.voice_name}, engine {st.voice_engine}"
            + (f", pace {st.voice_speed}" if st.voice_speed else "")
            + "\nchange it: /setvoice <name> [fast|expressive] [pace]\n"
            "e.g. /setvoice Randy expressive 0.9  or  /setvoice Jason\n"
            "(names: /voices; pace 0.5-2.0, blank = the voice's own)")
    st.voice_name = ctx.args[0]
    st.voice_engine = "fast"
    st.voice_speed = None
    for extra in ctx.args[1:]:
        low = extra.lower()
        if low in ("fast", "expressive"):
            st.voice_engine = low
        else:
            try:
                v = float(extra)
                if 0.5 <= v <= 2.0:
                    st.voice_speed = v
            except ValueError:
                pass
    await update.effective_message.reply_text(
        f"voice -> {st.voice_name}, engine {st.voice_engine}"
        + (f", pace {st.voice_speed}" if st.voice_speed else ""))


async def cmd_new(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    state_for(update.effective_chat.id).new_session()
    await update.effective_message.reply_text("new conversation started")


async def cmd_transcript(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    rel = str(st.transcript.path).replace("/transcripts", "./transcripts")
    await update.effective_message.reply_text(
        f"logging to {rel}\n({len(st.transcript.read())} entries so far)"
    )


async def cmd_export(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    md = st.transcript.as_markdown()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = Path("/transcripts/exports")
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"telegram-{st.transcript.session_id}-{stamp}.md"
    path.write_text(md, encoding="utf-8")
    buf = io.BytesIO(md.encode("utf-8"))
    buf.name = path.name
    await update.effective_message.reply_document(document=buf, filename=path.name)




async def cmd_usage(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """GPU and token usage as text, with as few dependencies as possible.

    The three fetches are independent on purpose: each one failing costs you
    only its own section, never the whole reply -- "GPU unavailable, here are
    your token counts" beats one blank error, and the moment you most want to
    look at usage is often exactly when something else is broken.
    """
    if not authorised(update):
        return await deny(update)

    await update.effective_chat.send_action(ChatAction.TYPING)

    metrics_text, stats_json, ps_json = "", None, None
    reached = False
    status: int | None = None
    err = ""
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            r = await client.get(LITELLM_METRICS_URL, headers=METRICS_HEADERS)
            # Keep the status code. "connection refused" and "404" both raise,
            # and they need opposite fixes -- one is a dead container, the
            # other is a healthy container whose exporter never loaded.
            status = r.status_code
            r.raise_for_status()
            metrics_text = r.text
            reached = True
        except httpx.HTTPStatusError as e:
            log.warning("/usage: litellm metrics HTTP %s", e.response.status_code)
        except Exception as e:  # noqa: BLE001
            status = None
            err = f"{type(e).__name__}: {e}"
            log.warning("/usage: litellm metrics unreachable: %s", e)

        # Preferred GPU source: real nvidia-smi numbers, via n8n.
        try:
            r = await client.get(N8N_STATS_URL)
            r.raise_for_status()
            stats_json = r.json()
        except Exception as e:  # noqa: BLE001
            log.info("/usage: n8n stats unavailable, will try ollama: %s", e)

        gpu = gpu_from_stats(stats_json)
        if gpu is None:
            try:
                r = await client.get(OLLAMA_PS_URL)
                r.raise_for_status()
                ps_json = r.json()
            except Exception as e:  # noqa: BLE001
                log.warning("/usage: ollama unreachable too: %s", e)
            gpu = gpu_from_ollama(ps_json)

    llm = summarise_llm(metrics_text)
    text = format_text(llm, gpu,
                       diagnose_metrics(reached, metrics_text, llm, status, err))
    # <pre>, not plain text. Telegram turns ANY /word in a message into a
    # tappable command, so a diagnostic that mentions the /metrics ENDPOINT
    # renders as a command that doesn't exist -- you tap it and the bot says
    # "/metrics isn't a command here", which is a dead end of my own making.
    # Inside <pre> nothing is linkified, and the GPU bar lines up as a bonus.
    text = f"<pre>{html.escape(text[:TG_LIMIT])}</pre>"
    # HTML, not markdown: model route names contain underscores and hyphens,
    # which Telegram's markdown parser mangles or rejects outright. <pre> is
    # escaped above, so neither can happen.
    await update.effective_message.reply_text(text, parse_mode="HTML")


async def cmd_health(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Everything at once: GPU, temps, VRAM, disk, RAM, network, uptime, down.

    Problems are printed FIRST. A health screen that opens with a tidy table
    of normal readings buries the one line that matters.
    """
    if not authorised(update):
        return await deny(update)
    await update.effective_chat.send_action(ChatAction.TYPING)
    # Same three-way split as everywhere else: refused, timed out and
    # "answered with an error" have three different fixes, and one message
    # covering all of them sends you looking in the wrong place.
    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            r = await client.get(N8N_STATS_URL)
    except httpx.TimeoutException:
        return await update.effective_message.reply_text(
            "n8n is up but `stats` took over 60s.\n"
            "One of its Docker steps is stuck - check n8n -> Executions."
        )
    except Exception as e:  # noqa: BLE001
        log.warning("/health: n8n unreachable: %s", e)
        return await update.effective_message.reply_text(
            f"Can't reach n8n at all.\n\n{type(e).__name__}: {e}\n\n"
            "  docker compose ps n8n"
        )

    if r.status_code == 404:
        return await update.effective_message.reply_text(
            "n8n is running, but there's no webhook at /webhook/stats.\n"
            "The `stats` workflow isn't published. Open it in n8n and\n"
            "click Publish - importing a workflow doesn't publish it."
        )
    if r.status_code >= 400:
        body = (r.text or "")[:300]
        hint = ""
        if "Unused Respond to Webhook" in body:
            hint = ("\n\nFix: open `stats` -> click the Webhook node ->\n"
                    "set Respond to \"Using 'Respond to Webhook' Node\".")
        return await update.effective_message.reply_text(
            f"`stats` ran and failed ({r.status_code}).\n\n{body}{hint}"
        )

    try:
        data = r.json()
    except Exception:  # noqa: BLE001
        return await update.effective_message.reply_text(
            "`stats` answered, but not with JSON. That usually means the\n"
            "Webhook node's Respond setting is 'Immediately' instead of\n"
            "'Using Respond to Webhook Node'.\n\n" + (r.text or "")[:300]
        )
    # <pre> for the same reason as /usage: plain text lets Telegram invent
    # tappable commands out of any /word in the output.
    await update.effective_message.reply_text(
        f"<pre>{html.escape(format_health(data)[:TG_LIMIT])}</pre>",
        parse_mode="HTML")


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """The recorded history, as a picture on a phone.

        /history            everything, last 24h (text summary)
        /history vram       one metric as a chart
        /history vram 48    one metric, 48 hours
        /history 72         everything, 72 hours

    A named metric gets a PNG because a line you can glance at beats a wall
    of numbers on a phone. The overview stays text -- ten charts in a row is
    worse than ten sparklines.
    """
    if not authorised(update):
        return await deny(update)
    await update.effective_chat.send_action(ChatAction.TYPING)

    metric, hours = "", 24
    for tok in (ctx.args or []):
        if tok.isdigit():
            hours = max(1, min(24 * 31, int(tok)))      # a month is all we keep
        elif not metric:
            metric = tok

    if metric and not resolve_metric(metric):
        return await update.effective_message.reply_text(
            f"Don't chart '{metric}'. Try one of:\n"
            + ", ".join(HISTORY_METRICS)
            + "\n\n/history            everything\n/history vram 48    one metric"
        )

    # Text first, always. It's the fallback if matplotlib or the image upload
    # fails, and it carries the outage list, which the chart only shades.
    try:
        async with httpx.AsyncClient(timeout=45.0, follow_redirects=True) as client:
            r = await client.get(f"{METRICS_URL}/history.txt",
                                 params={"hours": hours, "metric": metric})
            r.raise_for_status()
            text = r.text
    except Exception as e:  # noqa: BLE001
        log.warning("/history: recorder unreachable: %s", e)
        return await update.effective_message.reply_text(
            f"Can't reach the recorder.\n\n{type(e).__name__}: {e}\n\n"
            "  docker compose ps metrics"
        )

    if not metric:
        # Monospace so the sparklines line up. Telegram's proportional font
        # turns them into a ragged mess otherwise.
        return await update.effective_message.reply_text(
            f"<pre>{html.escape(text[:TG_LIMIT])}</pre>", parse_mode="HTML")

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            r = await client.get(f"{METRICS_URL}/history.png",
                                 params={"hours": hours, "metric": metric})
            r.raise_for_status()
        await update.effective_message.reply_photo(
            photo=io.BytesIO(r.content),
            caption=text[:1000])
    except Exception as e:  # noqa: BLE001
        # The numbers are the point; the picture is a nicety. Losing the
        # renderer shouldn't mean losing the answer.
        log.warning("/history: chart failed, sending text: %s", e)
        await update.effective_message.reply_text(
            f"<pre>{html.escape(text[:TG_LIMIT])}</pre>", parse_mode="HTML")


# ---------------------------------------------------------------------------
#  Printing
# ---------------------------------------------------------------------------
# Send the bot a document from anywhere and it comes out of the printer at
# home. The flow is deliberately two-step:
#
#   1. the file lands in pending/ and you get told what it is
#   2. only when you tap Approve does it move to approved/, which is the
#      only folder the Windows watcher will print from
#
# pending/ is never printed from, so a file that arrives and is never
# approved is inert -- it just sits there.


def _pdf_pages(data: bytes) -> int | None:
    """Page count, best effort, without a PDF library.

    Counts `/Type /Page` objects, which is what a page dictionary must
    declare, while excluding `/Type /Pages` (the tree NODES, of which there
    are far fewer than pages -- counting those would under-report badly).

    Best effort on purpose: a compressed object stream hides these, and then
    this returns None. "Couldn't read the page count" is an honest answer and
    the confirmation still shows name and size; guessing a number you would
    then act on would be worse.
    """
    try:
        n = len(re.findall(rb"/Type\s*/Page[^s]", data))
        return n if 0 < n < 10000 else None
    except Exception:  # noqa: BLE001
        return None


async def on_document(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """A file arrived. Vet it, stash it, and ask before spending any paper."""
    if not authorised(update):
        return await deny(update)

    msg = update.effective_message
    doc = msg.document
    if doc is None:
        return

    name = safe_name(doc.file_name or "document")
    size = doc.file_size or 0

    why = reject_reason(name, size)
    if why:
        return await msg.reply_text(why)

    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        tg_file = await doc.get_file()
        data = bytes(await tg_file.download_as_bytearray())
    except Exception as e:  # noqa: BLE001
        log.warning("print: download failed: %s", e)
        return await msg.reply_text(
            f"Couldn't download that from Telegram.\n\n{type(e).__name__}: {e}"
        )

    # Re-check the REAL size. file_size is what Telegram claims; this is what
    # actually arrived, and it is the one worth enforcing a limit on.
    why = reject_reason(name, len(data))
    if why:
        return await msg.reply_text(why)

    try:
        PRINT_PENDING.mkdir(parents=True, exist_ok=True)
        PRINT_APPROVED.mkdir(parents=True, exist_ok=True)
    except Exception as e:  # noqa: BLE001
        return await msg.reply_text(
            f"Can't write to the print queue ({type(e).__name__}: {e}).\n"
            "Is the transcripts folder mounted?"
        )

    token = secrets.token_urlsafe(8)
    path = PRINT_PENDING / f"{token}__{name}"
    try:
        path.write_bytes(data)
    except Exception as e:  # noqa: BLE001
        return await msg.reply_text(f"Couldn't save it: {type(e).__name__}: {e}")

    pages = _pdf_pages(data) if name.lower().endswith(".pdf") else None
    body = describe_job(name, len(data), pages)

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("Print it", callback_data=f"print:ok:{token}"),
        InlineKeyboardButton("Cancel", callback_data=f"print:no:{token}"),
    ]])
    await msg.reply_text(f"Ready to print:\n\n{body}", reply_markup=kb)


async def on_print_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Approve or cancel. Moving the file IS the approval."""
    q = update.callback_query
    if not authorised(update):
        await q.answer("not allowed", show_alert=True)
        return
    await q.answer()

    try:
        _, verb, token = (q.data or "").split(":", 2)
    except ValueError:
        return await q.edit_message_text("That button is from an older message.")

    # Match on the token only, and only inside pending/. The token comes back
    # from Telegram, so it is treated as untrusted input: it is used to FIND a
    # file we already wrote, never to build a path to open.
    matches = [p for p in PRINT_PENDING.glob(f"{token}__*")] if PRINT_PENDING.exists() else []
    if not matches:
        return await q.edit_message_text(
            "That file is gone - already printed, cancelled, or the queue was cleared."
        )
    src = matches[0]
    shown = src.name.split("__", 1)[-1]

    if verb == "no":
        try:
            src.unlink()
        except Exception:  # noqa: BLE001
            pass
        return await q.edit_message_text(f"Cancelled. Deleted {shown}.")

    try:
        src.replace(PRINT_APPROVED / shown)
    except Exception as e:  # noqa: BLE001
        log.warning("print: approve failed: %s", e)
        return await q.edit_message_text(
            f"Couldn't move it to the print queue.\n\n{type(e).__name__}: {e}"
        )

    # Say what actually happens, including who says it and when.
    #
    # The old wording promised a confirmation "when it has actually printed"
    # and named the wrong interval. The confirmation is real -- printq follows
    # the CUPS job to completion -- but it is DELIVERED by the printwatch
    # workflow in n8n, which polls every 5 minutes and has to be activated.
    # A promise with no stated deadline turns a 4-minute wait into "it's
    # broken", and sends you looking at the printer instead of at n8n.
    await q.edit_message_text(
        f"Sent to the printer: {shown}\n"
        "Picked up within 15 seconds, and confirmed here once the\n"
        "printer reports the job finished - usually within 5 minutes.\n"
        "\n"
        "No message after that? The confirmation comes from the\n"
        "printwatch workflow, so check it is Active in n8n.\n"
        "/stats printer shows the outcome either way."
    )


async def cmd_print(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/print - how the printer is doing, and how to send it something."""
    if not authorised(update):
        return await deny(update)

    status = None
    try:
        raw = PRINTER_STATUS.read_text(encoding="utf-8-sig")
        status = json.loads(raw)
        age = (datetime.now(timezone.utc).timestamp()
               - PRINTER_STATUS.stat().st_mtime) / 60.0
        status["age_min"] = round(age, 1)
    except FileNotFoundError:
        return await update.effective_message.reply_text(
            "The print server isn't running yet.\n\n"
            "On the PC:\n"
            "  printserver.cmd -Install\n"
            "  printserver.cmd -EnableLog   (as Administrator, once)\n\n"
            "Then just send me a PDF and I'll ask before printing it."
        )
    except Exception as e:  # noqa: BLE001
        return await update.effective_message.reply_text(
            f"Couldn't read the printer status ({type(e).__name__}: {e})."
        )

    text = format_printer_status(status)
    if not status.get("print_log_enabled", True):
        text += ("\n\nWindows' print log is off, so 'who printed what' will\n"
                 "stay empty. On the PC, as Administrator, once:\n"
                 "  printserver.cmd -EnableLog")
    text += "\n\nSend me any PDF, photo or document to print it."
    await update.effective_message.reply_text(
        f"<pre>{html.escape(text[:TG_LIMIT])}</pre>", parse_mode="HTML")


STAT_SECTIONS = [
    [("Machine", "machine"), ("GPU", "gpu")],
    [("Containers", "containers"), ("Disks", "disks")],
    [("Network", "network"), ("Models", "models")],
    [("Printer", "printer"), ("Last 24h", "history")],
]


def _stats_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(lbl, callback_data=f"stat:{key}")
             for lbl, key in row] for row in STAT_SECTIONS]
    rows.append([InlineKeyboardButton("Everything", callback_data="stat:__full__")])
    return InlineKeyboardMarkup(rows)


async def _fetch_stats(**params) -> str:
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
        r = await client.get(f"{METRICS_URL}/stats.txt", params=params)
        r.raise_for_status()
        return r.text


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats        the glance screen, with buttons into each section
       /stats full   every section, paged
       /stats gpu    one section

    Two depths rather than one compromise: a single screen serving both
    readers ends up padded until you stop reading it, or trimmed until the
    thing you needed is the thing that got cut.
    """
    if not authorised(update):
        return await deny(update)
    await update.effective_chat.send_action(ChatAction.TYPING)

    arg = (ctx.args[0].lower() if ctx.args else "")

    try:
        if arg in ("full", "all", "everything"):
            return await _send_full(update)
        if arg:
            text = await _fetch_stats(section=arg)
            return await update.effective_message.reply_text(
                f"<pre>{html.escape(text[:TG_LIMIT])}</pre>", parse_mode="HTML")
        text = await _fetch_stats(mode="simple")
    except Exception as e:  # noqa: BLE001
        log.warning("/stats: %s", e)
        return await update.effective_message.reply_text(
            f"Can't reach the recorder.\n\n{type(e).__name__}: {e}\n\n"
            "  docker compose ps metrics")

    await update.effective_message.reply_text(
        f"<pre>{html.escape(text[:TG_LIMIT])}</pre>",
        parse_mode="HTML", reply_markup=_stats_keyboard())


async def _send_full(update: Update) -> None:
    """Every section, one Telegram message per page.

    Paged rather than trimmed: Telegram hard-rejects anything over 4096
    characters, and silently cutting a detail screen defeats the point of
    asking for detail.
    """
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
        n = (await client.get(f"{METRICS_URL}/stats.pages")).json().get("pages", 1)
        for i in range(min(n, 6)):     # a sane ceiling; 6 pages is ~21k chars
            r = await client.get(f"{METRICS_URL}/stats.txt",
                                 params={"mode": "full", "page": i})
            await update.effective_message.reply_text(
                f"<pre>{html.escape(r.text[:TG_LIMIT])}</pre>", parse_mode="HTML")


async def on_stats_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not authorised(update):
        await q.answer("not allowed", show_alert=True)
        return
    await q.answer()
    key = (q.data or "").split(":", 1)[-1]
    try:
        if key == "__full__":
            return await _send_full(update)
        text = await _fetch_stats(section=key)
    except Exception as e:  # noqa: BLE001
        return await q.edit_message_text(f"Couldn't fetch that: {type(e).__name__}: {e}")
    # A NEW message rather than editing: the buttons stay usable, so you can
    # tap through several sections without going back for the menu each time.
    await q.message.reply_text(
        f"<pre>{html.escape(text[:TG_LIMIT])}</pre>",
        parse_mode="HTML", reply_markup=_stats_keyboard())


# -- Mama's tickets -------------------------------------------------------
#
# /tickets lists what she has open right now, each with two buttons: close
# it (documents, publishes the FAQ entry, syncs to OTOBO, tells her it's
# sorted -- the exact same pipeline /admin/ticket/{tid}/close runs) or reopen
# it. This is Mom's own ticket record talking; OTOBO is a mirror of it, not
# the other way round -- see the long note at the top of Mom/app/otobo.py.


async def _ticket_api(method: str, path: str, **kw) -> dict:
    if not MOM_TICKET_API_SECRET_KEY:
        raise RuntimeError("MOM_TICKET_API_SECRET_KEY is not set")
    headers = {"X-Ticket-Api-Secret": MOM_TICKET_API_SECRET_KEY}
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.request(method, f"{MOM_URL}{path}", headers=headers, **kw)
    r.raise_for_status()
    return r.json()


# Drafted fixes, waiting to be sent or discarded. In memory only -- same
# tradeoff every other per-chat thing in this file already makes (ChatState
# itself, PRINT_PENDING before it moves to disk): a bot restart loses an
# unconfirmed draft, which costs pressing "Ask my LLM" again, not data.
DRAFTS: dict[int, str] = {}


def _tickets_keyboard(tickets: list) -> InlineKeyboardMarkup:
    """Two rows per ticket: work on it, and the three statuses she can be
    in -- Open / Pending / Closed. Tapping the one already active is
    harmless (the route answers "already X" and the button just says so),
    which is simpler than hiding buttons and getting the hiding wrong."""
    rows = []
    for t in tickets:
        rows.append([
            InlineKeyboardButton(f"🛠 Work on {t['ticket_no']}",
                                 callback_data=f"tkt:work:{t['id']}"),
        ])
        rows.append([
            InlineKeyboardButton("○ Open", callback_data=f"tkt:reopen:{t['id']}"),
            InlineKeyboardButton("⏳ Pending", callback_data=f"tkt:pending:{t['id']}"),
            InlineKeyboardButton("✓ Close", callback_data=f"tkt:close:{t['id']}"),
        ])
    return InlineKeyboardMarkup(rows)


def _draft_keyboard(tid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("Send to Mama", callback_data=f"tkt:send:{tid}"),
        InlineKeyboardButton("Send + close", callback_data=f"tkt:sendclose:{tid}"),
    ], [
        InlineKeyboardButton("Discard", callback_data=f"tkt:discard:{tid}"),
    ]])


def _format_tickets(tickets: list) -> str:
    if not tickets:
        return "Nothing open right now."
    lines = ["Open tickets:\n"]
    for t in tickets:
        flag = (" ⚠ escalated" if t["status"] == "escalated"
               else " ⏳ pending" if t["status"] == "pending" else "")
        lines.append(f"{t['ticket_no']} — {t['title']}{flag}\n"
                     f"  {t['category'] or 'uncategorised'} · {t['username']}")
    return "\n".join(lines)


async def cmd_tickets(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    try:
        data = await _ticket_api("GET", "/internal/tickets/open")
    except Exception as e:  # noqa: BLE001
        return await update.effective_message.reply_text(
            f"Couldn't reach Mom ({type(e).__name__}: {e}).\n\n"
            "  docker compose ps mom")
    tickets = data.get("tickets") or []
    await update.effective_message.reply_text(
        _format_tickets(tickets)[:TG_LIMIT],
        reply_markup=_tickets_keyboard(tickets) if tickets else None)


def _work_keyboard(tid: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🤖 Ask my LLM", callback_data=f"tkt:llm:{tid}"),
        InlineKeyboardButton("🔗 Paste a link", callback_data=f"tkt:link:{tid}"),
    ]])


async def _run_suggest(update: Update, tid: str, link: str = "") -> None:
    """Ask Mom to draft a fix -- from `link` if given, from the ticket's own
    transcript otherwise -- and show it with Send/Discard buttons. Shared by
    the "🤖 Ask my LLM" button and by on_text() picking up a pasted link."""
    msg = update.effective_message
    thinking = await msg.reply_text(
        "Reading that and working it out..." if link else "Working it out...")
    try:
        result = await _ticket_api("POST", f"/internal/tickets/{tid}/suggest",
                                   json={"link": link} if link else {})
    except Exception as e:  # noqa: BLE001
        return await thinking.edit_text(f"Couldn't reach Mom: {type(e).__name__}: {e}")

    if not result.get("ok"):
        return await thinking.edit_text(f"Couldn't draft one: {result.get('why', 'unknown reason')}")

    text = result["text"]
    DRAFTS[int(tid)] = text
    await thinking.edit_text(
        f"Draft fix:\n\n{text}\n\n"
        "Nothing has been sent to her yet.",
        reply_markup=_draft_keyboard(tid))


async def on_ticket_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if not authorised(update):
        await q.answer("not allowed", show_alert=True)
        return
    await q.answer()

    try:
        _, verb, tid = (q.data or "").split(":", 2)
    except ValueError:
        return await q.edit_message_text("That button is from an older message.")

    # -- the "work on it" sub-menu: neither of these touches the ticket yet --
    if verb == "work":
        return await q.edit_message_text(
            "How do you want to work this one?", reply_markup=_work_keyboard(tid))

    if verb == "llm":
        return await _run_suggest(update, tid)

    if verb == "link":
        state_for(update.effective_chat.id).awaiting_link_for = int(tid)
        return await q.edit_message_text(
            "Send me the link. I'll pull a fix out of it and show you before "
            "anything reaches her.")

    # -- a drafted fix, sent, sent-and-closed, or thrown away --
    also_close = False
    if verb in ("send", "sendclose", "discard"):
        text = DRAFTS.pop(int(tid), "")
        if verb == "discard":
            return await q.edit_message_text("Discarded. Nothing was sent to her.")
        if not text:
            return await q.edit_message_text(
                "That draft is gone (bot restarted, or it already went out) "
                "-- ask the LLM again.")
        try:
            result = await _ticket_api("POST", f"/internal/tickets/{tid}/apply-fix",
                                       json={"text": text})
        except Exception as e:  # noqa: BLE001
            return await q.edit_message_text(f"Couldn't reach Mom: {type(e).__name__}: {e}")
        if not result.get("ok"):
            return await q.edit_message_text(f"Didn't send: {result.get('why', 'unknown reason')}")
        if verb == "send":
            return await q.edit_message_text(
                "Sent. She'll see it next time she opens /mama.")
        also_close = True   # sendclose: fall through to the close call below
        verb = "close"

    # -- close / reopen / pending --
    try:
        result = await _ticket_api("POST", f"/internal/tickets/{tid}/{verb}")
    except Exception as e:  # noqa: BLE001
        return await q.edit_message_text(f"Couldn't reach Mom: {type(e).__name__}: {e}")

    if not result.get("ok"):
        return await q.edit_message_text(f"Didn't do it: {result.get('why', 'unknown reason')}")

    title = result.get("title", "")
    suffix = f" — {title}" if title else ""
    if verb == "close":
        prefix = "Sent and closed" if also_close else "Closed"
        await q.edit_message_text(f"{prefix}{suffix}. She'll see it marked sorted in /mama.")
    elif verb == "pending":
        await q.edit_message_text(f"Marked pending{suffix}.")
    else:
        await q.edit_message_text(f"Reopened{suffix}.")


async def cmd_models(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """/models          - free check: is every configured model on disk?
       /models check X  - live check: actually call route X.

    The split matters. The free check loads nothing. The live check makes
    LiteLLM send a real request, which forces Ollama to load that model into
    VRAM -- so it's one route at a time, on request, never on a timer.
    """
    if not authorised(update):
        return await deny(update)

    args = ctx.args or []
    await update.effective_chat.send_action(ChatAction.TYPING)

    # --- live check for one route -------------------------------------------
    if args and args[0].lower() == "check":
        if len(args) < 2:
            return await update.effective_message.reply_text(
                "which route? e.g.  /models check coding")
        route = args[1]
        try:
            async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
                r = await client.get(f"{LITELLM_BASE}/health",
                                     params={"model": route},
                                     headers=METRICS_HEADERS)
                r.raise_for_status()
                res = parse_health(r.json(), route)
        except Exception as e:  # noqa: BLE001
            return await update.effective_message.reply_text(
                f"couldn't check {route}: {e}")

        if res["live"] is True:
            return await update.effective_message.reply_text(f"{route}: working")
        if res["live"] is False:
            return await update.effective_message.reply_text(
                f"{route}: FAILING\n{res['error']}")
        return await update.effective_message.reply_text(
            f"{route}: no clear answer ({res['error']})")

    # --- free inventory check -----------------------------------------------
    mi, tags = None, None
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        try:
            r = await client.get(f"{LITELLM_BASE}/model/info", headers=METRICS_HEADERS)
            r.raise_for_status()
            mi = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("/models: litellm model/info failed: %s", e)
        try:
            r = await client.get(OLLAMA_TAGS_URL)
            r.raise_for_status()
            tags = r.json()
        except Exception as e:  # noqa: BLE001
            log.warning("/models: ollama tags failed: %s", e)

    installed = parse_installed(tags)
    rows = reconcile(parse_routes(mi), installed)
    await update.effective_message.reply_text(
        format_inventory_text(rows, len(installed)))



async def cmd_status(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    jobs = await asyncio.to_thread(router.available_jobs)
    tts_ok = await asyncio.to_thread(voice.available)
    await update.effective_message.reply_text(
        f"router: ok ({len(jobs)} routes)\n"
        f"tts: {'ok' if tts_ok else 'offline'}\n"
        f"stt: {'configured' if stt.enabled else 'not configured'}"
    )


# -- messages -----------------------------------------------------------------


async def answer(update: Update, st: ChatState, text: str) -> None:
    await update.effective_chat.send_action(ChatAction.TYPING)

    if st.tools_on:
        # The agent path: model -> tools -> model, in the same workspace Hermes
        # uses. Runs in a thread because the loop is blocking and can take a
        # while; without this the bot stops answering other messages.
        st.sync_agent()
        st.tool_log = []
        try:
            reply = await asyncio.to_thread(st.agent.send, text)
        except Exception as e:  # noqa: BLE001
            log.exception("agent failed")
            return await update.effective_message.reply_text(f"agent error: {e}")

        if st.tool_log:
            # Say what it touched. On a phone you can't watch a terminal, and an
            # agent that edits your files without saying so is unnerving.
            steps = "\n".join(f"- {s}" for s in st.tool_log[:8])
            more = f"\n- (+{len(st.tool_log) - 8} more)" if len(st.tool_log) > 8 else ""
            await update.effective_message.reply_text(f"{steps}{more}")

        st.trim()
    else:
        # Plain chat, no tools. Faster, and what you want for a question that
        # has nothing to do with files.
        job = pick_job(text, st.job) if st.auto else st.job
        st.transcript.log("user", text, job=job)
        st.history.append({"role": "user", "content": text})
        try:
            resp = await asyncio.to_thread(router.chat, st.history, job=job)
            reply = (resp.choices[0].message.content or "").strip()
        except Exception as e:  # noqa: BLE001
            log.exception("router call failed")
            return await update.effective_message.reply_text(f"router error: {e}")
        st.history.append({"role": "assistant", "content": reply})
        st.transcript.log("assistant", reply, job=job)
        st.trim()

    await send_long(update, reply or "(no reply)")
    if st.voice_replies:
        await update.effective_chat.send_action(ChatAction.RECORD_VOICE)
        await send_voice(update, reply, st)


# --- career mode -------------------------------------------------------------

async def career_ask(update: Update, text: str, st: "ChatState") -> bool:
    """Send one question to the career portal and reply with its answer.

    Returns True if it handled the message. False means "not in career
    mode" and the normal path should run.

    A failure here does NOT fall back to the house agent. That sounds
    helpful and is not: she would get a confident answer from something
    that has never read her awards catalog, with no sign that anything
    went wrong. Better to say the portal is not answering.
    """
    if not st.career:
        return False
    uid = update.effective_user.id if update.effective_user else 0
    email = ZEE_USERS.get(uid)
    if not email:
        await update.effective_message.reply_text(
            "Career mode has no address for this Telegram account, so there "
            "is nowhere to file it. Add it to ZEE_TELEGRAM_USERS as "
            f"{uid}=her-email, then restart the bot.")
        return True
    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        r = await asyncio.to_thread(
            lambda: httpx.post(
                f"{ZEE_URL}/api/ask",
                headers={"X-Auth-Email": email},
                data={"text": text, "session": "telegram"},
                timeout=300))
    except Exception as e:  # noqa: BLE001
        await update.effective_message.reply_text(
            f"The career portal did not answer: {e}")
        return True
    if r.status_code >= 400:
        await update.effective_message.reply_text(
            f"The career portal refused that ({r.status_code}). "
            "Nothing was saved.")
        return True
    d = r.json()
    answer = (d.get("answer") or "").strip()
    if not answer:
        await update.effective_message.reply_text("No answer came back.")
        return True
    st.transcript.log("assistant", answer, job="career")
    # Told in two parts on purpose: the words, and where they went. She
    # should never have to wonder whether a thought was kept.
    await update.effective_message.reply_text(answer[:3800])
    await update.effective_message.reply_text(
        "\u2713 kept in your portal \u2014 it will be in the running "
        "document and the portfolio drafts.")
    if st.voice_replies:
        try:
            await send_voice(update, answer, st)
        except Exception:  # noqa: BLE001
            pass          # a silent answer is still an answer
    return True


async def cmd_career(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Everything said here goes to the career portal until /house."""
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    st.career = True
    uid = update.effective_user.id if update.effective_user else 0
    who = ZEE_USERS.get(uid)
    await update.effective_message.reply_text(
        "Career mode on. Talk or type and it goes to your career portal --"
        " it knows your awards catalog, your writing rules and your record,"
        " and it keeps every exchange.\n\n"
        + (f"Filing as {who}." if who
           else "WARNING: this Telegram account is not mapped to a portal "
                "address yet, so nothing can be filed.")
        + "\n\n/house puts it back to the ordinary assistant.")


async def cmd_house(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)
    st.career = False
    await update.effective_message.reply_text(
        "Back to the ordinary assistant.")


async def on_text(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)

    # A ticket's "🔗 Paste a link" button was pressed, and this is the very
    # next message -- consumed here, before the normal agent turn, so it
    # never has to be a whole separate flow with its own state machine.
    if st.awaiting_link_for is not None:
        tid = st.awaiting_link_for
        st.awaiting_link_for = None
        return await _run_suggest(update, tid, link=(update.effective_message.text or "").strip())

    typed = update.effective_message.text or ""
    # Career mode takes typed questions as well as spoken ones -- she
    # will not always be somewhere she can talk.
    if await career_ask(update, typed, st):
        return
    await answer(update, st, typed)


async def on_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not authorised(update):
        return await deny(update)
    st = state_for(update.effective_chat.id)

    if not stt.enabled:
        return await update.effective_message.reply_text(
            "Voice input isn't configured. Set STT_API_KEY in .env "
            "(any OpenAI-compatible transcription endpoint works), or just type."
        )

    msg = update.effective_message
    tg_file = await (msg.voice or msg.audio).get_file()
    audio = bytes(await tg_file.download_as_bytearray())

    await update.effective_chat.send_action(ChatAction.TYPING)
    try:
        text = await asyncio.to_thread(stt.transcribe, audio, "note.ogg")
    except Exception as e:  # noqa: BLE001
        log.exception("stt failed")
        return await msg.reply_text(f"transcription failed: {e}")

    if not text:
        return await msg.reply_text("couldn't make out any speech there")

    # The transcript is the deliverable here - always show and store it.
    st.transcript.log("user", text, job=st.job, source="voice")
    await msg.reply_text(
        f"<i>heard:</i> {html.escape(text)}", parse_mode=ParseMode.HTML
    )
    st.history.append({"role": "user", "content": text})

    # Career mode takes the whole message, spoken or typed.
    if await career_ask(update, text, st):
        return

    job = pick_job(text, st.job) if st.auto else st.job
    try:
        resp = await asyncio.to_thread(router.chat, st.history, job=job)
        reply = (resp.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        return await msg.reply_text(f"router error: {e}")

    st.history.append({"role": "assistant", "content": reply})
    st.transcript.log("assistant", reply, job=job)
    st.trim()
    await send_long(update, reply)
    if st.voice_replies:
        await send_voice(update, reply, st)


async def on_unknown(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """A command that no longer exists should say so.

    /graph was removed when Netdata took over charting. Without this, typing
    it produces silence, which reads exactly like a crash.
    """
    if not authorised(update):
        return await deny(update)
    cmd = (update.effective_message.text or "").split()[0]
    extra = ""
    if cmd.startswith("/graph"):
        extra = ("\n\nCharts moved to Netdata: http://localhost:19999\n"
                 "It has bandwidth, GPU, VRAM, temps and per-container history.")
    await update.effective_message.reply_text(
        f"{cmd} isn't a command here.{extra}\n\n/help lists what is.")


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Tell the person in the chat, not just the log file.

    This used to log and stop there, which meant a command that threw
    produced ABSOLUTE SILENCE on the phone -- indistinguishable from the bot
    being asleep, the message not sending, or the command not existing. The
    only way to find out was to go to the PC and read container logs, which
    defeats the point of having it on your phone at all.

    Wrapped in its own try/except because an error handler that raises is
    worse than no error handler: python-telegram-bot would then log a second
    error about the first, and you still get nothing.
    """
    log.error("handler error", exc_info=ctx.error)
    try:
        chat = getattr(getattr(update, "effective_chat", None), "id", None)
        if chat is None:
            return
        # Don't leak internals to whoever happened to message the bot.
        user = getattr(getattr(update, "effective_user", None), "id", None)
        if ALLOWED and user not in ALLOWED:
            return
        err = f"{type(ctx.error).__name__}: {ctx.error}"
        await ctx.bot.send_message(
            chat_id=chat,
            text=("That command failed.\n\n"
                  f"{err[:500]}\n\n"
                  "Full trace:  docker compose logs telegram --tail 40"),
        )
    except Exception:  # noqa: BLE001
        log.exception("could not report the error to the chat")


async def post_init(app: Application) -> None:
    # Say WHICH build is running, in the log, on every start.
    #
    # "Feature X works in Hermes but not Telegram" is almost always one thing:
    # the image was rebuilt but the container was never recreated, so old code
    # is still running. That is invisible from the chat -- the bot answers
    # normally, just without the new commands. This line makes it a five second
    # check instead of a guess:
    #
    #     docker compose logs telegram | findstr "commands registered"
    try:
        import hashlib
        stamp = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:8]
    except Exception:  # noqa: BLE001
        stamp = "unknown"
    names = sorted(
        h.commands and list(h.commands)[0] or "?"
        for grp in app.handlers.values() for h in grp
        if isinstance(h, CommandHandler)
    )
    log.info("bot.py build %s - %d commands registered: %s",
             stamp, len(names), " ".join(names))

    await app.bot.set_my_commands(
        [
            BotCommand("tools", "agent mode on/off"),
            BotCommand("unlock", "allow writing files and running commands"),
            BotCommand("lock", "back to reading only"),
            BotCommand("jobs", "list model routes"),
            BotCommand("job", "switch model route"),
            BotCommand("auto", "toggle automatic routing"),
            BotCommand("voice", "toggle spoken replies"),
            BotCommand("voices", "list cloned voices"),
            BotCommand("setvoice", "voice + engine + pace for replies"),
            BotCommand("new", "start a fresh conversation"),
            BotCommand("transcript", "where this chat is logged"),
            BotCommand("export", "download this conversation"),
            BotCommand("status", "stack health"),
            BotCommand("health", "everything: GPU, temps, disk, RAM, network, down"),
            BotCommand("menu", "buttons for everything worth looking at"),
            BotCommand("stats", "right now, at a glance - /stats full for everything"),
            BotCommand("print", "printer status - or just send me a file to print"),
            BotCommand("trends", "last 24h: what's rising, what broke, as a chart"),
            BotCommand("history", "same as /trends"),
            BotCommand("usage", "GPU and token usage, as text"),
            BotCommand("models", "are all configured models actually installed?"),
            BotCommand("help", "show commands"),
        ]
    )
    log.info("bridge up. allowlist=%s default_job=%s", sorted(ALLOWED), DEFAULT_JOB)


def main() -> None:
    if not TOKEN:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Get one from @BotFather on Telegram "
            "and put it in .env, then: docker compose up -d telegram"
        )
    if not ALLOWED:
        raise SystemExit(
            "TELEGRAM_ALLOWED_USER_IDS is empty. Set it in .env - message "
            "@userinfobot on Telegram to get your numeric id. Refusing to start "
            "a bot that would answer anyone who finds it."
        )

    app = Application.builder().token(TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("tools", cmd_tools))
    app.add_handler(CommandHandler("unlock", cmd_unlock))
    app.add_handler(CommandHandler("lock", cmd_lock))
    app.add_handler(CommandHandler("jobs", cmd_jobs))
    app.add_handler(CommandHandler("job", cmd_job))
    app.add_handler(CommandHandler("auto", cmd_auto))
    app.add_handler(CommandHandler("voice", cmd_voice))
    app.add_handler(CommandHandler("voices", cmd_voices))
    app.add_handler(CommandHandler("sample", cmd_sample))
    app.add_handler(CommandHandler("setvoice", cmd_setvoice))
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("transcript", cmd_transcript))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("status", cmd_status))
    # "metrics" is an alias because it is what you naturally type after
    # reading a message about the metrics endpoint. Cheaper to answer than
    # to explain.
    app.add_handler(CommandHandler(["usage", "metrics"], cmd_usage))
    app.add_handler(CommandHandler("models", cmd_models))
    app.add_handler(CommandHandler("health", cmd_health))
    # Two names for one command. "trends" is what you reach for when you want
    # to know if something is getting worse; "history" is what you reach for
    # when you want to know what happened. Same answer, so serve both rather
    # than making you remember which word this stack chose.
    app.add_handler(CommandHandler(["history", "trends"], cmd_history))
    app.add_handler(CommandHandler("print", cmd_print))
    app.add_handler(CommandHandler("menu", cmd_menu))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CallbackQueryHandler(on_stats_button, pattern=r"^stat:"))
    app.add_handler(CommandHandler("tickets", cmd_tickets))
    app.add_handler(CallbackQueryHandler(on_ticket_button, pattern=r"^tkt:"))
    app.add_handler(CallbackQueryHandler(on_menu_button, pattern=r"^menu:"))
    # Any document sent to the bot is a print request. Registered BEFORE the
    # catch-alls below, like every other real handler.
    app.add_handler(MessageHandler(filters.Document.ALL, on_document))
    app.add_handler(CallbackQueryHandler(on_print_button, pattern=r"^print:"))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voice))
    app.add_handler(CommandHandler("career", cmd_career))
    app.add_handler(CommandHandler("house", cmd_house))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    # MUST come after every real CommandHandler, or it swallows them all.
    app.add_handler(MessageHandler(filters.COMMAND, on_unknown))
    app.add_error_handler(on_error)

    # drop_pending_updates: don't replay everything sent while the stack was down
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
