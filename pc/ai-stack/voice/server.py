"""
The voice. One service, three consumers, one copy of the weights in VRAM.

  POST /synthesize-array   float array + sample rate. The phone line uses this
                           and does its own conversion to 8kHz mu-law.
  POST /v1/audio/speech    OpenAI-compatible, so Open WebUI's speaker button
                           and the Hermes CLI use the same voice.
  POST /preview            a playable WAV, for auditioning a voice in a browser.

Three engines, chosen with VOICE_ENGINE:

  kokoro      REMOVED. Preset voices only, cannot clone -- which made it the
              wrong tool here and, worse, a setting that silently ignored every
              recording you made. Selecting it now fails with an explanation.
  luxtts      ~1GB VRAM, Apache-2.0, clones from a 10-30s clip, ~150x realtime.
              This is how you get Dee's actual voice on the phone. Emits 48kHz.
  chatterbox  ~1.5GB, clones, supports [laugh]/[sigh] tags.

If a cloning engine fails to load, it falls back to Chatterbox rather than
leaving the phone mute.
"""

from __future__ import annotations

import io
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from scipy.signal import resample_poly

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("voice")

VOICES_DIR = Path(os.environ.get("VOICES_DIR", "/voices"))
ENGINE = os.environ.get("VOICE_ENGINE", "luxtts").lower()
DEVICE = os.environ.get("VOICE_DEVICE", "cuda")
DEFAULT_VOICE = os.environ.get("VOICE_DEFAULT", "dee")

# Per-voice pace, e.g. VOICE_SPEEDS="Randy=0.9,Randy-tuned=0.9". 1.0 = as
# synthesized; below slows without touching pitch (ffmpeg atempo). A voice
# absent from the table is never stretched -- Jason stays exactly Jason.
def _parse_speeds() -> dict:
    m = {}
    for pair in os.environ.get("VOICE_SPEEDS", "").split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            try:
                m[k.strip().lower()] = float(v)
            except ValueError:
                pass
    return m


VOICE_SPEEDS = _parse_speeds()


# Per-voice DELIVERY, not pace. VOICE_SPEEDS already handles speed; this is
# how forceful a voice is, which is a different complaint and had no
# mechanism until now -- the values could only be passed per REQUEST, so
# there was no way to say "Nellie is always like this".
#
#   VOICE_TUNE="Nellie=0.75:0.4,Randy=0.5:0.5"
#                      ^exaggeration ^cfg_weight
#
# exaggeration  how much emphasis and range. 0.5 is Chatterbox's neutral
#               read; higher is more forceful, past ~0.8 it starts to sound
#               theatrical.
# cfg_weight    how tightly it tracks the reference. LOWER follows the
#               reference's own delivery more closely and gives the
#               exaggeration room to act; higher is steadier but flatter.
#               The two work against each other, which is why they are set
#               as a pair.
def _parse_tune() -> dict:
    m = {}
    for pair in os.environ.get("VOICE_TUNE", "").split(","):
        if "=" not in pair:
            continue
        name, _, vals = pair.partition("=")
        parts = vals.split(":")
        try:
            exag = float(parts[0]) if parts[0] else None
            cfg = float(parts[1]) if len(parts) > 1 and parts[1] else None
        except ValueError:
            continue
        m[name.strip().lower()] = (exag, cfg)
    return m


VOICE_TUNE = _parse_tune()

# --- F5-TTS tuning -------------------------------------------------------
# Only read when VOICE_ENGINE=f5. Both are quality/speed dials rather than
# on/off switches, and Jason has said speed is not an issue on this box --
# so these are the two numbers to raise if a voice still is not close enough.
#   F5_NFE   function evaluations per step. Upstream default 32. More =
#            better prosody, linearly slower.
#   F5_CFG   how hard generation is pulled toward the reference clip.
#            Higher tracks the voice more closely, at some cost in
#            naturalness. Same trade as chatterbox's cfg_weight.
F5_MODEL = os.environ.get("F5_MODEL", "F5TTS_v1_Base")
F5_NFE = int(os.environ.get("F5_NFE_STEP", "32"))
F5_CFG = float(os.environ.get("F5_CFG_STRENGTH", "2.0"))

# Silence stitched between paragraph blocks when normalize is on. A blank
# line on the page is a long pause in the mouth.
PARAGRAPH_GAP = float(os.environ.get("PARAGRAPH_GAP_SECONDS", "0.65"))

# Trailing silence stitched onto every normalized render: a narration must
# END like a sentence -- a breath after the last word, never a hard cut.
TAIL_SECONDS = float(os.environ.get("TAIL_SECONDS", "0.8"))

# LuxTTS denoising steps: 4 = the distilled fast path, 8/16 = smoother for
# a slower render. Only the luxtts engine reads this.
LUXTTS_STEPS = int(os.environ.get("LUXTTS_STEPS", "4"))

