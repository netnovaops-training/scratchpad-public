#!/usr/bin/env python3
# =============================================================================
#  handoffs.py -- the host's patter, pre-rendered (2026-09-01, Jason's spec).
#
#  A real DJ never lets a record end into silence: he names what is coming,
#  lets the music breathe, then welcomes it in. Every clip here NAMES the
#  person being introduced, so the clips belong to the ARRIVING artist and
#  work in any order the dial happens to take:
#
#      people/<slug>/handoff-next-N.mp3   "Next up on the radio: X."
#      people/<slug>/handoff-in-N.mp3     "This is X."
#
#  Variety by design -- each person draws their variants from the pools with
#  a slug-seeded shuffle, so renders are reproducible but no two people use
#  the same three lines. The site picks randomly per visit on top of that.
#
#  Idempotent: existing clips are kept. Delete a clip to re-roll it.
# =============================================================================
import json
import os
import random
import sys
import time
import urllib.request

VOICE_URL = (os.environ.get("VOICE_NARRATE_URL")
             or os.environ.get("VOICE_URL",
                               "http://localhost:8095/v1/audio/speech"))
SITE = os.environ.get("PEEWEE_SITE") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "web", "public", "peewee"))
VOICE = os.environ.get("NARRATE_VOICE", "Randy")
VOICE_TAG = os.environ.get("VOICE_TAG", "")   # e.g. "-jason"
PER_PERSON = 3

NEXT_LINES = [
    "Next up on the radio: {name}.",
    "Coming up next, right here on the dial: {name}.",
    "Stay with me now. Up next: {name}.",
    "Don't touch that dial. {name} is coming right up.",
    "And now, we turn the dial toward {name}.",
    "Keep it right here. {name} is next.",
    "You know what's coming. {name}, up next.",
]
# The warm introductions (2026-09-01): the host bringing on somebody he
# holds close -- rendered as their own kind so adding them re-renders
# nothing that already exists.
DEAR_LINES = [
    "And now, I'd like to introduce someone dear to me: {name}.",
    "Now this next one is special to me. {name}.",
]

IN_LINES = [
    "Hey now. This is {name}.",
    "Ladies and gentlemen: {name}.",
    "You're on the air with {name}.",
    "Right here, right now: {name}.",
    "The one and only {name}.",
    "Turn it up. This is {name}.",
    "And here they are: {name}.",
]


def say(text, out):
    body = json.dumps({
        "input": text, "voice": VOICE, "response_format": "mp3",
        "normalize": True,
        # Patter is livelier than a telling: the desk-test's slow weight
        # would drag a one-line handoff, so these use the engine's default
        # delivery with a touch more feeling.
        "exaggeration": 0.55, "cfg_weight": 0.5,
    }).encode()
    req = urllib.request.Request(VOICE_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        data = r.read()
    if len(data) < 2000:
        raise RuntimeError("suspiciously small audio (%d bytes)" % len(data))
    with open(out, "wb") as f:
        f.write(data)


def main():
    os.chdir(SITE)
    done = kept = 0
    for slug in sorted(os.listdir("people")):
        d = os.path.join("people", slug)
        pj = os.path.join(d, "person.json")
        if not os.path.isfile(pj):
            continue
        if slug == "peewee-harris":
            continue        # the host is announced by his resurrection
        person = json.load(open(pj, encoding="utf-8"))
        name = person.get("name") or slug.replace("-", " ").title()
        rng = random.Random(slug)   # reproducible per person, varied across
        picks = {
            "handoff-next": rng.sample(NEXT_LINES, PER_PERSON),
            "handoff-in": rng.sample(IN_LINES, PER_PERSON),
            "handoff-dear": DEAR_LINES,
        }
        for kind, lines in picks.items():
            for i, tmpl in enumerate(lines, 1):
                out = os.path.join(d, f"{kind}-{i}{VOICE_TAG}.mp3")
                if os.path.exists(out):
                    kept += 1
                    continue
                text = tmpl.format(name=name)
                print("rendering", out, "--", text, flush=True)
                t0 = time.time()
                try:
                    say(text, out)
                    done += 1
                    print("  %.0fs" % (time.time() - t0), flush=True)
                except Exception as e:  # noqa: BLE001
                    print("  failed:", e, flush=True)
    print(f"rendered {done}, kept {kept}")


if __name__ == "__main__":
    main()
