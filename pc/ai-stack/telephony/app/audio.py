"""
G.711 mu-law codec and the audio plumbing for Twilio Media Streams.

Twilio speaks exactly one format on the phone: mu-law, 8000 Hz, mono, base64,
raw bytes with NO container header. Send it a WAV — header and all — and the
caller hears garbage. That mistake is the single most common way this breaks,
so encoding lives in its own file with its own tests.

Implemented in numpy rather than the stdlib `audioop` module on purpose:
audioop was deprecated in 3.11 and removed in 3.13, and a phone system that
stops working on a Python upgrade is not a phone system.
"""

from __future__ import annotations

import base64

import numpy as np

SAMPLE_RATE = 8000
FRAME_MS = 20
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000     # 160
FRAME_BYTES = FRAME_SAMPLES                        # mu-law is 1 byte/sample

_BIAS = 0x84
_CLIP = 32635


def _build_encode_table() -> np.ndarray:
    """int16 -> mu-law, precomputed for all 65536 values. Encoding is a lookup."""
    table = np.zeros(65536, dtype=np.uint8)
    for i in range(65536):
        sample = i - 32768
        sign = 0x80 if sample < 0 else 0x00
        if sample < 0:
            sample = -sample
        sample = min(sample, _CLIP)
        sample += _BIAS
        exponent = 7
        mask = 0x4000
        while exponent > 0 and not (sample & mask):
            exponent -= 1
            mask >>= 1
        mantissa = (sample >> (exponent + 3)) & 0x0F
        table[i] = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return table


def _build_decode_table() -> np.ndarray:
    table = np.zeros(256, dtype=np.int16)
    for i in range(256):
        u = ~i & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        sample = ((mantissa << 3) + _BIAS) << exponent
        sample -= _BIAS
        table[i] = -sample if sign else sample
    return table


_ENCODE = _build_encode_table()
_DECODE = _build_decode_table()


def pcm16_to_mulaw(pcm: np.ndarray) -> bytes:
    """int16 samples -> mu-law bytes."""
    pcm = np.asarray(pcm, dtype=np.int16)
    return _ENCODE[(pcm.astype(np.int32) + 32768).astype(np.uint16)].tobytes()


def mulaw_to_pcm16(data: bytes) -> np.ndarray:
    """mu-law bytes -> int16 samples."""
    return _DECODE[np.frombuffer(data, dtype=np.uint8)]


def mulaw_to_float(data: bytes) -> np.ndarray:
    """mu-law bytes -> float32 in [-1, 1], which is what Whisper wants."""
    return mulaw_to_pcm16(data).astype(np.float32) / 32768.0


def float_to_mulaw(audio: np.ndarray) -> bytes:
    """float32 in [-1, 1] -> mu-law bytes, clipped so peaks don't wrap."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 1.0:
        audio = audio / peak
    audio = np.clip(audio, -1.0, 1.0)
    return pcm16_to_mulaw((audio * 32767.0).astype(np.int16))


def resample_to_8k(audio: np.ndarray, src_rate: int) -> np.ndarray:
    """
    Anything -> 8000 Hz float32.

    Uses polyphase resampling, not sample-dropping. Naive decimation aliases
    badly, and on speech that sounds like a robot gargling — which is exactly
    what an 8kHz phone line will expose.
    """
    if src_rate == SAMPLE_RATE:
        return np.asarray(audio, dtype=np.float32)
    from math import gcd

    from scipy.signal import resample_poly

    g = gcd(int(src_rate), SAMPLE_RATE)
    return resample_poly(audio, SAMPLE_RATE // g, src_rate // g).astype(np.float32)


def frames(data: bytes, size: int = FRAME_BYTES):
    """Split mu-law bytes into 20ms frames, which is the cadence Twilio uses."""
    for i in range(0, len(data), size):
        chunk = data[i : i + size]
        if len(chunk) < size:
            chunk = chunk + b"\xff" * (size - len(chunk))  # 0xFF is mu-law silence
        yield chunk


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def unb64(payload: str) -> bytes:
    return base64.b64decode(payload)


def rms(audio: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(audio)))) if audio.size else 0.0