# PACE. 0.70, chosen by measurement -- see the table in synthesize(). It is
# not 1.0 because generate_cpu() multiplies whatever it is given by 1.3, so
# 1.0 really asks for 1.3x and every sentence the house spoke came out at
# roughly twice the speed of ordinary speech. 0.70 x 1.3 = 0.91, which lands
# a 32-word sentence at 2.9 words per second and a 7-word one at 2.2 -- both
# inside the 2.0-3.0 band people actually talk in.
#
# Raise it and short phrases break before you notice the pace changed: at
# 0.80 a one-word answer returns 60ms of audio instead of erroring.
LUXTTS_SPEED = float(os.environ.get("LUXTTS_SPEED", "0.70"))

# The floor the silence guard uses, in seconds per word. Real speech at the
# pace above is about 0.35 s/word; anything under a tenth of that is not fast
# talking, it is a truncated generation.
# 0.12 s/word. Ordinary speech at the pace above runs about 0.35, so this
# still passes anything merely brisk. It was 0.035 -- a tenth of real speech --
# and that let a genuine failure through: a 24-word sentence came back as 0.99
# seconds of audio and cleared the check by a hair, which is exactly the
# silent truncation this guard exists to catch. A threshold set so low that
# the bug passes is not a guard, it is decoration.
MIN_SEC_PER_WORD = float(os.environ.get("LUXTTS_MIN_SEC_PER_WORD", "0.12"))


def stretch(audio, sr, speed):
    """Change pace, keep pitch, via ffmpeg atempo. Returns audio unchanged
    on any failure -- a full-speed voice beats a dead request."""
    import subprocess
    try:
        src = io.BytesIO()
        sf.write(src, audio, sr, format="WAV", subtype="PCM_16")
        p = subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
             "-filter:a", f"atempo={speed}", "-f", "wav", "pipe:1"],
            input=src.getvalue(), capture_output=True, check=True)
        out, _sr = sf.read(io.BytesIO(p.stdout), dtype="float32")
        return out
    except Exception:
        log.exception("stretch failed; serving full speed")
        return audio
SECRET = os.environ.get("VOICE_SECRET", "")
NATIVE_RATE = 48000   # LuxTTS output rate
KOKORO_RATE = 24000   # Kokoro output rate
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
KOKORO_LANG = os.environ.get("KOKORO_LANG", "a")   # 'a' = American English
AUDIO_EXTS = (".wav", ".mp3", ".flac", ".ogg", ".m4a")
PHONE_RATES = {8000, 16000, 22050, 24000, 44100}

app = FastAPI(title="ai-stack voice", version="1.0")

_engine = None
_engine_name = ""
_prompt_cache: dict[str, Any] = {}
_load_lock = threading.Lock()
_gen_lock = threading.Lock()

# ---------------------------------------------------------------------------
# HANDING THE CARD BACK
#
# This server loads its model lazily and, until 2026-09-20, never let go
# of it again. Six GPU containers doing that on one 12 GB card left 9.7 GB
# held with no requests in twenty-four hours, and the photo tagger's 12B
# vision model about 400 MB to work in -- which is why tagging one
# photograph took 17 seconds, then 46, then timed out.
#
# Lazy loading already worked. All that was missing was the ability to
# become unloaded a second time.
#
# THE COST IS THE FIRST REQUEST AFTER A QUIET SPELL, which pays a reload.
# Fifteen minutes is long enough that a conversation, a narration run or a
# phone call never pays it twice, and short enough that an idle overnight
# box gives the card back. Set VOICE_IDLE_MINUTES higher on anything that
# must answer instantly, or 0 to keep the old behaviour.
# ---------------------------------------------------------------------------
IDLE_MINUTES = float(os.environ.get("VOICE_IDLE_MINUTES", "15"))
_last_used = time.time()


def _touch():
    """Called on every request that needs the engine."""
    global _last_used
    _last_used = time.time()


def _release_engine():
    """Drop the model and give the VRAM back.

    REFUSES WHILE SPEECH IS BEING MADE. The first version of this checked
    only the idle clock, and the clock is stamped when the engine is
    FETCHED, not while it is working. A synthesis longer than the idle
    window therefore had its model freed underneath it mid-sentence --
    caught with a one-minute timer on 2026-09-21, where the request then
    hung until the client gave up at 400s. _gen_lock is already held for
    the whole of generation, so trying to take it is the honest test of
    whether anybody is mid-sentence.
    """
    if not _gen_lock.acquire(blocking=False):
        return                      # busy speaking; try again next tick
    try:
        _release_engine_locked()
    finally:
        _gen_lock.release()


def _release_engine_locked():
    global _engine, _engine_name
    with _load_lock:
        if _engine is None:
            return
        name = _engine_name
        _engine = None
        _engine_name = ""
        _prompt_cache.clear()
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001, S110  freeing GPU memory is best-effort
            # Freeing is best-effort. Python dropping the last reference is
            # what actually matters; empty_cache only returns the allocator's
            # spare blocks to the driver.
            pass
        log.info("idle %.0f min -- released %s and freed the GPU",
                 IDLE_MINUTES, name or "the engine")


def _idle_watcher():
    while True:
        time.sleep(30)
        try:
            if _engine is not None and (time.time() - _last_used) > IDLE_MINUTES * 60:
                _release_engine()
        except Exception as exc:  # noqa: BLE001
            log.warning("idle watcher: %s", exc)


