"""
Tea Miss Dee — the whole phone line, on your machine.

Twilio provides the number and the audio pipe. Nothing else leaves the house:
transcription, the model, and the voice all run on your 4070.

    caller ──▶ Twilio ──▶ Cloudflare Tunnel ──▶ this service
                                                    │
                          ┌─────────────────────────┼────────────────────┐
                          ▼                         ▼                    ▼
                    stt (Whisper)          litellm -> qwen2.5:7b    voice (Kokoro)
                                                    │
                                                    ▼
                                              booking -> Google Calendar

Two endpoints:
    POST /twiml/incoming   Twilio asks what to do with a call; we say
                           "stream it to me"
    WS   /media            the call itself
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import numpy as np
from fastapi import (
    FastAPI,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse, PlainTextResponse

from . import sms as sms_mod
from .callers import CallerStore, greeting_for, normalise, summary_for
from .clients import ClientStore, client_summary
from .conversation import Conversation, Tuning
from .outbound import OutboundStore, due_messages
from .phi import PROMPT_RULES as HEALTH_RULES
from .places import Places
from .ratings import RatingStore
from .twilio_auth import signature_ok, stream_secret

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("telephony")

PUBLIC_HOST = os.environ.get("PUBLIC_HOST", "").replace("https://", "").replace("http://", "").strip("/")
STT_URL = os.environ.get("STT_BASE_URL", "http://stt:8092").rstrip("/")
VOICE_URL = os.environ.get("VOICE_BASE_URL", "http://voice:8091").rstrip("/")
ROUTER_URL = os.environ.get("ROUTER_BASE_URL", "http://litellm:4000/v1").rstrip("/")
ROUTER_KEY = os.environ.get("ROUTER_API_KEY", "")
PHONE_JOB = os.environ.get("PHONE_JOB", "phone")
BOOKING_URL = os.environ.get("BOOKING_BASE_URL", "http://booking:8080").rstrip("/")
BOOKING_KEY = os.environ.get("BOOKING_API_KEY", "")
BUSINESS_NAME = os.environ.get("BUSINESS_NAME", "Tea Miss Dee")
# Whose voice answers the phone. A clip at voices/<this>.wav, and
# VOICE_ENGINE must be a cloning engine (luxtts or chatterbox) -- kokoro
# has only preset voices and cannot clone.
PHONE_VOICE = os.environ.get("PHONE_VOICE", os.environ.get("VOICE_DEFAULT", "dee"))
STATE_DIR = os.environ.get("TELEPHONY_STATE_DIR", "/state")
TRANSCRIPT_DIR = os.environ.get("TRANSCRIPT_DIR", "/transcripts")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
# Guards /media. Derived from the auth token unless overridden, so there is
# nothing extra to configure and nothing extra to forget. See twilio_auth.py.
STREAM_SECRET = stream_secret(
    TWILIO_AUTH_TOKEN, os.environ.get("PHONE_STREAM_SECRET", "")
)
BOOKING_LINK = os.environ.get("BOOKING_LINK", "")
REVIEW_LINK = os.environ.get("REVIEW_LINK", "")
TIP_LINK = os.environ.get("TIP_LINK", "")
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")
BUSINESS_TZ = os.environ.get("BUSINESS_TZ", "America/Los_Angeles")
SMS_ENABLED = os.environ.get("SMS_ENABLED", "false").lower() == "true"

app = FastAPI(title="tea miss dee telephony", version="1.0")
callers = CallerStore(STATE_DIR)
clients = ClientStore(STATE_DIR)
ratings = RatingStore(STATE_DIR)

# ONE instance for the whole process, loaded at import. The directory is a few
# dozen dicts; holding it in memory is what makes "where could she go?" answer
# in the next breath rather than after a lookup. It re-reads itself when any of
# its three files changes, so nothing has to be restarted to add a place.
PLACES = Places()

# HOW MANY CALLS ARE LIVE RIGHT NOW.
#
# Exists so background work elsewhere in the stack can keep off the GPU while
# somebody is actually on the phone. Ollama answers one request at a time per
# model, so a long import job started mid-call puts a real caller behind it and
# they hear silence. A single integer is the whole coordination mechanism --
# anything more would be a scheduler, and this is one GPU and one phone line.
ACTIVE_CALLS = 0
outbound = OutboundStore(STATE_DIR)

SYSTEM_PROMPT = """You are the telephone assistant for {business}, a companionship \
service in North Las Vegas run by a woman everyone calls Miss Dee.

