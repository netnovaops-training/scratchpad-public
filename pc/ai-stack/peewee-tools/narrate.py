#!/usr/bin/env python3
# =============================================================================
#  narrate.py -- every story on the dial, read aloud in Randy's voice.
#
#  FOLDER LAYOUT (since 2026-08-31): each person is people/<slug>/ holding
#  story-N.txt; the narration lands beside it as story-N.mp3, so a person's
#  folder is complete in itself -- photos, words, and voice together, which
#  is what the phone line will read too. Run build.py after this so
#  stations.json notices new audio.
#
#  PRE-RENDERED, not synthesised live: the GPU is shared (the voice model
#  competes with the photo tagger and Hermes), a phone caller cannot wait
#  through a cold model load, and the aunt should hear the SAME telling on
#  the web and on the phone. Re-runs only render what changed (txt newer
#  than mp3).
# =============================================================================
import os
import re, json, urllib.request

# localhost when run on the desktop; voice:8091 when run beside it in compose.
# Expressive by default (Jason's 2026-09-01 routing rule): the
# tribute narrates on the engine built for feeling. VOICE_NARRATE_URL
# wins inside the Studio container; the localhost default is the
# expressive engine's desktop port.
VOICE_URL = (os.environ.get("VOICE_NARRATE_URL")
             or os.environ.get("VOICE_URL",
                               "http://localhost:8095/v1/audio/speech"))
VOICE = os.environ.get("NARRATE_VOICE", "Randy")
VOICE_TAG = os.environ.get("VOICE_TAG", "")   # e.g. "-jason"
SITE = os.environ.get("PEEWEE_SITE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "web", "public", "peewee")


# The Studio's fast engine, for artists whose voice-settings choose it.
FAST_URL = (os.environ.get("VOICE_FAST_URL")
            or {"http://localhost:8095/v1/audio/speech":
                "http://localhost:8091/v1/audio/speech"}.get(VOICE_URL)
            or os.environ.get("VOICE_URL",
                              "http://localhost:8091/v1/audio/speech"))


def load_defaults():
    """data/voice-defaults.json -- the whole tribute's baseline sound.
    Per-artist voice-settings.json wins over these, field by field."""
    try:
        with open(os.path.join("data", "voice-defaults.json"),
                  encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


DEFAULTS = None


def load_settings(d):
    """people/<slug>/voice-settings.json -- the Studio's mixing desk for
    this artist. Tuning set by ear once applies to every batch render."""
    try:
        with open(os.path.join(d, "voice-settings.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def say(text, out, settings=None):
    settings = settings or {}
    payload = {"input": text,
               # a TAGGED render is an alternate announcer: the chosen
               # voice wins over any per-artist setting
               "voice": VOICE if VOICE_TAG else settings.get("voice", VOICE),
               "response_format": "mp3",
               # page-punctuation -> speech-punctuation, server-side
               "normalize": True}
    for k in ("speed", "steps", "exaggeration", "cfg_weight", "gap"):
        if settings.get(k) is not None:
            payload[k] = settings[k]
    url = FAST_URL if settings.get("engine") == "fast" else VOICE_URL
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json"})
    # First render loads the model; on a busy GPU that alone can take a
    # couple of minutes. Every render after is seconds.
    with urllib.request.urlopen(req, timeout=600) as r:
        data = r.read()
    if len(data) < 2000:
        raise RuntimeError("suspiciously small audio (%d bytes)" % len(data))
    with open(out, "wb") as f:
        f.write(data)


# Paragraph = part. The writing already breaks where a thought ends, and
# the narrator already holds a real silence there, so cutting on blank
# lines gives clips that begin and end on a breath rather than mid-phrase.
def paragraphs(text):
    return [b.strip() for b in re.split(r"\n\s*\n", text.strip()) if b.strip()]


def render_parts(d, fn, src, settings, watched):
    """story-3.txt -> story-3-p1.mp3, -p2.mp3, ... beside the whole file.

    Returns (rendered, skipped). A single-paragraph telling gets no parts:
    the whole-file render already IS that one part."""
    body = open(src, encoding="utf-8").read()
    blocks = paragraphs(body)
    if len(blocks) < 2:
        return 0, 0
    newest = max(os.path.getmtime(x) for x in watched)
    stem = fn[:-4]                      # story-3
    made = kept = 0
    for i, block in enumerate(blocks, 1):
        out = os.path.join(d, "%s-p%d%s.mp3" % (stem, i, VOICE_TAG))
        if os.path.exists(out) and os.path.getmtime(out) > newest:
            kept += 1
            continue
        print("  part %d/%d -> %s" % (i, len(blocks), out), flush=True)
        try:
            say(block, out, settings)
            made += 1
        except Exception as e:
            print("    failed:", e, flush=True)
    return made, kept


def main():
    os.chdir(SITE)
    total = done = skipped = 0
    pmade = pkept = 0
    for slug in sorted(os.listdir("people")):
        d = os.path.join("people", slug)
        if not os.path.isdir(d):
            continue
        global DEFAULTS
        if DEFAULTS is None:
            DEFAULTS = load_defaults()
        settings = {**DEFAULTS, **load_settings(d)}
        for fn in sorted(os.listdir(d)):
            if not (fn.startswith(("story-", "journey-"))
                    and fn.endswith(".txt")):
                continue
            # story-N.speak.txt is a SPOKEN override (Studio Voice lab), a
            # phonetic script for the voice -- not a telling of its own.
            if fn.endswith(".speak.txt"):
                continue
            total += 1
            txt = os.path.join(d, fn)
            out = os.path.join(d, fn.replace(".txt", VOICE_TAG + ".mp3"))
            speak = os.path.join(d, fn.replace(".txt", ".speak.txt"))
            src = speak if os.path.exists(speak) else txt
            watched = [txt, src]
            sfile = os.path.join(d, "voice-settings.json")
            if os.path.exists(sfile):
                watched.append(sfile)  # retuned artist -> re-render
            newest = max(os.path.getmtime(x) for x in watched)
            if (os.path.exists(out)
                    and os.path.getmtime(out) > newest):
                skipped += 1
                continue
            print("rendering", out,
                  "(spoken override)" if src != txt else "", flush=True)
            try:
                say(open(src, encoding="utf-8").read(), out, settings)
                done += 1
            except Exception as e:
                print("  failed:", e, flush=True)
    # the same walk again, for parts -- separate pass so a failure to cut
    # one telling never costs the whole-file render of the next
    for slug in sorted(os.listdir("people")):
        d = os.path.join("people", slug)
        if not os.path.isdir(d):
            continue
        settings = {**DEFAULTS, **load_settings(d)}
        for fn in sorted(os.listdir(d)):
            if not (fn.startswith(("story-", "journey-"))
                    and fn.endswith(".txt")) or fn.endswith(".speak.txt"):
                continue
            txt = os.path.join(d, fn)
            speak = os.path.join(d, fn.replace(".txt", ".speak.txt"))
            src = speak if os.path.exists(speak) else txt
            watched = [txt, src]
            sfile = os.path.join(d, "voice-settings.json")
            if os.path.exists(sfile):
                watched.append(sfile)
            a, b = render_parts(d, fn, src, settings, watched)
            pmade += a
            pkept += b
    print("rendered %d, kept %d, of %d" % (done, skipped, total))
    print("parts: rendered %d, kept %d" % (pmade, pkept))


if __name__ == "__main__":
    main()