if IDLE_MINUTES > 0:
    threading.Thread(target=_idle_watcher, daemon=True).start()


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


def _resolve_device() -> str:
    if DEVICE == "cuda":
        try:
            import torch

            if not torch.cuda.is_available():
                log.warning("VOICE_DEVICE=cuda but no CUDA visible -- using cpu")
                return "cpu"
        except Exception:  # noqa: BLE001
            return "cpu"
    return DEVICE


def _needs_reference() -> bool:
    """Kokoro uses preset voices; the cloning engines need a sample."""
    return ENGINE != "kokoro"


def find_voice(name: str) -> Path | None:
    if not name:
        return None
    for ext in AUDIO_EXTS:
        p = _in_voices(f"{name}{ext}")
        if p is not None and p.exists():
            return p
    return None


def _in_voices(rel: str) -> Path | None:
    """VOICES_DIR/rel as a real path, or None if it would land outside VOICES_DIR.

    2026-11-20: a voice name comes from the request, so "../x" or a symlink
    can no longer reach a file outside the voices folder; such a name is
    simply "no such voice" (the callers already handle None).
    """
    base = os.path.realpath(str(VOICES_DIR))
    full = os.path.realpath(os.path.join(base, rel))
    if full == base or not full.startswith(base.rstrip(os.sep) + os.sep):
        return None
    return Path(full)


def list_voices() -> list[str]:
    if not VOICES_DIR.exists():
        return []
    return sorted({p.stem for p in VOICES_DIR.iterdir() if p.suffix.lower() in AUDIO_EXTS})


def get_engine():
    """Load lazily, and reload if the idle watcher has let it go."""
    global _engine, _engine_name
    _touch()
    if _engine is not None:
        return _engine
    with _load_lock:
        if _engine is not None:
            return _engine
        dev = _resolve_device()

        if ENGINE == "kokoro":
            # Kokoro was REMOVED from this image deliberately. It has 50 preset
            # voices and cannot clone -- and cloning is the entire point here,
            # since the stack speaks as Jason, Dee and Randy.
            #
            # Failing with a sentence beats an ImportError from a package that
            # isn't installed, which would send you looking for a broken build
            # rather than a stale setting.
            raise RuntimeError(
                "VOICE_ENGINE=kokoro, but Kokoro is no longer installed. It has "
                "only preset voices and cannot clone. Set VOICE_ENGINE=luxtts in "
                ".env, then: docker compose up -d --force-recreate voice"
            )

        if ENGINE == "luxtts":
            try:
                from zipvoice.luxvoice import LuxTTS

                log.info("loading LuxTTS on %s", dev)
                _engine = LuxTTS("YatharthS/LuxTTS", device=dev)
                _engine_name = "luxtts"
                log.info("LuxTTS ready")
                return _engine
            except Exception as exc:  # noqa: BLE001
                log.error("LuxTTS failed to load (%s) -- falling back to chatterbox", exc)

        if ENGINE in ("f5", "f5-tts", "f5tts"):
            # F5-TTS -- the quality step up, added 2026-09-13.
            #
            # WHY THIS ONE. Jason asked for "the next voice TTS model that's
            # a step up". F5-TTS is the flow-matching model documented as the
            # quality leader for English, and specifically better than
            # Chatterbox on LONG-FORM prosody -- which is the exact complaint
            # that started this ("Jason judged the tribute's narration
            # robotic, and he was right"). It wants ~3GB of VRAM, less than
            # Chatterbox's 4GB.
            #
            # WHAT WAS REJECTED AND WHY, so nobody re-litigates it later:
            #   Qwen3-TTS 1.7B  needs >=12GB on a 12GB card, and has
            #                   documented run-to-run timbre drift -- the
            #                   opposite of the accuracy being asked for.
            #   Voxtral TTS     needs 16GB. Does not fit, full stop.
            #   Qwen3-TTS 0.6B  fits, but is smaller than what is already here.
            #
            # THE ONE REAL DIFFERENCE IN USE: infer() REQUIRES ref_text, the
            # transcript of the reference clip, and does not transcribe for
            # you. voice/transcribe-references.py writes those .txt files
            # using the faster-whisper service already in this stack.
            from f5_tts.api import F5TTS

            log.info("loading F5-TTS (%s) on %s", F5_MODEL, dev)
            _engine = F5TTS(model=F5_MODEL, device=dev)
            _engine_name = "f5"
            log.info("F5-TTS ready")
            return _engine

        if ENGINE in ("chatterbox-turbo", "chatterbox_turbo", "turbo"):
            # 350M Turbo variant -- needs the git build of chatterbox
            # (Dockerfile.turbo); the PyPI 0.1.7 in the base image predates
            # it. Import lives inside the branch so the base image never
            # touches it. Same generate()/sr API as its big sibling, so the
            # synthesize() fallthrough below serves both.
            from chatterbox.tts_turbo import ChatterboxTurboTTS

            log.info("loading Chatterbox Turbo on %s", dev)
            _engine = ChatterboxTurboTTS.from_pretrained(device=dev)
            _engine_name = "chatterbox-turbo"
            log.info("Chatterbox Turbo ready")
            return _engine

        from chatterbox.tts import ChatterboxTTS

        log.info("loading Chatterbox on %s", dev)
        _engine = ChatterboxTTS.from_pretrained(device=dev)
        _engine_name = "chatterbox"
        log.info("Chatterbox ready")
        return _engine