WHAT THIS BUSINESS ACTUALLY IS
Miss Dee sits with people. She keeps them company. She is there so someone \
isn't alone — for a couple of hours, an afternoon, a whole day. Many of her \
clients are elderly. Some are at the end of their lives.

Errands happen too. If they need a prescription collected or a stop at the \
shop while she's with them, she does that. But those are things that happen \
DURING a visit. You are booking her company, not a delivery.

WHO IS CALLING
Often not the person she'll be visiting. It's usually a son or daughter \
arranging company for a parent, sometimes from another state, sometimes \
carrying a lot of guilt about not being there themselves. Be gentle with them.

Sometimes it is the person themselves, and they may be lonely enough that the \
call is partly the point. That is fine. Let them talk a little.

HOW TO SPEAK
Warm, unhurried, plain words. Short sentences — this is a phone call.
One question at a time, never two. If they pause, wait. Do not fill silence.
If you didn't catch something, ask kindly rather than guessing.
Never say "absolutely" or "perfect". Don't sound like a call centre.

WHAT YOU KNOW ABOUT THIS CALLER
{known}

If you already know them, greet them by name and confirm rather than re-ask.
Making a regular spell their street again is what makes them feel like a \
stranger to a business they already use.

WHAT TO FIND OUT, conversationally, one at a time — never read this as a list:
1. Who the visit is for, and whether that's them or someone they care for
2. Where — the address Miss Dee would come to
3. What that person would enjoy having company doing. This is the real \
   question. Cards, the garden, a programme they watch, just someone in the \
   house. Ask it warmly, not like a form field.
4. How long they'd like — a couple of hours, an afternoon, a full day
5. Whether it's a one-off or something they'd like regularly
6. Anything Miss Dee should pick up while she's out with them — a \
   prescription, a few things from the shop

VISIT LENGTHS
- visit_short          a couple of hours
- visit_half_day       an afternoon
- visit_full_day       a full day
- visit_appointment    company at an appointment, waiting room included

BOOKING
Call check_availability before offering any times. Offer them plainly — \
"Miss Dee could come Tuesday afternoon, Wednesday afternoon, or Thursday." \
Never read a long list. When they choose, call book_appointment with the exact \
start value you were given. Put the errands and what they'd enjoy in the notes.
Then read back the day and time, and spell the reference slowly.

If they want a regular arrangement, say Miss Dee will set that up with them \
herself — book the first visit and note that they'd like it regularly.

NEVER
- Give medical advice. Not about medicines, symptoms, or seeing a doctor.
- Ask what a medication is for, or about conditions or diagnoses. You need the \
pharmacy and the name on the prescription, nothing more. This business keeps \
no medical records on purpose.
- Quote a price. Say Miss Dee will go over it with them, and offer a callback.
- Claim to be a person. If asked, say plainly that you're an assistant and \
Miss Dee herself is the one who comes.

═══ HEALTH INFORMATION ═══
{health_rules}

═══ AN EMERGENCY HAPPENING RIGHT NOW ═══
If someone says a person has fallen, isn't breathing, can't be woken, is \
bleeding, is having chest pain — anything happening AT THIS MOMENT:
Tell them clearly and immediately to hang up and dial 911. Say it plainly, \
once, and don't add anything else. Do not ask questions. Do not book anything.

═══ SOMEONE HAS DIED, OR IS DYING ═══
This is DIFFERENT and 911 is the WRONG answer here.

If a caller tells you someone has passed away, or is in their final days:
- Stop being an assistant. Say you're very sorry. Keep it short and simple. \
  "I'm so sorry. Thank you for letting us know."
- Do NOT offer to book anything.
- Do NOT ask them to confirm or cancel appointments. Someone will handle that \
  quietly later; they should not have to do admin today.
- Do NOT say anything cheerful, and do NOT say "is there anything else I can \
  help with".
- Ask only for a good number to reach them on, and tell them Miss Dee will \
  call them herself.
