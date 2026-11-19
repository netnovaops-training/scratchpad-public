"""
Drive a fake phone call through the orchestrator.

No Twilio, no GPU, no network. Fake audio in, recorded websocket frames out.
This is where turn-taking bugs get caught, because on a real call they present
as "it feels weird" and you can't tell which stage was wrong.

    cd telephony && python tests/test_conversation.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import audio as A
from app.conversation import Conversation, Tuning

PASS: list[str] = []
FAIL: list[str] = []


def ck(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + ("" if cond or not detail else f" -- {detail}"))


# --- fake audio --------------------------------------------------------------

def speech_frame(level: float = 0.25) -> str:
    """20ms of something that reads as speech."""
    t = np.linspace(0, 0.02, A.FRAME_SAMPLES, endpoint=False, dtype=np.float32)
    sig = (level * (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 700 * t))).astype(np.float32)
    return A.b64(A.float_to_mulaw(sig))


def silence_frame() -> str:
    return A.b64(A.float_to_mulaw(np.zeros(A.FRAME_SAMPLES, dtype=np.float32)))


# --- harness -----------------------------------------------------------------

class Harness:
    def __init__(self, **kw):
        self.sent: list[dict] = []
        self.transcripts = kw.pop("transcripts", ["I need a ride to the pharmacy"])
        self.replies = kw.pop("replies", [{"role": "assistant", "content": "Of course. What is your name?"}])
        self.tool_results = kw.pop("tool_results", {})
        self.stt_calls = 0
        self.llm_calls = 0
        self.tts_calls = 0
        self.tool_calls: list[tuple[str, dict]] = []

        self.conv = Conversation(
            self.send,
            transcribe=self.transcribe,
            synth=self.synth,
            llm=self.llm,
            tool_call=self.tool,
            system_prompt="you are a test",
            tuning=kw.pop("tuning", Tuning(silence_ms=400, barge_in_ms=100, min_speech_ms=100)),
        )
        self.conv.state.stream_sid = "MZtest"

    async def send(self, frame: dict) -> None:
        self.sent.append(frame)

    def transcribe(self, audio: np.ndarray) -> str:
        i = min(self.stt_calls, len(self.transcripts) - 1)
        self.stt_calls += 1
        return self.transcripts[i]

    def synth(self, text: str):
        self.tts_calls += 1
        # 0.5s of 24kHz audio, like Kokoro would produce
        return np.zeros(12000, dtype=np.float32), 24000

    def llm(self, history, tools):
        i = min(self.llm_calls, len(self.replies) - 1)
        self.llm_calls += 1
        return self.replies[i]

    def tool(self, name: str, args: dict) -> str:
        self.tool_calls.append((name, args))
        return self.tool_results.get(name, "ok")

    # helpers
    def media_events(self) -> list[dict]:
        return [f for f in self.sent if f.get("event") == "media"]

    def clears(self) -> list[dict]:
        return [f for f in self.sent if f.get("event") == "clear"]

    def marks(self) -> list[dict]:
        return [f for f in self.sent if f.get("event") == "mark"]

    async def feed(self, frames: list[str]) -> None:
        for f in frames:
            await self.conv.on_media(f)

    async def settle(self) -> None:
        """Let any speak/respond tasks finish."""
        for _ in range(80):
            await asyncio.sleep(0)
            tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            if not tasks:
                break
            await asyncio.sleep(0.01)


# --- tests -------------------------------------------------------------------


async def test_speaks_and_marks():
    print("\nspeaking to the caller")
    h = Harness()
    await h.conv.speak("Hello there.")
    media = h.media_events()
    ck("sends audio frames", len(media) > 0, str(len(media)))
    ck("every frame carries the streamSid", all(f.get("streamSid") == "MZtest" for f in media))
    ck("payload is base64 of exactly one 20ms frame",
       all(len(A.unb64(f["media"]["payload"])) == A.FRAME_BYTES for f in media))
    ck("0.5s of 24k audio -> 25 frames of 8k", len(media) == 25, str(len(media)))
    ck("sends a mark after the audio", len(h.marks()) == 1)
    ck("mark comes last", h.sent[-1]["event"] == "mark")
    ck("no clear was sent", not h.clears())


async def test_turn_detection():
    print("\nend of turn: speech then silence")
    h = Harness()
    await h.feed([speech_frame()] * 40)          # 800ms speech
    ck("no transcription while they're still talking", h.stt_calls == 0)
    await h.feed([silence_frame()] * 10)          # 200ms silence, under 400 threshold
    ck("short pause does not end the turn", h.stt_calls == 0)
    await h.feed([silence_frame()] * 15)          # now past 400ms
    await h.settle()
    ck("longer pause ends the turn", h.stt_calls == 1)
    ck("model was called once", h.llm_calls == 1)
    ck("reply was spoken", h.tts_calls == 1)
    ck("caller turn recorded", h.conv.state.turns == 1)


async def test_cough_ignored():
    print("\na cough is not a turn")
    h = Harness()
    await h.feed([speech_frame()] * 3)            # 60ms, under min_speech_ms=100
    await h.feed([silence_frame()] * 30)
    await h.settle()
    ck("blip below the minimum is discarded", h.stt_calls == 0)
    ck("no model call", h.llm_calls == 0)


async def test_barge_in():
    print("\nbarge-in: caller talks over the assistant")
    h = Harness()
    task = asyncio.create_task(h.conv.speak("This is a long sentence " * 20))
    h.conv.state.speak_task = task
    await asyncio.sleep(0)
    ck("assistant is speaking", h.conv.state.speaking)

    await h.feed([speech_frame()] * 3)            # 60ms, under barge_in_ms=100
    ck("brief noise does not interrupt", not h.clears())

    await h.feed([speech_frame()] * 4)            # now past 100ms
    ck("sustained speech interrupts", len(h.clears()) == 1)
    ck("clear carries the streamSid", h.clears()[0].get("streamSid") == "MZtest")
    ck("speaking flag cleared", not h.conv.state.speaking)
    ck("their interrupting audio is kept as the start of their turn",
       h.conv.state.in_speech and len(h.conv.state.buf) > 0)
    task.cancel()


async def test_backchannel_does_not_interrupt():
    print("\n'mm-hmm' while the assistant talks")
    h = Harness(tuning=Tuning(silence_ms=400, barge_in_ms=300, min_speech_ms=100))
    task = asyncio.create_task(h.conv.speak("A fairly long reply " * 20))
    h.conv.state.speak_task = task
    await asyncio.sleep(0)
    # short "mm", then quiet, then another short "mm"
    await h.feed([speech_frame(0.2)] * 5 + [silence_frame()] * 10 + [speech_frame(0.2)] * 5)
    ck("brief acknowledgements don't stop the assistant", not h.clears())
    ck("still speaking", h.conv.state.speaking)
    task.cancel()


async def test_tool_flow():
    print("\ntool call then booking")
    h = Harness(
        transcripts=["I need a ride tuesday"],
        replies=[
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c1", "function": {
                 "name": "check_availability",
                 "arguments": json.dumps({"service": "appointment_ride"})}}]},
            {"role": "assistant", "content": "Miss Dee could come Tuesday at 9 AM."},
        ],
        tool_results={"check_availability": "Available: Tuesday 9 AM [start=2026-08-18T13:00:00+00:00]"},
    )
    h.conv.state.caller = "+17025550123"
    await h.feed([speech_frame()] * 40 + [silence_frame()] * 25)
    await h.settle()
    ck("the tool was called", len(h.tool_calls) == 1, str(h.tool_calls))
    ck("correct tool name", h.tool_calls and h.tool_calls[0][0] == "check_availability")
    ck("model called twice (request, then answer)", h.llm_calls == 2, str(h.llm_calls))
    ck("tool result went into history",
       any(m.get("role") == "tool" for m in h.conv.state.history))
    ck("spoke a filler while looking up, then the answer", h.tts_calls >= 2, str(h.tts_calls))


async def test_phone_number_injected():
    print("\nbooking gets the caller's number without asking")
    h = Harness(
        transcripts=["book it"],
        replies=[
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c1", "function": {
                 "name": "book_appointment",
                 "arguments": json.dumps({"service": "companionship",
                                          "start": "2026-08-18T13:00:00+00:00",
                                          "client_name": "Ruth"})}}]},
            {"role": "assistant", "content": "Booked."},
        ],
        tool_results={"book_appointment": "Booked a companionship visit. Reference K S C B."},
    )
    h.conv.state.caller = "+17025550123"
    await h.feed([speech_frame()] * 40 + [silence_frame()] * 25)
    await h.settle()
    _name, args = h.tool_calls[0]
    ck("caller id filled in automatically", args.get("client_phone") == "+17025550123", str(args))
    ck("booking flagged on the call", h.conv.state.booked)


async def test_transcript_recorded():
    print("\ntranscript")
    h = Harness(transcripts=["I need help with groceries"])
    await h.feed([speech_frame()] * 40 + [silence_frame()] * 25)
    await h.settle()
    roles = [t["role"] for t in h.conv.state.transcript]
    ck("caller turn captured", "caller" in roles)
    ck("assistant turn captured", "assistant" in roles)
    ck("caller text is right",
       any(t["text"] == "I need help with groceries" for t in h.conv.state.transcript))


async def test_mark_clears_speaking():
    print("\nmark echo ends the speaking state")
    h = Harness()
    h.conv.state.speaking = True
    h.conv.on_mark("turn-1")
    ck("mark echo clears speaking", not h.conv.state.speaking)


async def test_empty_transcription_ignored():
    print("\nsilence that transcribes to nothing")
    h = Harness(transcripts=[""])
    await h.feed([speech_frame()] * 40 + [silence_frame()] * 25)
    await h.settle()
    ck("empty transcript doesn't call the model", h.llm_calls == 0)
    ck("no turn counted", h.conv.state.turns == 0)


async def main() -> int:
    print("=" * 62)
    print("  telephony — conversation logic")
    print("=" * 62)
    for fn in (
        test_speaks_and_marks, test_turn_detection, test_cough_ignored,
        test_barge_in, test_backchannel_does_not_interrupt, test_tool_flow,
        test_phone_number_injected, test_transcript_recorded,
        test_mark_clears_speaking, test_empty_transcription_ignored,
    ):
        await fn()
    print("\n" + "=" * 62)
    print(f"  {len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print(f"    FAILED: {f}")
    print("=" * 62)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
