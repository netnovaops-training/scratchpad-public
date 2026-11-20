"""Talking to LiteLLM, and getting JSON back from something that speaks prose.

WHY THERE IS A RETRY AND A REPAIR PASS
--------------------------------------
`response_format={"type":"json_object"}` is honoured by OpenAI. Ollama behind
LiteLLM honours it MOSTLY -- an 8B model asked for JSON usually returns JSON,
and occasionally returns JSON wrapped in ```json fences, or JSON with a
sentence of introduction, or JSON with a trailing comma. All three are a
crash in `json.loads` and all three are trivially recoverable.

So: ask, try to parse, and if that fails, salvage the outermost {...} and try
again. Only if THAT fails do we go back to the model. One retry, not a loop --
a model that has produced garbage twice is usually going to keep producing it,
and by then the user has been staring at a spinner for a minute.

WHY `num_ctx` IS NOT SET HERE
-----------------------------
It is set per-job in litellm/config.yaml, which is the one place that knows how
much VRAM the card has. Setting it here would silently override that and is
exactly the kind of second source of truth that makes "why is it slow" take an
afternoon.
"""
import base64
import json
import re

import httpx

from . import core


class LLMError(Exception):
    """The model could not be reached or would not answer usefully."""


_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S)


def _salvage(text: str):
    """Get an object out of text that is nearly JSON."""
    m = _FENCE.search(text)
    if m:
        text = m.group(1)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise LLMError("The model did not return anything shaped like a recipe.")
    blob = text[start:end + 1]
    # Trailing commas before } or ] - the single most common malformation.
    blob = re.sub(r",\s*([}\]])", r"\1", blob)
    return json.loads(blob)


async def chat(system: str, user: str, *, model: str, want_json: bool = False,
               temperature: float = 0.2, max_tokens: int = 2048) -> str:
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if want_json:
        body["response_format"] = {"type": "json_object"}

    headers = {"Content-Type": "application/json"}
    if core.LLM_KEY:
        headers["Authorization"] = f"Bearer {core.LLM_KEY}"

    async with httpx.AsyncClient(timeout=core.LLM_TIMEOUT) as client:
        try:
            r = await client.post(f"{core.LLM_BASE}/chat/completions",
                                  json=body, headers=headers)
        except httpx.RequestError as e:
            raise LLMError(
                "I could not reach the model router. Is the `litellm` container "
                f"running? ({e.__class__.__name__})"
            )
    if r.status_code >= 400:
        raise LLMError(f"The model router said {r.status_code}: {r.text[:300]}")

    data = r.json()
    try:
        message = data["choices"][0]["message"]
    except (KeyError, IndexError):
        raise LLMError("The model router returned a reply with no message in it.")

    content = message.get("content") or ""
    if content.strip():
        return content

    # EMPTY CONTENT IS A REAL AND CONFUSING FAILURE, so it is named here
    # rather than left to become "the model did not return anything shaped
    # like a recipe" three functions later.
    #
    # A thinking model (qwen3, deepseek-r1) puts its reasoning in a separate
    # field and, when it runs out of budget mid-thought, answers 200 OK with
    # content of "". Measured on this machine, qwen3:8b asked for a JSON
    # object returns '' at both 200 and 900 max_tokens.
    #
    # Some routers surface the reasoning under `reasoning_content`. If the
    # answer is in there, use it -- the JSON salvage in chat_json can usually
    # pull an object out of a chain of thought, and half an answer beats none.
    for spare in ("reasoning_content", "reasoning", "thinking"):
        text = message.get(spare)
        if isinstance(text, str) and text.strip():
            return text

    raise LLMError(
        f"The model answering `{model}` returned an empty reply. That usually "
        "means it is a THINKING model that spent its whole token budget "
        "reasoning. Point this job at a plain model in litellm/config.yaml, "
        "or raise max_tokens a long way."
    )


async def chat_json(system: str, user: str, *, model: str, **kw):
    """chat(), but you get a dict. Raises LLMError rather than returning junk."""
    text = await chat(system, user, model=model, want_json=True, **kw)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return _salvage(text)
    except (json.JSONDecodeError, LLMError):
        pass

    # One repair pass. Give it back its own output and ask for only the object.
    fixed = await chat(
        "You output JSON and nothing else. No prose, no code fences.",
        "Return ONLY the JSON object from the following, corrected so that it "
        "parses. Change no values.\n\n" + text[:6000],
        model=model, want_json=True, temperature=0.0,
    )
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        return _salvage(fixed)


async def describe_image(image_bytes: bytes, question: str, *,
                         model: str = "", max_tokens: int = 300) -> str:
    """Ask a real vision model about a picture. Raises LLMError, same
    contract as chat() -- callers already know how to show that.

    CONFIRMED LIVE, THE EXACT SHAPE THAT WORKS: an OpenAI-style multimodal
    `content` list (`image_url` with a data: URI), not the plain-content-
    plus-separate-`images`-field shape Ollama's OWN native /api/chat
    endpoint takes -- LiteLLM's /chat/completions only understands the
    former. Tested against this exact model and endpoint before this
    function existed, not assumed from documentation.
    """
    model = model or core.LLM_VISION
    b64 = base64.b64encode(image_bytes).decode()
    body = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": question},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
        "max_tokens": max_tokens,
    }
    headers = {"Content-Type": "application/json"}
    if core.LLM_KEY:
        headers["Authorization"] = f"Bearer {core.LLM_KEY}"
    async with httpx.AsyncClient(timeout=core.LLM_TIMEOUT) as client:
        try:
            r = await client.post(f"{core.LLM_BASE}/chat/completions",
                                  json=body, headers=headers)
        except httpx.RequestError as e:
            raise LLMError(
                "I could not reach the model router. Is the `litellm` container "
                f"running? ({e.__class__.__name__})"
            )
    if r.status_code >= 400:
        raise LLMError(f"The vision model said {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        content = data["choices"][0]["message"].get("content") or ""
    except (KeyError, IndexError):
        raise LLMError("The vision model returned a reply with no message in it.")
    if not content.strip():
        raise LLMError("The vision model returned an empty reply.")
    return content.strip()


async def health() -> tuple[bool, str]:
    """Used by the status line on the home page. Never raises."""
    headers = {"Authorization": f"Bearer {core.LLM_KEY}"} if core.LLM_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"{core.LLM_BASE}/models", headers=headers)
        if r.status_code >= 400:
            return False, f"router said {r.status_code}"
        names = [m.get("id", "") for m in r.json().get("data", [])]
        missing = [j for j in (core.LLM_READ, core.LLM_TALK) if j not in names]
        if missing:
            return False, "no such job: " + ", ".join(missing)
        return True, f"{core.LLM_READ} + {core.LLM_TALK}"
    except Exception as e:
        return False, e.__class__.__name__
