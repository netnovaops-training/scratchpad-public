"""
Local speech-to-text. Whisper on your GPU, two ways in.

  POST /transcribe-array   a chunk of audio -> text.
                           This is what the phone line uses: the telephony
                           service has already decided the caller stopped
                           talking, so it just wants the words.

  WS   /stt                a streaming variant that does its own voice
                           detection and emits partial and final transcripts.
                           Kept for anything that wants to push a live audio
                           stream at it.

Engine is faster-whisper. "Whisper, live" is implemented here directly rather
than by bridging to a separate WhisperLive server — one less protocol in the
middle, one less thing to break at 2am.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("stt")

MODEL_SIZE = os.environ.get("STT_MODEL_SIZE", "small.en")
DEVICE = os.environ.get("STT_DEVICE", "cuda")
COMPUTE = os.environ.get("STT_COMPUTE_TYPE", "int8_float16")
SECRET = os.environ.get("STT_SECRET", "")

# Endpointing. These are the numbers to tune if the assistant talks over
# people or leaves them hanging.
FRAME_MS = 20
SILENCE_TO_END_MS = int(os.environ.get("STT_SILENCE_MS", "700"))
MIN_SPEECH_MS = 200            # shorter than this is a cough, not a word
PARTIAL_EVERY_MS = 800         # how often to send an interim guess
MAX_UTTERANCE_S = 30           # hard stop so a stuck line can't grow forever
SILENCE_RMS = float(os.environ.get("STT_SILENCE_RMS", "0.008"))

app = FastAPI(title="ai-stack stt", version="1.0")
_model = None
_pool = ThreadPoolExecutor(max_workers=2)

# ---------------------------------------------------------------------------
# HANDING THE CARD BACK. Same change as voice/server.py and for the same
# reason: this loaded Whisper lazily and then held it forever, and six
# GPU containers doing that on one 12 GB card left the photo tagger's
# vision model no room to work in.
#
# THE PHONE LINE IS WHY THIS DEFAULT IS LONGER. Speech-to-text here
# serves a live call; a caller cannot wait for a model to load mid
# sentence. Thirty minutes means a call that starts within half an hour
# of the last one pays nothing, and an overnight box still hands the
# card back. 0 disables it.
# ---------------------------------------------------------------------------
IDLE_MINUTES = float(os.environ.get("STT_IDLE_MINUTES", "30"))
_last_used = time.time()
_model_lock = threading.Lock()
# How many transcriptions are running. The release refuses while
# this is non-zero -- see _release_model.
_in_flight = 0


def _release_model():
    """Drop Whisper and give the VRAM back.

    Same guard as voice/server.py: the idle clock is stamped when the
    model is fetched, so a long transcription could otherwise have its
    model freed mid-sentence. A phone call is exactly that case.
    """
    global _model
    if _in_flight:
        return                      # transcribing; try again next tick
    with _model_lock:
        if _model is None:
            return
        _model = None
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001, S110  freeing GPU memory is best-effort
            pass
        log.info("idle %.0f min -- released whisper and freed the GPU", IDLE_MINUTES)


def _idle_watcher():
    while True:
        time.sleep(30)
        try:
            if _model is not None and (time.time() - _last_used) > IDLE_MINUTES * 60:
                _release_model()
        except Exception as exc:  # noqa: BLE001
            log.warning("idle watcher: %s", exc)


if IDLE_MINUTES > 0:
    threading.Thread(target=_idle_watcher, daemon=True).start()


def get_model():
    global _model, _last_used
    _last_used = time.time()
    if _model is None:
        from faster_whisper import WhisperModel

        dev = DEVICE
        compute = COMPUTE
        try:
            import torch

            if dev == "cuda" and not torch.cuda.is_available():
                log.warning("STT_DEVICE=cuda but no CUDA visible -- using cpu")
                dev, compute = "cpu", "int8"
        except Exception:  # noqa: BLE001
            dev, compute = "cpu", "int8"
        log.info("loading faster-whisper %s on %s (%s)", MODEL_SIZE, dev, compute)
        _model = WhisperModel(MODEL_SIZE, device=dev, compute_type=compute)
        log.info("whisper ready")
    return _model


def transcribe(audio: np.ndarray, sample_rate: int) -> str:
    """audio: float32 mono in [-1,1]."""
    global _in_flight, _last_used
    if audio.size < sample_rate * 0.1:
        return ""
    _in_flight += 1
    try:
        return _transcribe(audio, sample_rate)
    finally:
        _in_flight -= 1
        _last_used = time.time()


def _transcribe(audio: np.ndarray, sample_rate: int) -> str:
    model = get_model()
    segments, _info = model.transcribe(
        audio,
        language="en",
        beam_size=1,               # greedy: this is a live phone call
        vad_filter=False,          # we do our own segmentation upstream
        condition_on_previous_text=False,
        temperature=0.0,
    )
    return " ".join(s.text.strip() for s in segments).strip()


def deinterleave(pcm: bytes, channels: int) -> tuple[np.ndarray, np.ndarray]:
    """Interleaved 16-bit LE -> (customer, assistant) float32 mono in [-1,1]."""
    # A websocket frame can end mid-sample. np.frombuffer raises outright on a
    # buffer that isn't a whole number of int16s, which would kill the
    # transcriber in the middle of a live call over one stray byte. the phone agent's own
    # docs tell you to trim malformed buffers -- this is that.
    if len(pcm) % 2:
        pcm = pcm[: len(pcm) - (len(pcm) % 2)]
    if not pcm:
        return np.zeros(0, dtype=np.float32), np.zeros(0, dtype=np.float32)
    a = np.frombuffer(pcm, dtype="<i2")
    if channels <= 1:
        mono = a.astype(np.float32) / 32768.0
        return mono, np.zeros(0, dtype=np.float32)
    usable = (a.size // channels) * channels     # drop a torn frame
    a = a[:usable].reshape(-1, channels)
    customer = a[:, 0].astype(np.float32) / 32768.0
    assistant = a[:, 1].astype(np.float32) / 32768.0 if channels > 1 else np.zeros(0, np.float32)
    return customer, assistant


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x)))) if x.size else 0.0


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL_SIZE,
        "device": DEVICE,
        "loaded": _model is not None,
        "silence_ms": SILENCE_TO_END_MS,
    }


class ArrayIn(BaseModel):
    audio: list[float]
    sample_rate: int = 8000


@app.post("/transcribe-array")
def transcribe_array(req: ArrayIn):
    """
    Transcribe a chunk of raw audio.

    Used by the telephony service, which has already done VAD and knows the
    caller finished talking -- so this is a plain request/response, not a
    stream. Phone audio arrives at 8kHz; Whisper wants 16kHz, and resampling
    up is what stops it hallucinating on narrowband speech.
    """
    audio = np.asarray(req.audio, dtype=np.float32)
    if audio.size == 0:
        return {"text": "", "seconds": 0.0}
    started = time.perf_counter()
    if req.sample_rate != 16000:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(int(req.sample_rate), 16000)
        audio = resample_poly(audio, 16000 // g, req.sample_rate // g).astype(np.float32)
    text = transcribe(audio, 16000)
    ms = (time.perf_counter() - started) * 1000
    log.info("transcribed %.1fs of audio in %.0fms: %s",
             audio.size / 16000, ms, text[:80])
    return {"text": text, "seconds": round(audio.size / 16000, 2), "ms": round(ms)}


@app.post("/v1/audio/transcriptions")
async def openai_transcriptions(
    file: UploadFile = File(...),  # noqa: B008  FastAPI parameter marker, read once by design
    model: str = Form("whisper-1"),
    response_format: str = Form("json"),
    language: str = Form(None),
    prompt: str = Form(None),
    temperature: float = Form(0.0),
):
    """
    OpenAI's transcription shape, so anything written against their API works
    against this box instead.

    WHY IT EXISTS. shared/voice.py talks /v1/audio/transcriptions and nothing
    else, so the Telegram bot answered every voice note with "Voice input
    isn't configured. Set STT_API_KEY" -- while a perfectly good Whisper was
    already running here with no way to reach it. The other endpoints on this
    service are shaped for the phone line (arrays of floats, a websocket) and
    are no use to a general client.

    `model` is accepted and IGNORED, deliberately. Callers send "whisper-1"
    because that is what the API expects; the model actually used is whichever
    STT_MODEL_SIZE this service was started with. Rejecting the field would
    break every unmodified client for no gain.

    NO API KEY IS CHECKED. The port is bound to the LAN and not forwarded, so
    a key here would be a password on an inside door -- but clients still
    require STT_API_KEY to be SET before they will try, so put any non-empty
    value in .env.
    """
    raw = await file.read()
    if not raw:
        return JSONResponse({"error": {"message": "empty file"}}, status_code=400)

    # ffmpeg, not soundfile: clients send whatever the platform recorded --
    # Telegram sends OGG/Opus, phones send m4a, browsers send webm. ffmpeg is
    # already in this image for exactly this reason and reads all of them.
    import os as _os
    import subprocess
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=_os.path.splitext(file.filename or "a.ogg")[1] or ".ogg",
                                     delete=False) as tf:
        tf.write(raw)
        src = tf.name
    try:
        # In a thread so a long decode does not stall every other request (ASYNC221).
        proc = await asyncio.to_thread(
            subprocess.run,
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", src,
             "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
            capture_output=True, timeout=120,
            check=False,
        )
        if proc.returncode != 0:
            log.warning("ffmpeg could not decode %s: %s",
                        file.filename, proc.stderr.decode("utf-8", "replace")[:200])
            return JSONResponse(
                {"error": {"message": "could not decode the audio"}}, status_code=400)
        audio = np.frombuffer(proc.stdout, dtype=np.float32)
    finally:
        try:
            _os.unlink(src)
        except OSError:
            pass

    if audio.size == 0:
        text = ""
    else:
        started = time.perf_counter()
        text = transcribe(audio, 16000)
        log.info("openai-shape: %.1fs of audio in %.0fms: %s",
                 audio.size / 16000, (time.perf_counter() - started) * 1000, text[:80])

    # response_format=text returns a bare string; everything else gets JSON.
    # Those are the two shapes shared/voice.py asks for.
    if response_format == "text":
        return PlainTextResponse(text)
    return {"text": text}


@app.post("/warm")
def warm():
    """Load the model before the first call, not during it."""
    started = time.monotonic()
    get_model()
    transcribe(np.zeros(16000, dtype=np.float32), 16000)
    return {"warmed": True, "seconds": round(time.monotonic() - started, 2)}


@app.websocket("/stt")
async def stt(ws: WebSocket):
    if SECRET:
        got = ws.headers.get("x-stt-secret")
        if got != SECRET:
            await ws.close(code=1008)
            log.warning("rejected a websocket with a bad secret")
            return

    await ws.accept()
    log.info("transcriber connected")

    sample_rate = 16000
    channels = 2
    buf: list[np.ndarray] = []
    speech_ms = 0.0
    silence_ms = 0.0
    in_speech = False
    since_partial = 0.0
    loop = asyncio.get_running_loop()

    async def send(text: str, final: bool) -> None:
        if not text:
            return
        await ws.send_text(json.dumps({
            "type": "transcriber-response",
            "transcription": text,
            "channel": "customer",
            "transcriptType": "final" if final else "partial",
        }))

    async def run_transcribe(audio: np.ndarray) -> str:
        return await loop.run_in_executor(_pool, transcribe, audio, sample_rate)

    try:
        while True:
            msg = await ws.receive()

            if msg.get("type") == "websocket.disconnect":
                break

            # --- control frame ---
            if msg.get("text") is not None:
                try:
                    data = json.loads(msg["text"])
                except json.JSONDecodeError:
                    continue
                if data.get("type") == "start":
                    sample_rate = int(data.get("sampleRate") or 16000)
                    channels = int(data.get("channels") or 2)
                    log.info("stream start: %dHz, %d channel(s), %s",
                             sample_rate, channels, data.get("encoding"))
                continue

            # --- audio frame ---
            chunk = msg.get("bytes")
            if not chunk:
                continue

            customer, _assistant = deinterleave(chunk, channels)
            if customer.size == 0:
                continue

            frame_ms = (customer.size / sample_rate) * 1000.0
            loud = rms(customer) > SILENCE_RMS

            if loud:
                buf.append(customer)
                speech_ms += frame_ms
                silence_ms = 0.0
                in_speech = True
                since_partial += frame_ms

                if since_partial >= PARTIAL_EVERY_MS and speech_ms >= MIN_SPEECH_MS:
                    since_partial = 0.0
                    audio = np.concatenate(buf)
                    text = await run_transcribe(audio)
                    await send(text, final=False)

                if speech_ms / 1000.0 >= MAX_UTTERANCE_S:
                    audio = np.concatenate(buf)
                    await send(await run_transcribe(audio), final=True)
                    buf, speech_ms, silence_ms, in_speech = [], 0.0, 0.0, False

            elif in_speech:
                # Keep a little trailing silence in the buffer: Whisper is more
                # accurate on the last word when it isn't cut off mid-breath.
                buf.append(customer)
                silence_ms += frame_ms

                if silence_ms >= SILENCE_TO_END_MS:
                    if speech_ms >= MIN_SPEECH_MS:
                        audio = np.concatenate(buf)
                        text = await run_transcribe(audio)
                        if text:
                            await send(text, final=True)
                            log.info("final (%.1fs speech): %s", speech_ms / 1000, text[:90])
                    buf, speech_ms, silence_ms, in_speech, since_partial = [], 0.0, 0.0, False, 0.0

    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("transcriber error")
    finally:
        # Flush anything still buffered when they hang up mid-sentence.
        if buf and speech_ms >= MIN_SPEECH_MS:
            try:
                text = transcribe(np.concatenate(buf), sample_rate)
                if text:
                    log.info("flushed on disconnect: %s", text[:90])
            except Exception:  # noqa: BLE001, S110  last flush on disconnect is best-effort
                pass
        log.info("transcriber disconnected")


if __name__ == "__main__":
    import uvicorn

    # Listens on every interface inside its own container; only the docker network and Caddy reach it (no published port).
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8092")))  # nosec B104