- Then let them go. Do not keep them on the line.

If a family is in hospice care and asking what to do, they should call their \
HOSPICE, not 911 — hospice handles an expected death. Do not tell a grieving \
family to call 911.

IF SOMEONE IS SIMPLY UPSET
Grief, exhaustion, guilt about a parent — be kind and unhurried, but do not \
counsel them. Offer to have Miss Dee call. She is the one who helps; you just \
answer the phone.

ENDING
Confirm what's booked, thank them by name, and let them hang up first. They \
may take a moment.

Today is {today}."""


# ---------------------------------------------------------------------------
# the local services
# ---------------------------------------------------------------------------


def stt_transcribe(audio: np.ndarray) -> str:
    """8kHz float32 -> text. Runs on the GPU next door."""
    try:
        r = httpx.post(
            f"{STT_URL}/transcribe-array",
            json={"audio": audio.astype(np.float32).tolist(), "sample_rate": 8000},
            timeout=30.0,
        )
        r.raise_for_status()
        return (r.json().get("text") or "").strip()
    except Exception as exc:  # noqa: BLE001
        log.error("stt failed: %s", exc)
        return ""


def tts_synth(text: str) -> tuple[np.ndarray, int]:
    """
    text -> (float32 audio, sample rate)

    The voice is named EXPLICITLY rather than left to the service default.
    Without this every surface speaks in whatever the default happens to be,
    so changing the CLI's voice would silently change the voice answering
    your mum's business line. Each surface asks for its own.
    """
    r = httpx.post(
        f"{VOICE_URL}/synthesize-array",
        json={"text": text, "voice": PHONE_VOICE},
        timeout=60.0,
    )
    r.raise_for_status()
    data = r.json()
    return np.asarray(data["audio"], dtype=np.float32), int(data["sample_rate"])


def llm_turn(history: list[dict], tools: list[dict] | None) -> dict[str, Any]:
    """One model turn. Not streamed: we need the whole reply before we can speak it."""
    payload: dict[str, Any] = {
        "model": PHONE_JOB,
        "messages": history,
        "temperature": 0.4,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    r = httpx.post(
        f"{ROUTER_URL}/chat/completions",
        json=payload,
        headers={"Authorization": f"Bearer {ROUTER_KEY}"},
        timeout=90.0,
    )
    r.raise_for_status()
    msg = r.json()["choices"][0]["message"]
    out: dict[str, Any] = {"role": "assistant", "content": msg.get("content") or ""}
    if msg.get("tool_calls"):
        out["tool_calls"] = msg["tool_calls"]
    return out


def make_tool_runner(state):
    """
    Tools bound to one call, so they know who's on the phone.

    Returns a plain function the conversation loop calls. Errors come back
    phrased as instructions the model can act on, never as stack traces --
    a caller should hear "let me take your number" not "500".
    """

    def run(name: str, args: dict) -> str:
        # --- client identity -------------------------------------------------
        if name == "identify_client":
            code = "".join(ch for ch in str(args.get("code") or "") if ch.isdigit())
            if code:
                client = clients.get(code)
                if not client:
                    return ("There's no client with that number. Ask them to read it "
                            "again slowly, or ask for the name of the person the "
                            "visit is for.")
                # Remember this phone for next time -- they'll never need the
                # number again from this handset.
                if state.caller:
                    clients.link_phone(code, state.caller)
                state.client = client
                return (client_summary(client) +
                        " You now know who this is about. Do not ask again for their "
                        "name or address.")

            nm = (args.get("name") or "").strip()
            if nm:
                matches = clients.find_by_name(nm)
                if not matches:
                    return (f"No client found matching '{nm}'. They may be new -- "
                            "that's fine, just take the details as normal.")
                if len(matches) > 1:
                    return ("More than one person matches that name: "
                            + ", ".join(m["name"] for m in matches)
                            + ". Ask for their client number to be sure.")
                state.client = matches[0]
                if state.caller:
                    clients.link_phone(matches[0]["code"], state.caller)
                return client_summary(matches[0])
            return "I need either a client number or a name to look someone up."

        # --- a message for Dee ----------------------------------------------
        if name == "leave_note":
            if not state.client:
                return ("I don't know who this note is about yet. Ask for their "
                        "client number, or the name of the person Miss Dee visits.")
            ok = clients.add_note(
                state.client["code"], args.get("note") or "",
                from_phone=state.caller, from_name=args.get("from_name") or "",
            )
            if not ok:
                return "Nothing to write down. Ask them what they'd like Dee to know."
            state.left_note = True
            return ("Written down. Miss Dee will read it before her next visit. "
                    "Tell them so, warmly and briefly, and don't offer to do "
                    "anything else unless they ask.")

        # --- how has it been going ------------------------------------------
        if name == "recent_visits":
            if not state.client:
                return ("I don't know who they're asking about. Ask for the client "
                        "number or the name.")
            notes = clients.recent_notes(state.client["code"], limit=5)
            if not notes:
                return ("There's nothing written down yet. Say honestly that you "
                        "don't have anything from the last visit, and offer to have "
                        "Miss Dee call and tell them herself.")
            lines = [f"- {n['created_at'][:10]}: {n['note']}" for n in notes]
            return ("Here is what's been written down recently:\n" + "\n".join(lines)
                    + "\n\nShare this warmly and in your own words. If they want more "
                    "than this, offer to have Miss Dee call them -- she is the one "
                    "who was actually there.")

        # --- somewhere to go --------------------------------------------------
        #
        # ANSWERED FROM MEMORY, WITH NO MODEL AND NO NETWORK. The directory is
        # loaded at startup and re-read when it changes; this is a scored lookup
        # over a few dozen dicts. It returns in under a millisecond, which is the
        # entire point -- a caller asking "where could she go?" gets an answer in
        # the next breath instead of listening to silence while something thinks.
        #
        # It is also the safety mechanism. Every entry here was checked against a
        # source. Without this tool the model answers from training data, and a
        # confidently wrong address sends a frail person to a building that
        # closed two years ago.
        if name == "find_places":
            query = (args.get("query") or "").strip()
            purpose = (args.get("purpose") or "any").strip().lower()
            low = bool(args.get("low_mobility"))

            if purpose == "outing":
                found = PLACES.suggest_outing(wants=query, low_mobility=low, limit=3)
            else:
                found = PLACES.search(query=query, limit=3)

            if not found:
                return ("There is nothing in the directory for that. Say so plainly "
                        "-- do NOT suggest somewhere from memory, because you cannot "
                        "check whether it is still open or where it actually is. "
                        "Offer to have Miss Dee find somewhere and call them back.")

            lines = []
            for e in found:
                bits = [e.get("name", "")]
                if e.get("address"):
                    bits.append(e["address"])
                if e.get("phone"):
                    bits.append(e["phone"])
                if e.get("hours"):
                    bits.append(f"open {e['hours']}")
                if e.get("cost"):
                    bits.append(str(e["cost"]))
                if e.get("good_for"):
                    bits.append("good for " + ", ".join(e["good_for"]))
                if e.get("notes"):
                    bits.append(e["notes"])
                lines.append("- " + " | ".join(str(b) for b in bits if b))

            advice = next((e["_advice"] for e in found if e.get("_advice")), "")
            out = ("From the directory -- these are real and were checked:\n"
                   + "\n".join(lines))
            if advice:
                out += f"\n\nWorth mentioning: {advice}"
            out += ("\n\nOffer two of these, named, with one reason each. Read the "
                    "address only if they ask for it -- nobody writes down three "
                    "addresses over the phone.")
            return out

        # --- booking ---------------------------------------------------------
        path = {"check_availability": "/tools/check-availability",
                "book_appointment": "/tools/book"}.get(name)
        if not path:
            return f"error: unknown tool {name}"
        if state.client:
            args.setdefault("client_name", state.client["name"])
            args.setdefault("pickup_address", state.client.get("address") or "")

        body = {"message": {"toolCallList": [{"id": "t", "name": name, "arguments": args}]},
                "call": {"id": state.call_sid}}
        try:
            r = httpx.post(
                f"{BOOKING_URL}{path}", json=body,
                headers={"X-API-Key": BOOKING_KEY}, timeout=20.0,
            )
            r.raise_for_status()
            return r.json()["results"][0]["result"]
        except Exception as exc:  # noqa: BLE001
            log.error("tool %s failed: %s", name, exc)
            return ("The scheduling system isn't responding. Apologise, take their "
                    "name and number, and say Miss Dee will call them back.")

    return run


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health():
    out = {"status": "ok", "business": BUSINESS_NAME, "public_host": PUBLIC_HOST or "(not set)",
           "known_callers": len(callers.recent(1000)),
           # Read by the KB importer so it can stay off the GPU during a call.
           "active_calls": ACTIVE_CALLS,
           "places": PLACES.count()}
    for name, url in (("stt", STT_URL), ("voice", VOICE_URL), ("booking", BOOKING_URL)):
        try:
            out[name] = httpx.get(f"{url}/health", timeout=3.0).json().get("status", "?")
        except Exception:  # noqa: BLE001
            out[name] = "unreachable"
            out["status"] = "degraded"
    return out


@app.post("/twiml/incoming")
async def twiml_incoming(request: Request):
    """
    Twilio hits this when someone calls. We answer with TwiML telling it to
    open a two-way audio stream to us.

    <Connect> rather than <Start>: <Connect> is the bidirectional one and it
    blocks the rest of the call flow, so we own the call until we're done.
    <Start> would only let us listen.
    """
    form = await request.form()

    # Prove it's Twilio before doing anything at all. See twilio_auth.py.
    if not signature_ok(
        TWILIO_AUTH_TOKEN, PUBLIC_HOST, "/twiml/incoming",
        {k: str(v) for k, v in form.items()},
        request.headers.get("X-Twilio-Signature"),
    ):
        raise HTTPException(status_code=403, detail="bad signature")

    caller = normalise(str(form.get("From") or ""))
    call_sid = str(form.get("CallSid") or "")
    log.info("incoming call %s from %s", call_sid, caller or "(withheld)")

    if not PUBLIC_HOST:
        log.error("PUBLIC_HOST is not set -- Twilio has nowhere to stream to")
        return PlainTextResponse(
            '<?xml version="1.0" encoding="UTF-8"?><Response><Say>'
            "This line is not configured yet. Goodbye."
            "</Say><Hangup/></Response>",
            media_type="application/xml",
        )

    # Query strings aren't allowed on the stream URL, so the caller's number
    # travels as a <Parameter> and arrives in the start event.
    #
    # The `token` parameter is what protects /media. Twilio can't sign a
    # WebSocket handshake, so the socket can't be verified the way these POSTs
    # are. Instead the secret rides in the TwiML that only a verified caller
    # could have caused us to generate, and comes back in the start event.
    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f'    <Stream url="wss://{PUBLIC_HOST}/media">\n'
        f'      <Parameter name="caller" value="{caller}" />\n'
        f'      <Parameter name="callSid" value="{call_sid}" />\n'
        f'      <Parameter name="token" value="{STREAM_SECRET}" />\n'
        "    </Stream>\n"
        "  </Connect>\n"
        "</Response>"
    )
    return PlainTextResponse(twiml, media_type="application/xml")


# THE STORY LINE. A number rings, the story plays, the call ends.
#
# STORY_URL points at the tribute site, so re-narrating the story
# changes what the phone plays with no deploy here -- the words, the
# voice and the telephone are one thing kept in one place, which was the
# point of pre-rendering the narration in the first place.
STORY_URL = os.environ.get(
    "PEEWEE_STORY_URL",
    "https://peewee.home.example.net/people/peewee-harris/story-5.mp3")


@app.post("/twiml/peewee")
async def twiml_peewee(request: Request):
    """Answer, play the story, hang up.

    NO STREAM, NO MODEL, NO TRANSCRIPT. <Play> is executed by Twilio
    against a public URL; nothing about this call reaches the house
    except the fact that it happened. That is the right shape for a
    memorial line and it is also the cheap one -- the assistant route
    next door holds a socket open and runs a model per minute.

    <Hangup/> IS EXPLICIT even though Twilio ends a call that runs out
    of TwiML. Relying on the default leaves a silent line while the
    caller wonders whether to say something, and the last thing this
    line should do is ask a person to fill a silence.
    """
    form = await request.form()
    if not signature_ok(
        TWILIO_AUTH_TOKEN, PUBLIC_HOST, "/twiml/peewee",
        {k: str(v) for k, v in form.items()},
        request.headers.get("X-Twilio-Signature"),
    ):
        raise HTTPException(status_code=403, detail="bad signature")

    caller = normalise(str(form.get("From") or ""))
    log.info("peewee line: %s -> playing %s", caller or "(withheld)", STORY_URL)

    twiml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        # A beat before the voice starts. Answering mid-word sounds like
        # a machine; a moment of line noise sounds like somebody picking
        # up, and the caller is still moving the phone to their ear.
        '  <Pause length="1"/>\n'
        f"  <Play>{STORY_URL}</Play>\n"
        "  <Hangup/>\n"
        "</Response>"
    )
    return PlainTextResponse(twiml, media_type="application/xml")


@app.post("/twiml/sms")
async def twiml_sms(request: Request):
    """
    Someone texted the number. Same number they'd call.

    No model in this path on purpose: texts are keyword-driven and instant.
    Running an LLM here would add seconds and unpredictability to something
    that should feel like a vending machine.
    """
    form = await request.form()

    # The endpoint with the most to lose. An unsigned POST here drives the
    # whole keyword handler as if it were a client, and once SMS_ENABLED is
    # true it sends real, billed texts to any number the caller names.
    if not signature_ok(
        TWILIO_AUTH_TOKEN, PUBLIC_HOST, "/twiml/sms",
        {k: str(v) for k, v in form.items()},
        request.headers.get("X-Twilio-Signature"),
    ):
        raise HTTPException(status_code=403, detail="bad signature")

    body = str(form.get("Body") or "")
    from_phone = normalise(str(form.get("From") or ""))
    log.info("sms from %s: %s", from_phone or "(unknown)", body[:80])
    twiml = sms_mod.handle(
        body, from_phone,
        clients=clients,
        business_name=BUSINESS_NAME,
        booking_url=BOOKING_LINK or "(booking link not configured)",
        ratings=ratings,
        outbound=outbound,
        review_link=REVIEW_LINK,
        tip_link=TIP_LINK,
    )
    return PlainTextResponse(twiml, media_type="application/xml")


def send_sms(to: str, body: str) -> bool:
    """One text, via Twilio's REST API."""
    if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_FROM_NUMBER):
        log.warning("SMS not configured -- would have sent to %s: %s", to, body[:60])
        return False
    try:
        r = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_ACCOUNT_SID}/Messages.json",
            auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
            data={"To": to, "From": TWILIO_FROM_NUMBER, "Body": body},
            timeout=20.0,
        )
        r.raise_for_status()
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("sms to %s failed: %s", to, exc)
        return False


