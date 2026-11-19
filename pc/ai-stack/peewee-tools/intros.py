#!/usr/bin/env python3
# =============================================================================
#  intros.py -- the host's fifty ways to bring somebody on (2026-09-01).
#
#  COMPOSED, not baked. Fifty generic openers are rendered ONCE, and each
#  person gets one clip of their own name. The site plays an opener and
#  then the name, so fifty openers across two hundred people costs fifty
#  renders plus two hundred -- not ten thousand. Add a person, render one
#  name clip, and they inherit every introduction the host knows.
#
#  Each opener is written to LAND on a name: the normalizer gives it a
#  full stop, the tail silence gives it a beat, and the name drops into
#  that beat the way a jock does it.
#
#  Writes data/intros/opener-NN.mp3 + data/intros.json, and
#  people/<slug>/name.mp3 for everybody on the dial.
#  NARRATE_VOICE / VOICE_TAG work here exactly as in the other tools.
# =============================================================================
import json
import os
import urllib.request
from pathlib import Path


def _http_only(target):
    """Return target (a URL string or urllib Request) if its scheme is http or https, else raise ValueError.

    urlopen also follows file:// and custom schemes (Bandit B310). Every caller here talks to an
    http(s) service, so anything else is a mistake or a planted URL. Added 2026-11-19.
    """
    url = str(getattr(target, "full_url", target))
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url[:80]}")
    return target


VOICE_URL = (os.environ.get("VOICE_NARRATE_URL")
             or os.environ.get("VOICE_URL",
                               "http://localhost:8095/v1/audio/speech"))
VOICE = os.environ.get("NARRATE_VOICE", "Randy")
VOICE_TAG = os.environ.get("VOICE_TAG", "")
SITE = os.environ.get("PEEWEE_SITE") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "web", "public", "peewee"))

OPENERS = [
    "Ladies and gentlemen, the one and only",
    "You know the voice. You know the face. Here they are",
    "This one needs no introduction, but they are getting one anyway",
    "Straight off the top of the charts",
    "Now here is a name that carried a whole generation",
    "Sit down for this one",
    "Turn it up, because this is",
    "The next voice you hear belongs to",
    "Somebody who changed what was possible",
    "A giant. There is no other word for it",
    "This is the real thing",
    "Coming to you now, an American original",
    "Every so often somebody arrives who cannot be copied",
    "Here comes a legend, and I do not use that word lightly",
    "The dial lands on greatness",
    "Now, somebody this city took to heart",
    "You have heard the records. Here is the person",
    "Stand by for a heavyweight",
    "This next one earned every bit of it",
    "A voice that outlived its own era",
    "History is about to walk in",
    "The board lights up for this one",
    "Somebody Pee-Wee was proud to have on his wall",
    "Not a copy of anybody. The original",
    "Here is a career that reads like a dare",
    "This one is pure class",
    "Now hear this",
    "The next name on the dial is",
    "Somebody who made the whole country listen",
    "You are in good company now",
    "A talent so big it changed the room",
    "Here is somebody who never needed a second take",
    "Let me put this one on for you",
    "This next one is a favourite around here",
    "Some people define a decade. Here is one",
    "Now, an artist who did it their own way",
    "Somebody worth every minute of your time",
    "Here is a name they will still be saying in a hundred years",
    "The kind of talent that only shows up once",
    "Something special coming up",
    "Now a voice that made the whole world lean in",
    "You know exactly who this is",
    "This is somebody who opened doors",
    "Here is a life worth hearing about",
    "The next station on the dial belongs to",
    "Somebody who never once played it safe",
    "A star, in the oldest sense of the word",
    "Now, a name Pee-Wee played more than most",
    "Here comes the genuine article",
    "And now, somebody I am glad to bring you",
    # --- the second tier (2026-09-02): written with the delivery marks
    # the normalizer acts on -- a double dash is a real beat, a comma is a
    # shorter one -- so the host lands these instead of reading them. Same
    # rule as the first fifty: each one leads INTO a name and states
    # nothing about the person it is introducing.
    "Now this -- this is somebody Pee-Wee would have turned all the way up for",
    "Listen close, because you do not get many like",
    "Some names you announce. This one, you just get out of the way of",
    "I have been holding this one back all night. Here is",
    "Sit up. Here comes",
    "You have been waiting, whether you knew it or not, for",
    "Let me say this name properly, because it deserves it",
    "There is loud, there is famous -- and then there is",
    "Hold everything. On this frequency, right now",
    "If you only stay for one more, make it this one",
    "The board lights up for this one. Here is",
    "Some people arrive. Others land. This one lands",
    "I could talk all night and not do this one justice, so here is",
    "Pee-Wee kept this record close. Here comes",
    "Ask anybody who was there -- they will tell you about",
    "This is the part of the night I look forward to",
    "Lean in for this one",
    "Every dial in the Capital District stopped here",
    "And now -- take your time with this one",
    "I want you to hear every second of",
    "Nobody else sounds like this. Nobody",
    "Here is a name that still moves a room",
    "You will want to remember where you were for this one",
    "Clear the floor. Coming to you now",
    "Out of everybody on this dial, few carried it like",
]


def say(text, out):
    body = json.dumps({"input": text, "voice": VOICE,
                       "response_format": "mp3", "normalize": True,
                       "exaggeration": 0.55, "cfg_weight": 0.5}).encode()
    req = urllib.request.Request(VOICE_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(_http_only(req), timeout=600) as r:  # nosec B310
        data = r.read()
    if len(data) < 2000:
        raise RuntimeError("suspiciously small audio")
    with open(out, "wb") as f:
        f.write(data)


def main():
    os.chdir(SITE)
    os.makedirs(os.path.join("data", "intros"), exist_ok=True)
    done = kept = 0

    openers = []
    for i, line in enumerate(OPENERS, 1):
        out = os.path.join("data", "intros", f"opener-{i:02d}{VOICE_TAG}.mp3")
        base = f"data/intros/opener-{i:02d}.mp3"
        if os.path.exists(out):
            kept += 1
        else:
            print(f"opener {i:02d}: {line}", flush=True)
            try:
                say(line, out)
                done += 1
            except Exception as e:  # noqa: BLE001
                print("  failed:", e, flush=True)
                continue
        openers.append(base)

    names = {}
    for slug in sorted(os.listdir("people")):
        d = os.path.join("people", slug)
        pj = os.path.join(d, "person.json")
        if not os.path.isfile(pj) or slug == "peewee-harris":
            continue
        person = json.loads(Path(pj).read_text(encoding="utf-8"))
        name = person.get("name") or slug.replace("-", " ").title()
        out = os.path.join(d, f"name{VOICE_TAG}.mp3")
        if os.path.exists(out):
            kept += 1
        else:
            print(f"name: {name}", flush=True)
            try:
                say(name + ".", out)
                done += 1
            except Exception as e:  # noqa: BLE001
                print("  failed:", e, flush=True)
                continue
        names[slug] = f"people/{slug}/name.mp3"

    with open(os.path.join("data", "intros.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump({"_note": "openers are generic and rendered once; each "
                            "person owns one clip of their name. The site "
                            "plays an opener, then the name -- so every "
                            "person inherits every introduction.",
                   "openers": openers, "names": names}, f, indent=1)
    print(f"rendered {done}, kept {kept}; "
          f"{len(openers)} openers, {len(names)} names")


if __name__ == "__main__":
    main()