def _encoded_prompt(engine, ref: Path):
    """LuxTTS wants the reference clip encoded once; cache it per voice.

    CAUGHT LIVE: engine.encode_prompt() (zipvoice/luxvoice.py) hands its
    argument straight to librosa.load() internally -- it wants a PATH, not
    decoded samples. Passing the pre-read numpy array here produced
    "TypeError: Invalid file: array([...])" on every first encode of every
    voice. sf.read() below is now only for the log line's duration.
    """
    key = str(ref)
    if key in _prompt_cache:
        return _prompt_cache[key]
    audio, sr = sf.read(str(ref), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    _prompt_cache[key] = engine.encode_prompt(str(ref), rms=0.01)
    log.info("encoded voice prompt for %s (%.1fs @ %dHz)", ref.name, len(audio) / sr, sr)
    return _prompt_cache[key]


def synthesize(text: str, ref: Path | None,
               steps: int | None = None,
               exaggeration: float | None = None,
               cfg_weight: float | None = None) -> tuple[np.ndarray, int]:
    """Returns float32 mono audio in [-1, 1] and its sample rate."""
    engine = get_engine()
    with _gen_lock:  # one at a time; keeps VRAM predictable
        _touch()     # and again on the way out, below -- a long synthesis
                     # must not look idle just because it started a while ago
        if _engine_name == "kokoro":
            chunks = []
            for _gs, _ps, chunk in engine(text, voice=KOKORO_VOICE):
                arr = chunk.detach().cpu().numpy() if hasattr(chunk, "detach") else np.asarray(chunk)
                chunks.append(np.asarray(arr, dtype=np.float32).squeeze())
            if not chunks:
                return np.zeros(0, dtype=np.float32), KOKORO_RATE
            return np.concatenate(chunks), KOKORO_RATE

        if _engine_name == "luxtts":
            # SHORT PHRASES USED TO RETURN 500. "Yes.", "Okay." and "Testing
            # the warm path." all died in the vocoder with:
            #
            #   Calculated padded input size per channel: (6). Kernel size: (7)
            #
            # It reads like a minimum-length limit on the text. It is not.
            # generate_cpu() multiplies the requested speed by 1.3 -- its own
            # comment says "default is too slow" -- so asking for normal pace
            # actually asks for 1.3x, and 1.3x of a two-word phrase predicts
            # fewer frames than the vocoder's 7-frame kernel can convolve.
            # The built-in speed-up is the cause; the brevity only exposes it.
            #
            # That speed-up is also why every sentence was rushed, so the
            # default pace moved to LUXTTS_SPEED (0.70) rather than being a
            # retry -- which fixes the crash and the pace in one change:
            #
            #     speed   32 words   7 words   "Yes."
            #     1.00      5.19       9.94    crashes
            #     0.80      3.57       3.28    0.06s of audio (truncated)
            #     0.70      2.92       2.22    0.79s
            #     0.60      2.35       1.55    1.75s
            #     0.50      1.85       1.09    3.10s (padding with silence)
            #
            # Words per second, measured on this box with this reference. The
            # 0.5 rung exists for a phrase shorter than anything in that table;
            # the ladder stops there because below it the model stops speaking
            # slower and starts appending silence, and a caller would rather
            # have an error than a voice that trails off into nothing.
            #
            # Only the vocoder frame-size error is retried. Any other
            # RuntimeError is re-raised untouched -- a retry that swallows
            # unknown faults is how a broken voice becomes a mysteriously slow
            # one instead of a reported one.
            enc = _encoded_prompt(engine, ref)
            words = max(1, len(text.split()))
            wav = None
            last = None
            for attempt_speed in (LUXTTS_SPEED, 0.50):
                try:
                    cand = engine.generate_speech(
                        text, enc, num_steps=steps or LUXTTS_STEPS,
                        speed=attempt_speed)
                except RuntimeError as exc:
                    if "Kernel size" not in str(exc):
                        raise
                    last = f"too short for the vocoder at speed {attempt_speed:.2f}"
                    log.info("luxtts: %r %s, retrying slower", text[:40], last)
                    continue

                # THE SILENCE GUARD. generate_speech does not always fail
                # loudly: at speed 0.80 a one-word phrase came back as 60ms of
                # audio with no exception raised. That is indistinguishable
                # from a working voice until somebody presses play, so a result
                # too short to be speech is treated exactly like the crash.
                n = cand.numel() if hasattr(cand, "numel") else len(cand)
                secs = n / float(NATIVE_RATE)
                if secs < words * MIN_SEC_PER_WORD:
                    last = (f"{secs:.3f}s for {words} word(s) at speed {attempt_speed:.2f} -- truncated")
                    log.warning("luxtts: %r %s, retrying slower", text[:40], last)
                    continue

                wav = cand
                break

            if wav is None:
                raise RuntimeError(
                    f"LuxTTS could not synthesize {text[:60]!r}: {last}. It is too short for "
                    "the vocoder 7-frame kernel even at the slowest pace -- "
                    "give it a longer phrase."
                )
            arr = wav.numpy() if hasattr(wav, "numpy") else np.asarray(wav)
            return np.asarray(arr, dtype=np.float32).squeeze(), NATIVE_RATE

        if _engine_name == "f5":
            # F5-TTS needs the transcript of the reference clip. It lives
            # beside the wav, written by transcribe-references.py. Without a
            # MATCHING transcript the clone degrades quietly rather than
            # erroring, so an absent one is a hard failure with a sentence
            # that says what to run -- not a silent guess.
            ref_txt = _in_voices(os.path.relpath(str(ref.with_suffix(".txt")), os.path.realpath(str(VOICES_DIR))))
            if ref_txt is None or not ref_txt.exists():
                raise RuntimeError(
                    f"F5-TTS needs a transcript for {ref.name}. Run: "
                    f"docker exec voice python /app/voice/transcribe-references.py --apply"
                )
            ref_text = ref_txt.read_text(encoding="utf-8", errors="replace").strip()
            wav, sr, _spec = engine.infer(
                ref_file=str(ref),
                ref_text=ref_text,
                gen_text=text,
                # nfe_step is the quality/speed dial: more function
                # evaluations, better prosody, slower render. 32 is the
                # upstream default; Jason has said speed is not an issue, so
                # this is the knob to raise if the voice still is not right.
                nfe_step=F5_NFE,
                # cfg_strength: how hard it is pulled toward the reference.
                # Higher tracks the voice more closely at some cost in
                # naturalness -- the same trade as chatterbox's cfg_weight.
                # A per-voice cfg from VOICE_TUNE wins over the global one,
                # so a single voice can be tuned without moving every other.
                cfg_strength=(VOICE_TUNE.get(ref.stem.lower(), (None, None))[1]
                              or F5_CFG),
                speed=VOICE_SPEEDS.get(ref.stem.lower(), 1.0),
                remove_silence=True,
            )
            arr = wav.detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav)
            return np.asarray(arr, dtype=np.float32).squeeze(), int(sr)

        # Per-voice defaults from VOICE_TUNE, overridden by anything the
        # request asked for explicitly. Order matters: a caller who names a
        # value means it, and the config is only a default.
        tuned_exag, tuned_cfg = VOICE_TUNE.get(ref.stem.lower(), (None, None))
        kw = {}
        if (exaggeration if exaggeration is not None else tuned_exag) is not None:
            kw["exaggeration"] = (exaggeration if exaggeration is not None
                                  else tuned_exag)
        if (cfg_weight if cfg_weight is not None else tuned_cfg) is not None:
            kw["cfg_weight"] = (cfg_weight if cfg_weight is not None
                                else tuned_cfg)
        wav = engine.generate(text, audio_prompt_path=str(ref), **kw)
        arr = wav.detach().cpu().numpy() if hasattr(wav, "detach") else np.asarray(wav)
        return np.asarray(arr, dtype=np.float32).squeeze(), int(engine.sr)


