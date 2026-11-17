"""
Thin client for the LiteLLM router.

The only thing this adds over calling the OpenAI SDK directly is the idea of a
JOB. You ask for "coding" or "reasoning"; litellm/config.yaml decides which
model actually answers. Nothing in the app code names a real model.
"""

from __future__ import annotations

import os
from typing import Any, Iterable

from openai import OpenAI

# Jobs we expect litellm/config.yaml to define. Kept here only so the CLI and
# the bot can offer tab-completion / a /models list without a round trip.
KNOWN_JOBS = [
    "fast",
    "chat",
    "coding",
    "reasoning",
    "long-context",
    "vision",
]

# Cheap heuristics for `/job auto`. Deliberately dumb and readable -- if you
# want real classification, route through the `fast` model and ask it.
# NOTE: there is deliberately NO "vision" rule here.
#
# There used to be, matching phrases like "look at this" and "the diagram".
# It was actively harmful for two compounding reasons:
#
#   1. Neither surface can send an image to a model. Hermes and the Telegram
#      bot pass TEXT. So a vision model would receive "look at this error in
#      my nginx config" with no image attached and answer worse than the
#      coding model would have.
#
#   2. It is an 11B model, roughly 8GB of VRAM on a 12GB card. Loading it
#      EVICTS whatever was resident, so one message containing the words
#      "look at this" would swap out your chat and coding models and leave
#      them cold for the next ten minutes.
#
# `vision` is still a perfectly good route -- it is just one you ask for on
# purpose with /job vision, never one that gets picked for you.
_AUTO_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("coding", ("```", "def ", "class ", "function ", "import ", "traceback",
                "stack trace", "compile", "refactor", "unit test", "regex",
                "sql", "docker", "bug", "exception", "npm ", "git ",
                "script", "bash", "shell", "code", "api", "endpoint",
                "json", "yaml", "css", "html", "javascript", "python",
                "nginx", "deploy", "install", "config", "database",
                "error", "debug", "test ", "build ", "container")),
    ("reasoning", ("why ", "explain how", "prove", "step by step", "plan",
                   "trade-off", "tradeoff", "compare", "architect",
                   "strategy", "decide", "should i", "pros and cons",
                   "which is better")),
]


def pick_job(text: str, default: str = "chat") -> str:
    """Guess a job from the text. Used only when the user asks for auto mode."""
    low = text.lower()
    if len(low) > 6000:
        return "long-context"
    for job, needles in _AUTO_RULES:
        if any(n in low for n in needles):
            return job
    if len(low) < 120:
        return "fast"
    return default


class Router:
    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        default_job: str | None = None,
    ) -> None:
        self.base_url = base_url or os.environ.get(
            "ROUTER_BASE_URL", "http://litellm:4000/v1"
        )
        self.api_key = api_key or os.environ.get("ROUTER_API_KEY", "sk-none")
        self.default_job = default_job or os.environ.get("DEFAULT_JOB", "chat")
        self.client = OpenAI(
            base_url=self.base_url, api_key=self.api_key, timeout=600.0, max_retries=1
        )

    # -- discovery ------------------------------------------------------------

    def available_jobs(self) -> list[str]:
        """Ask the router what routes actually exist right now."""
        try:
            models = self.client.models.list()
            names = sorted({m.id for m in models.data})
            return names or list(KNOWN_JOBS)
        except Exception:
            return list(KNOWN_JOBS)

    # -- completion -----------------------------------------------------------

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        job: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        temperature: float = 0.7,
        **kwargs: Any,
    ):
        job = job or self.default_job
        payload: dict[str, Any] = {
            "model": job,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
            **kwargs,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return self.client.chat.completions.create(**payload)

    def complete_text(
        self, prompt: str, *, job: str | None = None, system: str | None = None
    ) -> str:
        msgs: list[dict[str, Any]] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": prompt})
        resp = self.chat(msgs, job=job)
        return (resp.choices[0].message.content or "").strip()

    # -- embeddings -----------------------------------------------------------

    def embed(self, texts: list[str], *, job: str = "embed") -> list[list[float]]:
        """Vectors for a list of strings, via the router's `embed` route.

        This is the one job a generalist model cannot do, which is why
        nomic-embed-text stays pulled even after the stack collapses to a
        single chat model. It is 274 MB and it is what makes per-person
        memory recall work.

        Callers treat failure as "no recall available", not as an error --
        see MemoryStore.recall, which falls back to substring matching.
        """
        resp = self.client.embeddings.create(model=job, input=texts)
        return [d.embedding for d in resp.data]

    def stream_text(
        self, messages: list[dict[str, Any]], *, job: str | None = None
    ) -> Iterable[str]:
        for chunk in self.chat(messages, job=job, stream=True):
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if delta and delta.content:
                yield delta.content