async def sms_scheduler() -> None:
    """
    Every ten minutes: ask the booking service what's on, work out what's due,
    send it.

    Deliberately dumb and idempotent. If the machine was off for a day, it
    wakes up and sends only what's still appropriate -- the quiet-hours and
    staleness rules mean it can't spam anyone with a backlog.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(BUSINESS_TZ)
    await asyncio.sleep(30)   # let the rest of the stack come up
    while True:
        try:
            if SMS_ENABLED:
                # In a thread: a slow booking API must not freeze live calls (ASYNC210).
                r = await asyncio.to_thread(
                    httpx.get, f"{BOOKING_URL}/bookings", params={"limit": 200},
                    headers={"X-API-Key": BOOKING_KEY}, timeout=15.0)
                r.raise_for_status()
                bookings = r.json().get("bookings", [])
                due = due_messages(
                    bookings, outbound,
                    now_utc=datetime.now(UTC), tz=tz,
                    business=BUSINESS_NAME, booking_link=BOOKING_LINK,
                )
                for msg in due:
                    if send_sms(msg.phone, msg.body):
                        outbound.record_sent(msg)
                        if msg.kind == "followup":
                            client = clients.by_phone(msg.phone)
                            if client:
                                ratings.request(client["code"], msg.phone)
                        log.info("sent %s for %s", msg.kind, msg.reference)
        except Exception:
            log.exception("sms scheduler pass failed")
        await asyncio.sleep(600)


@app.on_event("startup")
async def _start_scheduler():
    if SMS_ENABLED:
        asyncio.create_task(sms_scheduler())
        log.info("sms scheduler running (timezone %s)", BUSINESS_TZ)
    else:
        log.info("sms disabled (SMS_ENABLED=false)")


@app.post("/admin/suppress/{reference}")
def admin_suppress(reference: str, reason: str = "bereavement"):
    """Stop all further texts about a booking. Use after a death."""
    outbound.suppress(reference, reason)
    return {"suppressed": reference, "reason": reason}


@app.get("/admin/messages")
def admin_messages():
    return {"sent": outbound.history()}


@app.get("/admin/ratings")
def admin_ratings():
    return {"summary": ratings.summary(), "needs_attention": ratings.needs_attention()}


@app.get("/admin/notes")
def admin_notes():
    """What Dee hasn't read yet -- the closest thing to an inbox."""
    return {"unseen": clients.unseen_notes()}