# ---------------------------------------------------------------------------
# audio conversion — this is where calls go silent if it's wrong
# ---------------------------------------------------------------------------


def to_raw_pcm(audio: np.ndarray, src_rate: int, dst_rate: int) -> bytes:
    """float32 mono at src_rate  ->  raw 16-bit LE PCM mono at dst_rate."""
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = np.asarray(audio, dtype=np.float32)

    if src_rate != dst_rate:
        # resample_poly instead of a naive stride: decimating by picking every
        # Nth sample aliases badly, and on speech that sounds like a robot
        # underwater. This does the anti-alias filtering properly.
        from math import gcd

        g = gcd(int(src_rate), int(dst_rate))
        audio = resample_poly(audio, dst_rate // g, src_rate // g).astype(np.float32)

    # Clip before scaling. Without this, any sample above 1.0 wraps around at
    # int16 and produces a loud click in the caller's ear.
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1.0:
        audio = audio / peak
    audio = np.clip(audio, -1.0, 1.0)

    return (audio * 32767.0).astype("<i2").tobytes()


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------


def check_secret(request: Request) -> None:
    if not SECRET:
        return
    got = request.headers.get("x-voice-secret")
    if got != SECRET:
        raise HTTPException(status_code=401, detail="bad or missing secret")


@app.get("/health")
def health():
    return {
        "status": "ok",
        "engine": _engine_name or f"{ENGINE} (not loaded yet)",
        "device": _resolve_device(),
        "loaded": _engine is not None,
        "voices": list_voices(),
        "default_voice": DEFAULT_VOICE if _needs_reference() else KOKORO_VOICE,
        "clones_voices": _needs_reference(),
        "native_rate": NATIVE_RATE,
    }


@app.post("/warm")
def warm():
    """
    Pre-load the model and pre-encode the voice prompt.

    Call this after the stack starts. Without it the FIRST caller of the day
    waits through model load plus prompt encoding — many seconds of silence
    while they say "hello? hello?" and hang up.
    """
    started = time.monotonic()
    ref = find_voice(DEFAULT_VOICE)
    if ref is None:
        return {"warmed": False, "reason": f"no clip at voices/{DEFAULT_VOICE}.wav"}
    audio, sr = synthesize("Hello, this is a warm up.", ref)
    return {
        "warmed": True,
        "engine": _engine_name,
        "seconds": round(time.monotonic() - started, 2),
        "samples": int(audio.size),
        "rate": sr,
    }


@app.post("/synthesize")
@app.post("/voice")
async def raw_pcm_request(request: Request):
    check_secret(request)
    started = time.monotonic()

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="body was not JSON")

    msg = payload.get("message") or payload
    text = (msg.get("text") or "").strip()
    rate = int(msg.get("sampleRate") or 24000)

    if not text:
        # Empty text is not an error on a phone call -- return a beat of silence
        # rather than a 400 that would surface as a failed call.
        return Response(content=b"\x00\x00" * (rate // 50), media_type="application/octet-stream")

    if rate not in PHONE_RATES:
        log.warning("unusual sampleRate %s requested; honouring it anyway", rate)

    voice_name = (msg.get("voice") or DEFAULT_VOICE)
    ref = find_voice(voice_name) or find_voice(DEFAULT_VOICE)
    if ref is None:
        log.error("no reference clip for '%s' and no default", voice_name)
        raise HTTPException(
            status_code=400,
            detail=f"No voice clip. Put a 10-30s sample at voices/{DEFAULT_VOICE}.wav. "
                   f"Available: {list_voices() or 'none'}",
        )

    try:
        audio, src_rate = synthesize(text[:2000], ref)
    except Exception as exc:
        log.exception("synthesis failed")
        raise HTTPException(status_code=500, detail=f"synthesis failed: {exc}") from exc

    pcm = to_raw_pcm(audio, src_rate, rate)
    took = time.monotonic() - started
    audio_seconds = len(pcm) / 2 / rate
    log.info(
        "spoke %d chars -> %.1fs audio @ %dHz in %.0fms (%.1fx realtime)",
        len(text), audio_seconds, rate, took * 1000,
        (audio_seconds / took) if took else 0,
    )
    return Response(content=pcm, media_type="application/octet-stream")




# -----------------------------------------------------------------------------
#  Narration normalizer (2026-08-31).
#
#  The tribute's tellings are written for the EYE: em-dashes, curly quotes,
#  middle dots, "R&B", call letters like WABY, year ranges like 1923-1992.
#  Every TTS engine stumbles on exactly those -- reading a dash as a word,
#  swallowing a pause, spelling nothing or everything. This pass rewrites
#  page-punctuation into speech-punctuation before the engine ever sees it.
#
#  OPT-IN via "normalize": true in the request, because the phone line's
#  conversational text is written for the ear already and double-processing
#  real speech would mangle it. The tribute's renderer and the Telegram
#  /sample command both ask for it.
# -----------------------------------------------------------------------------
import re as _re


def _squash_ws(m) -> str:
    run = m.group(0)
    if "\n" in run:
        return "\n"
    # 2026-11-20: every other run (spaces, tabs, \r, no-break spaces...) is
    # now one space. Before, "anything stranger" was left as it was, so a
    # long run of \r or U+00A0 still made the patterns below slow.
    return " "


def normalize_narration(text: str) -> str:
    t = text
    # emojis and pictographs are for the eye only -- never spoken
    t = _re.sub("["
                "\U0001F000-\U0001FAFF"   # pictographs, faces, symbols
                # 2026-11-20: the two Unicode blocks are written as two ranges
                # (same characters as before), so each is plainly one block.
                "\u2600-\u26ff"           # misc symbols
                "\u2700-\u27bf"           # dingbats
                # flag letters (U+1F1E6-1F1FF) are inside the first range already;
                # 2026-11-20: their separate range was dropped (CodeQL
                # py/overly-large-range, alert 3041, saw the two overlap).
                "\u2b00-\u2bff"           # arrows, stars
                "\ufe0e\ufe0f\u200d"     # variation selectors, joiner
                "]+", " ", t)
    # markdown is typography, not prose: **bold**, *em*, `code`, # headers
    t = t.replace("**", "").replace("`", "")
    t = _re.sub(r"(?<!\w)\*(\S[^*\n]*?)\*(?!\w)", r"\1", t)
    t = t.replace("*", "")
    t = _re.sub(r"^\s*#{1,6}\s+", "", t, flags=_re.MULTILINE)
    # typographic quotes and apostrophes -> plain (engines read them as noise)
    t = t.replace("\u2018", "'").replace("\u2019", "'")
    t = t.replace("\u201c", "").replace("\u201d", "")
    t = t.replace('"', "")
    # 2026-11-20: shrink every run of 2+ whitespace characters to one before the
    # whitespace-hungry patterns below. A long run of spaces made them
    # re-scan it from every position (polynomial time, ~0.5 s for 5000
    # spaces). A run with a newline becomes "\n", any other run becomes " "
    # (2026-11-20 later: odd whitespace too, see _squash_ws). The spoken result
    # is identical (checked against the old code on random input).
    t = _re.sub(r"\s{2,}", _squash_ws, t)
    # year/number ranges: 1923-1992 (any dash) -> "1923 to 1992"
    t = _re.sub(r"(\d)\s*[\u2013\u2014-]\s*(\d)", r"\1 to \2", t)
    # em/en dashes and middle dots are PAUSES on the page; commas in the mouth
    t = _re.sub(r"\s*[\u2013\u2014]\s*", ", ", t)
    t = _re.sub(r"\s*\u00b7\s*", ", ", t)
    t = _re.sub(r"\s+--\s+", ", ", t)
    # ellipses trail off; a comma breathes better than three dots read aloud
    t = t.replace("\u2026", ",").replace("...", ",")
    # semicolons are commas with ambition
    t = t.replace(";", ",")
    # colons are silent beats -- except inside a clock time like 7:30
    t = _re.sub(r"(?<!\d):(?!\d)", ",", t)
    # ampersand words
    # a slash between words is an "or" ("his/her", "yes/no") -- never the
    # word "slash". Digits keep theirs: 24/7 and 8/31 are rhythms and dates.
    t = _re.sub(r"\band/or\b", "and or", t)
    t = _re.sub(r"(?<=[A-Za-z])\s*/\s*(?=[A-Za-z])", " or ", t)
    t = _re.sub(r"\bR&B\b", "R and B", t)
    # brand names people say as one word, never letter-by-letter
    t = _re.sub(r"\b[Mm]\s*&\s*[Mm](?:'?[Ss])?\b", "Eminems", t)
    t = t.replace("&", " and ")
    # a spaced hyphen is a pause on the page, same as a dash
    t = _re.sub(r"(?<=\w)\s+-\s+(?=\w)", ", ", t)
    # one exclamation carries the excitement; a pile of them makes engines stutter
    t = _re.sub(r"!{2,}", "!", t)
    # US radio call letters (W/K + 2-3 caps): spell them out -- "WABY" the
    # word sounds like a baby; "W A B Y" sounds like a station
    t = _re.sub(r"\b([WK])([A-Z]{2,3})\b",
                lambda m: " ".join(m.group(1) + m.group(2)), t)
    # DJ reads better spelled
    t = _re.sub(r"\bDJs\b", "D Js", t)
    t = _re.sub(r"\bDJ\b", "D J", t)
    # parentheses become comma asides
    t = _re.sub(r"\s*\(\s*", ", ", t)
    t = _re.sub(r"\s*\)\s*", ", ", t)
    # tidy: collapse comma runs and whitespace
    t = _re.sub(r"(,\s*)+", ", ", t)
    t = _re.sub(r"\s+([.!?])", r"\1", t)
    t = _re.sub(r"[ \t]+", " ", t)
    t = _re.sub(r"\s*\n\s*", " ", t)
    t = _re.sub(r"(,\s*)+", ", ", t)
    t = t.strip()
    # the last word gets a full stop -- an unterminated final sentence is
    # what makes an engine slam the door instead of setting the mic down
    if t and t[-1] not in ".!?":
        t += "."
    return t


class ArrayIn(BaseModel):
    text: str
    voice: str | None = None


@app.post("/synthesize-array")
def synthesize_array(req: ArrayIn):
    """
    Speech as a plain float array, for the telephony service.

    Returns the engine's native sample rate and lets the caller resample --
    the phone line needs 8kHz mu-law and doing that conversion once, close to
    where it's used, beats doing it twice.
    """
    ref = None
    if _needs_reference():
        ref = find_voice(req.voice or DEFAULT_VOICE) or find_voice(DEFAULT_VOICE)
        if ref is None:
            raise HTTPException(
                status_code=400,
                detail=f"engine '{ENGINE}' clones a voice and needs a sample. "
                       f"Put a 10-30s clip at voices/{DEFAULT_VOICE}.wav, or set "
                       f"VOICE_ENGINE=kokoro to use a preset voice.",
            )
    audio, sr = synthesize(req.text[:2000], ref)
    return {"audio": audio.tolist(), "sample_rate": sr}


@app.get("/v1/models")
def openai_models():
    return {"object": "list", "data": [{"id": "tts-1", "object": "model", "owned_by": "ai-stack"}]}


@app.get("/voices")
def voices():
    return {"voices": list_voices(), "default": DEFAULT_VOICE}


@app.post("/v1/audio/speech")
async def openai_speech(request: Request):
    """
    OpenAI-compatible speech, so the SAME cloned voice serves Open WebUI's
    speaker button and the Hermes CLI, not just the phone. One voice service,
    three consumers -- rather than a second TTS container holding a second copy
    of the weights in VRAM.
    """
    check_secret(request)
    payload = await request.json()
    text = (payload.get("input") or "").strip()
    if payload.get("normalize"):
        # a blank line is a LONG pause: real silence between blocks below
        blocks = [normalize_narration(b) for b in
                  _re.split(r"\n\s*\n", text[:5000])]
        blocks = [b for b in blocks if b]
    else:
        blocks = [text[:5000]] if text else []
    if not blocks:
        raise HTTPException(status_code=400, detail="input is empty")
    text = " ".join(blocks)  # kept for logs/limits

    name = payload.get("voice") or DEFAULT_VOICE
    # Ignore OpenAI's stock voice names unless a clip of that name exists.
    if name in {"alloy", "echo", "fable", "onyx", "nova", "shimmer"} and not find_voice(name):
        name = DEFAULT_VOICE
    ref = find_voice(name) or find_voice(DEFAULT_VOICE)
    if ref is None:
        raise HTTPException(
            status_code=400,
            detail=f"No voice clip. Put a 10-30s sample at voices/{DEFAULT_VOICE}.wav. "
                   f"Available: {list_voices() or 'none'}",
        )

    def _f(key):
        try:
            return float(payload[key]) if payload.get(key) is not None else None
        except (TypeError, ValueError):
            return None

    try:
        req_steps = int(payload.get("steps")) if payload.get("steps") else None
    except (TypeError, ValueError):
        req_steps = None
    req_exag, req_cfg = _f("exaggeration"), _f("cfg_weight")
    req_gap = _f("gap")
    gap_s = req_gap if req_gap is not None and 0.0 <= req_gap <= 5.0 else PARAGRAPH_GAP
    parts, sr = [], None
    for b in blocks:
        a, sr = synthesize(b, ref, steps=req_steps,
                           exaggeration=req_exag, cfg_weight=req_cfg)
        parts.append(np.asarray(a).squeeze())
    if len(parts) == 1:
        audio = parts[0]
    else:
        gap = np.zeros(int(gap_s * sr), dtype=parts[0].dtype)
        stitched = []
        for i, a in enumerate(parts):
            stitched.append(a)
            if i < len(parts) - 1:
                stitched.append(gap)
        audio = np.concatenate(stitched)
    if payload.get("normalize") and TAIL_SECONDS > 0 and sr:
        audio = np.concatenate(
            [audio, np.zeros(int(TAIL_SECONDS * sr), dtype=audio.dtype)])
    try:
        speed = float(payload.get("speed") or VOICE_SPEEDS.get(name.lower(), 1.0))
    except (TypeError, ValueError):
        speed = 1.0
    if 0.5 <= speed <= 2.0 and abs(speed - 1.0) >= 0.01:
        audio = stretch(audio, sr, speed)
    fmt = (payload.get("response_format") or "wav").lower()
    buf = io.BytesIO()
    if fmt in ("mp3", "flac", "ogg", "opus"):
        target = {"mp3": "MP3", "flac": "FLAC", "ogg": "OGG", "opus": "OGG"}[fmt]
        try:
            sf.write(buf, audio, sr, format=target)
            media = {"MP3": "audio/mpeg", "FLAC": "audio/flac", "OGG": "audio/ogg"}[target]
        except Exception:  # noqa: BLE001
            buf = io.BytesIO()
            sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
            media = "audio/wav"
    else:
        sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
        media = "audio/wav"
    return Response(content=buf.getvalue(), media_type=media)


@app.post("/preview")
async def preview(request: Request):
    """Same synthesis, but returns a playable WAV so you can listen in a browser."""
    check_secret(request)
    payload = await request.json()
    text = (payload.get("text") or "Hello, this is a test of the voice.").strip()
    ref = find_voice(payload.get("voice") or DEFAULT_VOICE) or find_voice(DEFAULT_VOICE)
    if ref is None:
        raise HTTPException(status_code=400, detail="no reference clip available")
    audio, sr = synthesize(text[:2000], ref)

    # APPLY THE SAME PACE THE REAL ENDPOINTS DO. This was missing, and it
    # made the audition endpoint lie: /v1/audio/speech stretches by
    # VOICE_SPEEDS and /preview did not, so a voice slowed down in config
    # still previewed at full speed. You tune it, it sounds right in use and
    # wrong in the preview -- or you conclude the setting did nothing and
    # revert a change that was working. Found 2026-09-13 slowing Nellie
    # down: the render came back 8.76s against 8.84s, which is noise, and
    # the reason was the endpoint, not the setting.
    try:
        speed = float(payload.get("speed") or VOICE_SPEEDS.get(ref.stem.lower(), 1.0))
    except (TypeError, ValueError):
        speed = 1.0
    if 0.5 <= speed <= 2.0 and abs(speed - 1.0) >= 0.01:
        audio = stretch(audio, sr, speed)

    buf = io.BytesIO()
    sf.write(buf, audio, sr, format="WAV", subtype="PCM_16")
    return Response(content=buf.getvalue(), media_type="audio/wav")


@app.exception_handler(Exception)
def unhandled(_request: Request, exc: Exception):
    log.exception("voice error")
    return JSONResponse(status_code=500, content={"detail": str(exc)})


if __name__ == "__main__":
    import uvicorn

    # Listens on every interface inside its own container; only the docker network and Caddy reach it (no published port).
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8091")))  # nosec B104
