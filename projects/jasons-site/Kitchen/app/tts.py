"""Reading a recipe step aloud, in a voice you actually chose.

WHAT THIS IS FOR
----------------
Same argument as art.py's pictures, for ears instead of eyes: hands covered in
flour, eyes on the pan, phone propped against the toaster.

ONE VOICE SERVICE, NOT TWO. This used to call `voicebox` (a separate "studio"
app, port 17493) directly. Jason's own choice (2026-08-25), after being asked
about it directly: unify on `voice` (LuxTTS, port 8091) everywhere instead --
the SAME engine already serving the phone line, Telegram, Open WebUI and
Hermes, off one copy of the weights. Recording one sample now gives one voice
across the whole household stack, not a second clone living only here.
Cloning a voice there is genuinely just dropping a clean 10-30s clip at
ai-stack/voices/<name>.<ext> (wav/mp3/flac/ogg/m4a all accepted, auto
-resampled) -- nothing in this file does any of that; it only calls the
finished voice by name.

NO VOICE NAME, NO SPEECH. There is no way to guess whose voice is "the right
one" the way art.py can guess what a browned onion looks like, so a missing
KITCHEN_VOICE_NAME is a plain, quiet "not set up yet", never a guess at some
default voice nobody chose.

CACHED, NOT RE-SPOKEN ON EVERY CLICK. Jason's own request (2026-08-25):
"save the recipes to be able to speak aloud... something small like an mp3."
Before this, listen_step() (main.py) called speak() live on every single
press of Listen -- the GPU re-read the exact same sentence, in the exact
same voice, every time. cached_speak() below saves the mp3 to disk keyed by
(voice name, spoken text) the first time, and every later request for that
same step just serves the file back. The key naturally changes -- and so
regenerates, rather than serving something stale -- the moment either the
step's own wording or the configured voice changes; nothing has to remember
to invalidate it by hand.
"""
import hashlib
import re
from pathlib import Path

import httpx

from . import core

BASE = core.os.getenv("KITCHEN_VOICE_URL", "http://voice:8091").rstrip("/")
VOICE_NAME = core.os.getenv("KITCHEN_VOICE_NAME", "").strip()
TIMEOUT = float(core.os.getenv("KITCHEN_VOICE_TIMEOUT", "60"))

AUDIO_DIR = core.DATA_DIR / "audio"
AUDIO_DIR.mkdir(parents=True, exist_ok=True)

# What voice/server.py's /v1/audio/speech can hand back, both ways -- so a
# cached file's own extension is enough to know its content type again
# without a second, easy-to-drift record of it anywhere.
_EXT_FOR_TYPE = {"audio/mpeg": "mp3", "audio/wav": "wav",
                 "audio/flac": "flac", "audio/ogg": "ogg"}
_TYPE_FOR_EXT = {v: k for k, v in _EXT_FOR_TYPE.items()}

# Markdown and HTML a step's text can carry -- art.py's own step
# illustrations embed `![...](...)` plus a caption into a step's `summary`,
# and an imported page's photo credit can too. None of it is words in the
# recipe; none of it should be read aloud as if it were.
_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_EMPHASIS = re.compile(r"[*_`#]+")
_HTML_TAG = re.compile(r"<[^>]+>")


class TTSError(Exception):
    pass


def enabled() -> bool:
    return bool(VOICE_NAME)


