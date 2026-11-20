"""Gemini, given the WHOLE recipe instead of one step at a time.

WHAT IS ACTUALLY BEING TESTED (Jason, 2026-09-04). Kitchen's diagram.py
reads one step in isolation, so it cannot know that a pan is already on
the heat from step two, or that the onion was chopped in step one. That
is a context problem, not a drawing problem, and it is the reason to try
a model with room for the entire recipe rather than a better renderer.

TWO HALVES, AND ONLY ONE OF THEM WORKS TODAY.

  TEXT works on the free tier right now. This is the half that carries
  most of the value anyway: knowing what is already in the pan, what
  this step is waiting for, and what to have ready before starting it.

  IMAGES do not. Every image model -- 3.1-flash-image, 2.5-flash-image,
  3-pro-image -- returns 429 with quota ids ending FreeTier. Pictures
  need billing enabled on the Google Cloud project. Checked, not
  assumed, on 2026-09-04.

So the image call is written and will start working the day billing is
on, and until then it says exactly that instead of failing obscurely.

EVERYTHING IS CACHED BY CONTENT. A recipe's guidance does not change
between two people looking at it, and this is metered. The cache key is
the recipe and the model, so a re-render after an edit is a new answer
and an unchanged recipe is free forever.
"""
import hashlib
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from . import config

API = "https://generativelanguage.googleapis.com/v1beta/models"
CACHE = Path(os.getenv("RECIPEAPP_GEMINI_CACHE", "/tmp/gemini-cache"))

SYSTEM = (
    "You are helping somebody cook a specific recipe, and you can see ALL of "
    "it -- every ingredient and every step -- which the person reading step "
    "four cannot hold in their head.\n"
    "For the step you are asked about, return STRICT JSON with these keys:\n"
    '  "ready"   what should already be done or already in the pan before '
    "this step starts, from EARLIER steps only. Empty string if nothing.\n"
    '  "watch"   the one thing most likely to go wrong here, in a short '
    "clause. Empty string if there is nothing worth saying.\n"
    '  "looks"   what it should look, sound or smell like when this step is '
    "finished. Empty string if the recipe gives no signal.\n"
    "USE ONLY WHAT THE RECIPE SAYS. Never invent a time, a temperature or an "
    "ingredient that is not written down. An empty string is a correct "
    "answer and a helpful one; an invented detail ruins somebody's dinner. "
    "No preamble, no markdown, JSON only."
)


def available() -> tuple[bool, str]:
    if not config.GEMINI_KEY:
        return False, "GEMINI_API_KEY is empty."
    return True, "ready"


def _cache_path(kind: str, key: str) -> Path:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return CACHE / f"{kind}-{h}"


def _post(model: str, body: dict, timeout: int = 120) -> dict:
    url = f"{API}/{model}:generateContent?key={config.GEMINI_KEY}"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def _recipe_context(recipe: dict) -> str:
    """The whole thing, as the model should see it. This is the point."""
    ings = recipe.get("ingredients") or []
    lines = []
    for i in ings:
        lines.append(i if isinstance(i, str) else str(i.get("text") or i))
    steps = recipe.get("steps") or []
    stext = []
    for n, s in enumerate(steps, 1):
        stext.append(f"{n}. " + (s if isinstance(s, str) else (s.get("text") or "")))
    return (f"TITLE: {recipe.get('title', '')}\n\n"
            "INGREDIENTS:\n" + "\n".join(f"- {x}" for x in lines) +
            "\n\nSTEPS:\n" + "\n".join(stext))


def step_notes(recipe: dict, step_no: int) -> dict:
    """{ready, watch, looks} for one step, with the whole recipe in view.

    Returns {} on any failure -- this is an enhancement over a page that
    already works, and it must never be the reason a recipe will not open.
    """
    ok, _ = available()
    if not ok:
        return {}
    ctx = _recipe_context(recipe)
    key = f"{config.GEMINI_MODEL}|{step_no}|{ctx}"
    cached = _cache_path("notes", key)
    if cached.exists():
        try:
            return json.loads(cached.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM}]},
        "contents": [{"parts": [{"text": ctx + f"\n\nThe step in question is "
                                              f"number {step_no}."}]}],
        "generationConfig": {"temperature": 0.2, "responseMimeType":
                             "application/json"},
    }
    try:
        d = _post(config.GEMINI_MODEL, body)
        txt = d["candidates"][0]["content"]["parts"][0]["text"]
        out = json.loads(txt)
        out = {k: str(out.get(k, "") or "").strip()
               for k in ("ready", "watch", "looks")}
    except Exception:  # noqa: BLE001
        return {}
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        cached.write_text(json.dumps(out), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    return out


def step_image(recipe: dict, step_no: int) -> tuple[bytes | None, str]:
    """A picture of this step, drawn knowing the whole recipe.

    Returns (bytes, ""), or (None, reason). The reason is shown to the
    person rather than swallowed, because the most likely one -- billing
    is not enabled -- is fixable and invisible otherwise.
    """
    ok, why = available()
    if not ok:
        return None, why
    ctx = _recipe_context(recipe)
    steps = recipe.get("steps") or []
    this = ""
    if 0 < step_no <= len(steps):
        s = steps[step_no - 1]
        this = s if isinstance(s, str) else (s.get("text") or "")
    key = f"{config.GEMINI_IMAGE_MODEL}|{step_no}|{ctx}"
    cached = _cache_path("img", key)
    if cached.exists() and cached.stat().st_size > 800:
        return cached.read_bytes(), ""
    prompt = (
        "A clean, appetising illustration of ONE step in a recipe, for a "
        "cooking app. Flat, warm, uncluttered, no text and no hands. "
        "Draw only what this step involves, in the state the earlier steps "
        "have left things.\n\n"
        f"{ctx}\n\nDraw step {step_no}: {this}")
    try:
        d = _post(config.GEMINI_IMAGE_MODEL,
                  {"contents": [{"parts": [{"text": prompt}]}]}, timeout=180)
        parts = d["candidates"][0]["content"]["parts"]
        for p in parts:
            blob = p.get("inlineData") or p.get("inline_data")
            if blob and blob.get("data"):
                import base64
                raw = base64.b64decode(blob["data"])
                try:
                    CACHE.mkdir(parents=True, exist_ok=True)
                    cached.write_bytes(raw)
                except Exception:  # noqa: BLE001
                    pass
                return raw, ""
        return None, "Gemini returned no image for that step."
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            pass
        if e.code == 429 or "quota" in detail.lower():
            return None, ("Image generation is not on the free tier -- every "
                          "image model returns a FreeTier quota error. Enable "
                          "billing on the Google Cloud project (a budget cap "
                          "is fine) and this starts working with no code "
                          "change.")
        return None, f"Gemini refused: {e.code}"
    except Exception as e:  # noqa: BLE001
        return None, f"Gemini did not answer: {type(e).__name__}"
