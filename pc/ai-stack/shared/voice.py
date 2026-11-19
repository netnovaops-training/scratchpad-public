"""
Client for the cloned-voice TTS service.

Talks the OpenAI /v1/audio/speech shape, so the exact same call works against
our local Chatterbox container or against OpenAI/ElevenLabs if you ever want
to point it elsewhere -- change TTS_BASE_URL and nothing else.
"""

from __future__ import annotations

import os
from pathlib import Path

import httpx


class Voice:
    def __init__(
        self,
        base_url: str | None = None,
        voice: str | None = None,
        api_key: str = "none",
        timeout: float = 300.0,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("TTS_BASE_URL", "http://tts:4123/v1")
        ).rstrip("/")
        self.voice = voice or os.environ.get("TTS_DEFAULT_VOICE", "default")
        self.api_key = api_key
        self.timeout = timeout

    def available(self) -> bool:
        try:
            root = self.base_url.rsplit("/v1", 1)[0]
            r = httpx.get(f"{root}/health", timeout=5.0)
            return r.status_code == 200
        except Exception:  # noqa: BLE001  health probe: any failure means not available
            return False

    def list_voices(self) -> list[str]:
        try:
            root = self.base_url.rsplit("/v1", 1)[0]
            r = httpx.get(f"{root}/voices", timeout=10.0)
            r.raise_for_status()
            return [v["name"] for v in r.json().get("voices", [])]
        except Exception:  # noqa: BLE001  voice list is optional; empty on any failure
            return []

    def speak(
        self,
        text: str,
        *,
        voice: str | None = None,
        fmt: str = "wav",
        out_path: str | Path | None = None,
        normalize: bool = False,
        speed: float | None = None,
        steps: int | None = None,
    ) -> bytes:
        """Render text. Returns the audio bytes; also writes them if out_path given."""
        payload = {
            "model": "tts-1",
            "input": text,
            "voice": voice or self.voice,
            "response_format": fmt,
            # Ask the server to rewrite page-punctuation for the mouth --
            # dashes, call letters, ranges. Off by default: conversational
            # text is already written for the ear.
            "normalize": normalize,
        }
        if speed:
            # per-request pace override; otherwise the server's per-voice
            # VOICE_SPEEDS table decides
            payload["speed"] = speed
        if steps:
            # LuxTTS denoising steps (ignored by other engines): Telegram
            # pins 4 to stay the quick path while the container default is 16
            payload["steps"] = steps
        with httpx.Client(timeout=self.timeout) as c:
            r = c.post(
                f"{self.base_url}/audio/speech",
                json=payload,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            r.raise_for_status()
            data = r.content
        if out_path:
            p = Path(out_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
        return data


class Transcriber:
    """
    Speech -> text. Points at any OpenAI-compatible /v1/audio/transcriptions.
    Cloud is fine here; the point is getting a transcript we can store.
    Disabled (returns None) when no key is configured.
    """

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("STT_API_BASE", "https://api.openai.com/v1")
        ).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("STT_API_KEY", "")
        self.model = model or os.environ.get("STT_MODEL", "whisper-1")

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def transcribe(self, audio_bytes: bytes, filename: str = "audio.ogg") -> str | None:
        if not self.enabled:
            return None
        with httpx.Client(timeout=120.0) as c:
            r = c.post(
                f"{self.base_url}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (filename, audio_bytes, "application/octet-stream")},
                data={"model": self.model, "response_format": "text"},
            )
            r.raise_for_status()
            return r.text.strip()