# --- written for the eye, said for the ear ---------------------------------
# A recipe is dense with things that read fine and SPEAK badly, and this was
# established by measurement rather than worry: the step
#
#   "Add 1/2 cup flour and 1 tsp salt, bake at 350F for 25-30 min."
#
# was synthesised and then transcribed back by the household speech-to-text
# service, and came out as
#
#   "Add 1 1 half cup flour and 1 TSP salt bake at 350F for 2530 men."
#
# "Bake at 350 for 2530 minutes" is not a cosmetic problem. Somebody cooking
# from audio, with their hands in a bowl, has no reason to doubt it.
#
# ORDER MATTERS BELOW. Mixed numbers ("1 1/2") must be handled before bare
# fractions, or the 1 and the 1/2 are read as two separate quantities -- which
# is exactly the "1 1 half" above. Ranges must be handled before units, or the
# hyphen is gone by the time anything looks for it.
# "half a cup", not "half cup" -- the trailing "a" is what makes it sound like
# a person rather than a label. The plural the unit table then produces
# ("half a cups") is cleaned up at the end.
_FRACTIONS = {
    "1/2": "half a", "1/3": "a third of a", "2/3": "two thirds of a",
    "1/4": "a quarter of a", "3/4": "three quarters of a",
    "1/8": "an eighth of a", "3/8": "three eighths of a",
    "5/8": "five eighths of a", "7/8": "seven eighths of a",
    "1/6": "a sixth of a", "1/16": "a sixteenth of a",
}
# In a mixed number the fraction is spoken bare: "one and a half tablespoons",
# never "one and half a tablespoons".
_MIXED_FRACTIONS = {
    "1/2": "a half", "1/3": "a third", "2/3": "two thirds",
    "1/4": "a quarter", "3/4": "three quarters", "1/8": "an eighth",
    "3/8": "three eighths", "5/8": "five eighths", "7/8": "seven eighths",
    "1/6": "a sixth", "1/16": "a sixteenth",
}
_UNITS = [
    (r"\btsps?\b", "teaspoons"), (r"\btbsps?\b", "tablespoons"),
    (r"\btbs\b", "tablespoons"), (r"\boz\b", "ounces"), (r"\blbs?\b", "pounds"),
    (r"\bmins?\b", "minutes"), (r"\bhrs?\b", "hours"), (r"\bsecs?\b", "seconds"),
    (r"\bqts?\b", "quarts"), (r"\bpts?\b", "pints"), (r"\bgals?\b", "gallons"),
    (r"\bml\b", "millilitres"), (r"\bcl\b", "centilitres"),
    (r"\bg\b", "grams"), (r"\bkg\b", "kilograms"), (r"\bmg\b", "milligrams"),
    (r"\bpkg\b", "package"), (r"\bdoz\b", "dozen"),
]


def _say_numbers(t: str) -> str:
    # Temperatures first: "350F" and "180C" carry no space to anchor on later.
    t = re.sub(r"(\d+)\s*°?\s*F\b", r"\1 degrees Fahrenheit", t)
    t = re.sub(r"(\d+)\s*°?\s*C\b", r"\1 degrees Celsius", t)
    t = re.sub(r"(\d+)\s*°", r"\1 degrees", t)

    # "1 1/2" -> "one and a half". Before bare fractions, deliberately.
    def mixed(m):
        whole, frac = m.group(1), m.group(2)
        word = _MIXED_FRACTIONS.get(frac)
        return f"{whole} and {word}" if word else f"{whole} {frac}"
    t = re.sub(r"\b(\d+)\s+(\d+/\d+)", mixed, t)

    # "21g", "250ml" -- a number run straight into its unit, which is how
    # recipes are actually written and which \b can never match. Split them so
    # the unit table below can see the unit at all.
    t = re.sub(r"(\d)\s*(kg|mg|ml|cl|oz|lbs?|qts?|pts?|g)\b", r"\1 \2", t)

    for frac, word in _FRACTIONS.items():
        t = re.sub(rf"(?<![\d/]){re.escape(frac)}(?![\d/])", word, t)

    # "25-30" -> "25 to 30". A hyphen between numbers is a range every time in
    # a recipe, and it is what produced "2530".
    t = re.sub(r"(\d)\s*[-–]\s*(\d)", r"\1 to \2", t)

    for pat, word in _UNITS:
        t = re.sub(pat, word, t, flags=re.I)

    # "half a cups", "three quarters of a pounds" -- the fraction wanted a
    # singular and the unit table always yields a plural. Cheaper to correct
    # here than to make that table aware of what precedes it.
    t = re.sub(r"\b(of a|half a) (\w+?)s\b", r"\1 \2", t)

    # "1 teaspoons" reads as a mistake even when the quantity is right.
    t = re.sub(r"\b1 (teaspoons|tablespoons|ounces|pounds|minutes|hours|"
               r"seconds|quarts|pints|gallons|grams|kilograms|milligrams|"
               r"cups)\b", lambda m: "1 " + m.group(1)[:-1], t)
    return t


