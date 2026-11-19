"""
One phone call, start to finish.

This is the genuinely hard part of a voice agent: deciding
when the caller has finished talking, when to interrupt yourself, and how to
keep all of that responsive while a GPU is busy.

The loop:

    caller audio ──▶ VAD ──▶ end of turn? ──▶ Whisper ──▶ LLM (+tools) ──▶ Kokoro
                     │                                                      │
                     └──── caller talks while we're speaking ──▶ barge in ◀─┘

Everything is timed and logged, because when a call feels wrong the only way
to know *which* stage was slow is to have measured it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from . import audio as A

log = logging.getLogger("telephony.call")

# find_places is imported rather than written out again. It lived in places.py
# with a complete schema and a test suite and was never registered here, which
# meant the phone agent had no way to look a place up -- so when a caller asked
# where their mother could go, the model answered from its own head. That is the
# one failure mode this whole directory exists to prevent: an invented address
# sends a frail person to the wrong building.
from .places import TOOL_SCHEMA as _FIND_PLACES

TOOLS = [
    _FIND_PLACES,
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Find times Miss Dee is free to visit. Call before offering any times.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "enum": ["visit_short", "visit_half_day",
                                 "visit_full_day", "visit_appointment"],
                    },
                    "date": {"type": "string", "description": "YYYY-MM-DD, optional"},
                    "days": {"type": "integer", "description": "days ahead, default 7"},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "identify_client",
            "description": (
                "Look up who this call is about, by their 5-digit client number or "
                "by name. Use this as soon as you know either -- it tells you their "
                "address and what they enjoy, so you don't have to ask again."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string",
                             "description": "the 5-digit client number, digits only"},
                    "name": {"type": "string",
                             "description": "the client's name, if they don't know the number"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "leave_note",
            "description": (
                "Write something down for Miss Dee to read before her next visit. "
                "Use this whenever a family member tells you something about their "
                "loved one -- what they've been like lately, something they'd enjoy, "
                "something to watch for, or just a message they'd like passed on."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {"type": "string",
                             "description": "what to tell Dee, in the caller's own words where possible"},
                    "from_name": {"type": "string",
                                  "description": "who it's from, e.g. 'Karen, her daughter'"},
                },
                "required": ["note"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "recent_visits",
            "description": (
                "How the last few visits went. Use when a family member asks how "
                "their loved one has been, or wants to know what Dee and they did "
                "together."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "book_appointment",
            "description": "Book a visit. Only after the caller has chosen a time you offered.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {
                        "type": "string",
                        "enum": ["visit_short", "visit_half_day",
                                 "visit_full_day", "visit_appointment"],
                    },
                    "start": {"type": "string",
                              "description": "exact start value from check_availability"},
                    "client_name": {"type": "string"},
                    "client_phone": {"type": "string"},
                    "pickup_address": {"type": "string"},
                    "who_is_it_for": {"type": "string",
                              "description": "the person being visited, if not the caller"},
                    "what_theyd_enjoy": {"type": "string",
                              "description": "what they'd like company doing -- cards, the garden, just someone in the house"},
                    "errands": {"type": "string",
                              "description": "anything to pick up during the visit -- a prescription, a few things from the shop"},
                    "wants_regular": {"type": "string",
                              "description": "yes/no -- whether they'd like this to be a regular arrangement"},
                    "notes": {"type": "string",
                              "description": "practical only -- mobility, which door, hard of hearing. NEVER medical"},
                },
                "required": ["service", "start", "client_name"],
            },
        },
    },
]


@dataclass
class Tuning:
    # How long the caller must go quiet before we accept their turn is over.
    # This is THE number for elderly callers. Too low and you cut them off
    # mid-thought; too high and the line feels dead.
    silence_ms: int = 800
    # Below this RMS is silence. Phone lines have noise floors, so it isn't 0.
    silence_rms: float = 0.012
    # Ignore blips shorter than this -- a cough, a door, a breath.
    min_speech_ms: int = 250
    # How much continuous caller speech interrupts us mid-sentence. Low enough
    # to feel responsive, high enough that "mm-hmm" doesn't stop us dead.
    barge_in_ms: int = 320
    # Hard stops.
    max_utterance_s: int = 30
    max_call_s: int = 1200
    # Silence before we assume they've wandered off.
    no_input_timeout_s: int = 12


@dataclass
class CallState:
    stream_sid: str = ""
    call_sid: str = ""
    caller: str = ""
    started: float = field(default_factory=time.monotonic)
    speaking: bool = False
    speak_task: asyncio.Task | None = None
    buf: list[np.ndarray] = field(default_factory=list)
    speech_ms: float = 0.0
    silence_ms: float = 0.0
    in_speech: bool = False
    barge_ms: float = 0.0
    idle_ms: float = 0.0
    turns: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    transcript: list[dict[str, str]] = field(default_factory=list)
    booked: bool = False
    # Who the call is about -- often not the person on the phone.
    client: dict[str, Any] | None = None
    left_note: bool = False


class Conversation:
    """
    Owns one call. `send` is how we push JSON frames back down the websocket.
    """

    def __init__(
        self,
        send: Callable[[dict], Any],
        *,
        transcribe: Callable[[np.ndarray], str],
        synth: Callable[[str], tuple[np.ndarray, int]],
        llm: Callable[[list[dict], list[dict] | None], Any],
        tool_call: Callable[[str, dict], str],
        system_prompt: str,
        tuning: Tuning | None = None,
    ) -> None:
        self.send = send
        self.transcribe = transcribe
        self.synth = synth
        self.llm = llm
        self.tool_call = tool_call
        self.tuning = tuning or Tuning()
        self.state = CallState()
        self.state.history = [{"role": "system", "content": system_prompt}]

    @staticmethod
    def _executor():
        """
        The running loop, fetched at call time -- never cached.

        Caching `asyncio.get_event_loop()` in __init__ looks harmless and is a
        real bug: if the object is ever constructed outside the loop that later
        runs it, every run_in_executor raises "attached to a different loop" and
        the call goes silent with no audio and no obvious cause.
        """
        return asyncio.get_running_loop()

    # -- outbound audio ------------------------------------------------------

    async def speak(self, text: str) -> None:
        """
        Synthesise and stream to the caller.

        Twilio paces playback in real time, so we can push the whole utterance
        as fast as we can generate it. But we push it in 20ms frames rather
        than one blob so a barge-in `clear` lands with fine granularity.
        """
        text = (text or "").strip()
        if not text:
            return
        self.state.speaking = True
        self.state.transcript.append({"role": "assistant", "text": text})
        t0 = time.monotonic()
        try:
            wav, rate = await self._executor().run_in_executor(None, self.synth, text)
            eight_k = A.resample_to_8k(wav, rate)
            mulaw = A.float_to_mulaw(eight_k)
            log.info("tts %d chars -> %.1fs audio in %.0fms",
                     len(text), len(eight_k) / 8000, (time.monotonic() - t0) * 1000)

            for frame in A.frames(mulaw):
                if not self.state.speaking:      # barged in
                    log.info("playback cancelled mid-utterance")
                    return
                await self.send({
                    "event": "media",
                    "streamSid": self.state.stream_sid,
                    "media": {"payload": A.b64(frame)},
                })
                # Yield so inbound frames keep being processed while we send.
                await asyncio.sleep(0)

            # A mark comes back once Twilio has actually played everything
            # queued before it. That echo is the only trustworthy "they heard
            # it" signal.
            await self.send({
                "event": "mark",
                "streamSid": self.state.stream_sid,
                "mark": {"name": f"turn-{self.state.turns}"},
            })
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("speak failed")
            self.state.speaking = False

    async def barge_in(self) -> None:
        """Caller started talking over us. Stop, and flush what Twilio queued."""
        if not self.state.speaking:
            return
        log.info("caller interrupted")
        self.state.speaking = False
        if self.state.speak_task and not self.state.speak_task.done():
            self.state.speak_task.cancel()
        await self.send({"event": "clear", "streamSid": self.state.stream_sid})

    # -- inbound audio -------------------------------------------------------

    async def on_media(self, payload: str) -> None:
        t = self.tuning
        pcm = A.mulaw_to_float(A.unb64(payload))
        if pcm.size == 0:
            return
        frame_ms = (pcm.size / A.SAMPLE_RATE) * 1000.0
        loud = A.rms(pcm) > t.silence_rms
        s = self.state

        # --- while we're talking, watch only for a real interruption ---
        if s.speaking:
            if loud:
                s.barge_ms += frame_ms
                if s.barge_ms >= t.barge_in_ms:
                    s.barge_ms = 0.0
                    await self.barge_in()
                    # keep this audio: it's the start of what they said
                    s.buf = [pcm]
                    s.speech_ms, s.silence_ms, s.in_speech = frame_ms, 0.0, True
            else:
                s.barge_ms = max(0.0, s.barge_ms - frame_ms)
            return

        # --- normal listening ---
        if loud:
            s.buf.append(pcm)
            s.speech_ms += frame_ms
            s.silence_ms = 0.0
            s.in_speech = True
            s.idle_ms = 0.0
            if s.speech_ms / 1000.0 >= t.max_utterance_s:
                await self._end_of_turn()
            return

        if s.in_speech:
            # Keep trailing silence -- Whisper is more accurate on the last
            # word when it isn't clipped mid-breath.
            s.buf.append(pcm)
            s.silence_ms += frame_ms
            if s.silence_ms >= t.silence_ms:
                await self._end_of_turn()
        else:
            s.idle_ms += frame_ms
            if s.idle_ms >= t.no_input_timeout_s * 1000:
                s.idle_ms = 0.0
                await self._prompt_idle()

    async def _prompt_idle(self) -> None:
        if self.state.turns == 0:
            line = "Take your time. I'm still here whenever you're ready."
        else:
            line = "Are you still there?"
        self.state.speak_task = asyncio.create_task(self.speak(line))

    async def _end_of_turn(self) -> None:
        s = self.state
        t = self.tuning
        if s.speech_ms < t.min_speech_ms or not s.buf:
            s.buf, s.speech_ms, s.silence_ms, s.in_speech = [], 0.0, 0.0, False
            return

        audio = np.concatenate(s.buf)
        s.buf, s.speech_ms, s.silence_ms, s.in_speech = [], 0.0, 0.0, False

        t0 = time.monotonic()
        text = await self._executor().run_in_executor(None, self.transcribe, audio)
        stt_ms = (time.monotonic() - t0) * 1000
        if not text.strip():
            log.info("stt returned nothing (%.0fms)", stt_ms)
            return

        log.info("caller (%.0fms): %s", stt_ms, text)
        s.transcript.append({"role": "caller", "text": text})
        s.turns += 1
        s.history.append({"role": "user", "content": text})

        s.speak_task = asyncio.create_task(self._respond())

    # -- the model -----------------------------------------------------------

    async def _respond(self) -> None:
        s = self.state
        try:
            for _round in range(6):
                t0 = time.monotonic()
                msg = await self._executor().run_in_executor(
                    None, self.llm, s.history, TOOLS
                )
                log.info("llm %.0fms", (time.monotonic() - t0) * 1000)

                calls = msg.get("tool_calls") or []
                s.history.append(msg)

                if not calls:
                    await self.speak(msg.get("content") or "")
                    return

                # Cover the lookup with a spoken beat, or the line goes silent
                # and the caller says "hello?".
                if _round == 0:
                    await self.speak("Let me have a look for you.")

                for call in calls:
                    fn = call["function"]["name"]
                    try:
                        args = json.loads(call["function"]["arguments"] or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    if fn == "book_appointment":
                        args.setdefault("client_phone", s.caller)
                    result = await self._executor().run_in_executor(
                        None, self.tool_call, fn, args
                    )
                    if fn == "book_appointment" and "Booked" in result:
                        s.booked = True
                    s.history.append({
                        "role": "tool",
                        "tool_call_id": call.get("id", ""),
                        "name": fn,
                        "content": result,
                    })
            await self.speak("I'm sorry, I'm having trouble with that. "
                             "Let me have Miss Dee call you back.")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("respond failed")
            await self.speak("I'm sorry, something went wrong on my end. "
                             "Please call back in a moment.")

    # -- lifecycle -----------------------------------------------------------

    def on_mark(self, name: str) -> None:
        # Everything queued before this mark has now been played.
        self.state.speaking = False
        self.state.barge_ms = 0.0

    def expired(self) -> bool:
        return (time.monotonic() - self.state.started) > self.tuning.max_call_s