@app.get("/admin/clients")
def admin_clients():
    return {"clients": clients.all_clients()}


@app.post("/admin/clients")
async def admin_create_client(request: Request):
    data = await request.json()
    if not (data.get("name") or "").strip():
        raise HTTPException(status_code=400, detail="name is required")
    client = clients.create(
        data["name"], address=data.get("address", ""),
        notes=data.get("notes", ""), likes=data.get("likes", ""),
    )
    log.info("created client %s -> %s", client["code"], client["name"])
    return client


def _save_transcript(conv: Conversation) -> None:
    s = conv.state
    try:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        d = Path(TRANSCRIPT_DIR) / "phone" / day
        d.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now(UTC).isoformat(timespec="seconds"),
            "surface": "phone",
            "call_sid": s.call_sid,
            "caller": s.caller,
            "seconds": round(time.monotonic() - s.started, 1),
            "turns": s.turns,
            "booked": s.booked,
            "transcript": s.transcript,
        }
        (d / f"{s.call_sid or 'unknown'}.json").write_text(
            json.dumps(rec, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception:
        log.exception("could not save transcript")


def _remember(conv: Conversation) -> None:
    """
    Pull the durable facts out of the call so they don't have to say them again.

    Deliberately a cheap extra model call, not something clever: it happens
    after the caller has hung up, so nobody is waiting on it.
    """
    s = conv.state
    if not s.caller or not s.transcript:
        return
    try:
        text = "\n".join(f"{t['role']}: {t['text']}" for t in s.transcript)
        prompt = (
            "From this phone call, extract JSON with keys: caller_name, "
            "pickup_address, destination, notes. Use empty strings for anything "
            "not mentioned. `notes` is practical only -- mobility, which door, "
            "hard of hearing. NEVER medical information. JSON only.\n\n" + text
        )
        msg = llm_turn(
            [{"role": "system", "content": "You extract structured data. JSON only."},
             {"role": "user", "content": prompt}],
            None,
        )
        raw = (msg.get("content") or "").strip()
        if "{" in raw:
            raw = raw[raw.index("{"): raw.rindex("}") + 1]
        data = json.loads(raw)
        callers.update(
            s.caller,
            name=(data.get("caller_name") or "").strip() or None,
            pickup_address=(data.get("pickup_address") or "").strip() or None,
            notes=(data.get("notes") or "").strip() or None,
        )
        if (data.get("destination") or "").strip():
            callers.add_destination(s.caller, data["destination"].strip())
        log.info("remembered %s for %s", [k for k, v in data.items() if v], s.caller)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not extract caller details: %s", exc)


@app.websocket("/media")
async def media(ws: WebSocket):
    global ACTIVE_CALLS
    await ws.accept()
    ACTIVE_CALLS += 1
    log.info("media stream connected (%d live)", ACTIVE_CALLS)

    async def send(frame: dict) -> None:
        await ws.send_text(json.dumps(frame))

    conv: Conversation | None = None

    try:
        while True:
            raw = await ws.receive_text()
            try:
                evt = json.loads(raw)
            except json.JSONDecodeError:
                continue

            kind = evt.get("event")

            if kind == "connected":
                continue

            if kind == "start":
                start = evt.get("start") or {}
                params = start.get("customParameters") or {}

                # Only TwiML we generated carries this, and we only generate
                # TwiML for a signature-verified call. Anything else opening
                # this socket gets closed before a single frame is processed --
                # otherwise it's an open line to the model on the GPU.
                if not STREAM_SECRET or not hmac.compare_digest(
                    str(params.get("token") or ""), STREAM_SECRET
                ):
                    log.warning("media stream without a valid token -- closing")
                    await ws.close(code=1008)
                    return

                caller = normalise(params.get("caller") or "")
                rec = callers.touch(caller) if caller else None
                # If this number has called about someone before, we already
                # know who the call is about -- no code needed.
                known_client = clients.by_phone(caller) if caller else None

                conv = Conversation(
                    send,
                    transcribe=stt_transcribe,
                    synth=tts_synth,
                    llm=llm_turn,
                    tool_call=lambda n, a: None,   # replaced below, needs state
                    system_prompt=SYSTEM_PROMPT.format(
                        business=BUSINESS_NAME,
                        known=(summary_for(rec) + " " + client_summary(known_client)).strip(),
                        today=datetime.now().strftime("%A, %d %B %Y"),
                        health_rules=HEALTH_RULES,
                    ),
                    tuning=Tuning(
                        silence_ms=int(os.environ.get("PHONE_SILENCE_MS", "800")),
                        barge_in_ms=int(os.environ.get("PHONE_BARGE_MS", "320")),
                    ),
                )
                conv.state.stream_sid = evt.get("streamSid") or start.get("streamSid", "")
                conv.state.call_sid = start.get("callSid") or params.get("callSid", "")
                conv.state.caller = caller
                conv.state.client = known_client
                conv.tool_call = make_tool_runner(conv.state)
                log.info("call started: %s from %s (%s)",
                         conv.state.call_sid, caller or "unknown",
                         "returning" if (rec and rec.get("call_count", 0) > 1) else "new")

                conv.state.speak_task = asyncio.create_task(
                    conv.speak(greeting_for(rec, BUSINESS_NAME))
                )
                continue

            if conv is None:
                continue

            if kind == "media":
                await conv.on_media((evt.get("media") or {}).get("payload", ""))
                if conv.expired():
                    log.info("call hit the time limit")
                    await conv.speak("I'm afraid I have to go now. Please call back "
                                     "any time. Take care.")
                    break

            elif kind == "mark":
                conv.on_mark((evt.get("mark") or {}).get("name", ""))

            elif kind == "dtmf":
                log.info("keypad: %s", (evt.get("dtmf") or {}).get("digit"))

            elif kind == "stop":
                log.info("caller hung up")
                break

    except WebSocketDisconnect:
        log.info("stream disconnected")
    except Exception:
        log.exception("media stream error")
    finally:
        # DECREMENTED IN `finally`, not after a clean disconnect. A call that
        # ends by exception is still a call that ended, and a counter that only
        # goes down on the happy path drifts upward until the importer decides
        # the phone is permanently busy and never runs again.
        ACTIVE_CALLS = max(0, ACTIVE_CALLS - 1)
        log.info("media stream closed (%d live)", ACTIVE_CALLS)
        if conv:
            if conv.state.speak_task and not conv.state.speak_task.done():
                conv.state.speak_task.cancel()
            _save_transcript(conv)
            # Do the remembering off the hot path -- the caller is gone.
            asyncio.get_event_loop().run_in_executor(None, _remember, conv)
            log.info("call ended: %d turns, %.0fs, booked=%s",
                     conv.state.turns, time.monotonic() - conv.state.started,
                     conv.state.booked)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001, S110  closing an already-dropped socket
            pass


# --- small admin surface, localhost only ------------------------------------


@app.get("/admin/callers")
def admin_callers():
    return {"callers": callers.recent(200)}


@app.get("/admin/caller/{number}")
def admin_caller(number: str):
    rec = callers.get(number)
    if not rec:
        raise HTTPException(status_code=404, detail="no record for that number")
    return rec


@app.post("/admin/caller/{number}/block")
def admin_block(number: str):
    callers.block(number, True)
    return {"blocked": normalise(number)}


@app.exception_handler(Exception)
def unhandled(_request: Request, exc: Exception):
    log.exception("unhandled")
    return JSONResponse(status_code=500, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn

    # Listens on every interface inside its own container; only the docker network and Caddy reach it (no published port).
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8093")))  # nosec B104