def speech_text(step_text: str) -> str:
    """The step, stripped of anything meant for a screen and not an ear."""
    t = _MD_IMAGE.sub("", step_text or "")
    t = _MD_LINK.sub(r"\1", t)
    t = _HTML_TAG.sub("", t)
    t = _MD_EMPHASIS.sub("", t)
    t = _say_numbers(t)
    return re.sub(r"\s+", " ", t).strip()


async def speak(text: str) -> tuple[bytes, str]:
    """(audio bytes, content-type) for `text`, in the configured voice.
    Always hits the network -- see cached_speak() for the version a route
    should actually call. Raises TTSError with a message fit to show a
    person -- never returns silently broken or empty audio.
    """
    if not VOICE_NAME:
        raise TTSError("No voice is set up yet.")
    t = speech_text(text)
    if not t:
        raise TTSError("Nothing to read in that step.")

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(f"{BASE}/v1/audio/speech",
                                  json={"input": t, "voice": VOICE_NAME,
                                        "response_format": "mp3"})
            if r.status_code == 400:
                raise TTSError(
                    f"The voice '{VOICE_NAME}' has no sample yet -- drop a "
                    f"clean 10-30s clip at ai-stack/voices/{VOICE_NAME}.<ext> "
                    "first (wav, mp3, flac, ogg or m4a).")
            r.raise_for_status()
            if not r.content:
                raise TTSError("The voice service answered with no audio.")
            return r.content, r.headers.get("content-type", "audio/mpeg")
    except TTSError:
        raise
    except Exception as e:  # noqa: BLE001
        raise TTSError(f"Could not reach the voice service ({e.__class__.__name__}).") from e


def _cache_key(voice_text: str) -> str:
    # VOICE_NAME is folded into the key itself (not just the file's path),
    # so switching voices invalidates every cached step everywhere in one
    # move rather than needing every draft's audio directory visited.
    return hashlib.sha256(f"{VOICE_NAME}|{voice_text}".encode()).hexdigest()[:16]


async def cached_speak(draft_id: str, slot: str, text: str) -> tuple[bytes, str]:
    """Same contract as speak(), but reads back a saved file when this
    exact (voice, slot, wording) has already been spoken, and saves a new
    one the first time it hasn't. `slot` is the same "step-N" shape
    photos.py already uses for pictures -- same recipe, same per-slot idea,
    just an mp3 instead of a jpg.
    """
    if not VOICE_NAME:
        raise TTSError("No voice is set up yet.")
    t = speech_text(text)
    if not t:
        raise TTSError("Nothing to read in that step.")

    key = _cache_key(t)
    draft_dir = AUDIO_DIR / draft_id
    for existing in draft_dir.glob(f"{slot}-{key}.*"):
        ctype = _TYPE_FOR_EXT.get(existing.suffix.lstrip("."), "audio/mpeg")
        return existing.read_bytes(), ctype

    audio, ctype = await speak(t)
    ext = _EXT_FOR_TYPE.get(ctype, "mp3")
    draft_dir.mkdir(parents=True, exist_ok=True)
    # REPLACE, DON'T STACK -- an old key for this slot (the step's wording
    # changed, or the voice did) is dead weight now, not a second valid
    # cache entry. Same "replace rather than accumulate" rule this app
    # already applies to pushed step illustrations.
    for stale in draft_dir.glob(f"{slot}-*"):
        stale.unlink(missing_ok=True)
    (draft_dir / f"{slot}-{key}.{ext}").write_bytes(audio)
    return audio, ctype
