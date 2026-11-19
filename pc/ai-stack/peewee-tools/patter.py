#!/usr/bin/env python3
# =============================================================================
#  patter.py -- the host's record talk (2026-09-01, Jason's spec).
#
#  Three kinds of clips, all in Randy's voice:
#    data/patter/song-<file>.mp3     "Here's {title} -- {artist}, {year}."
#                                    announced the first time a record plays
#    data/patter/interrupt-N.mp3     the host stopping himself mid-flow to
#                                    let the record breathe
#    data/patter/resume-N.mp3        "Okay, where was I." -- picking the
#                                    thread back up
#
#  Titles/artists/years come from data/songfacts.json -- documented facts
#  only; a record with no entry gets no announcement rather than a guess.
#  Idempotent: existing clips kept. data/patter.json is the manifest.
# =============================================================================
import json
import os
import random
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
VOICE_TAG = os.environ.get("VOICE_TAG", "")   # e.g. "-jason"
SITE = os.environ.get("PEEWEE_SITE") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "web", "public", "peewee"))

# ---------------------------------------------------------------------
#  THE HOST'S VOCABULARY. Written to be spoken, not read: a double dash
#  is a beat, a comma is a shorter one, an exclamation is lift. Pools are
#  deep on purpose -- a record every few minutes for an hour means a thin
#  pool repeats itself inside one sitting, and that is what makes a
#  station sound canned.
# ---------------------------------------------------------------------
UPNOW_LINES = [
    "You're listening to {title}. {artist}, {year}.",
    "That's {title} -- {artist}, back in {year}.",
    "{title}, if you're wondering. {artist}.",
    "This is {artist} -- {title}, {year}.",
    "Still with you: {title}, by {artist}.",
    "That sound is {title}. {year}. {artist}.",
    "Somebody's going to ask -- that's {title}, by {artist}.",
    "You're in good hands. {artist}, {title}.",
    "{artist} on the turntable. {title}, {year}.",
    "Let it ride. {title} -- {artist}.",
]
UPNEXT_LINES = [
    "After this one, we're playing {title}, by {artist}.",
    "Coming up after this: {title}. {artist}.",
    "And after this record: {title}, {artist}.",
    "Stay where you are -- {title} is next. {artist}.",
    "When this one lets go, {artist} takes it. {title}.",
    "Next on the dial: {title}, by {artist}.",
    "I've got {artist} warming up. {title}, right after this.",
    "Don't touch that -- {title} is coming. {artist}, {year}.",
    "Hold on for this next one. {title}, by {artist}!",
    "Right behind it: {artist}, with {title}.",
    "The one after this is {title}. You'll know it.",
    "Then it's {artist}. {title}. Stay with me.",
    "Cue it up -- {title}, {artist}, next.",
    "And when that finishes? {title}. {artist}.",
    "I'm putting {artist} on next. {title}, from {year}.",
]
FACT_LINES = [
    "{title}? {fact}.",
    "Little something about {title} -- {fact}.",
    "Here's one for you. {title}: {fact}.",
    "You may not know this about {title}. {fact}.",
    "Worth saying about {title} -- {fact}.",
    "{fact}. That's {title}.",
]

SONG_LINES = [
    "Here's {title}. {artist}, {year}.",
    "This one's {title} -- {artist}, {year}.",
    "You're hearing {title}, by {artist}. {year}.",
    "Let's get into it. {title}, {artist}, {year}!",
    "Turn this one up. {title} -- {artist}.",
    "{year}. {artist}. {title}. Go ahead.",
    "Been waiting to play this. {title}, by {artist}.",
    "Here comes {artist} -- {title}, {year}.",
    "This is the one. {title}. {artist}, {year}.",
    "Roll it. {title}, by {artist}, out of {year}.",
]
# The host stepping OUT of a telling, because a record needs him. With
# tellings now cut at their paragraphs, these land on a real seam rather
# than cutting a sentence in half.
INTERRUPTS = [
    "Hold that thought. Let this record breathe a second.",
    "You know what, let the music talk first.",
    "Stay right there -- I've got to see to the music.",
    "One second. The record wants me.",
    "Let me take care of the turntable, and I'll be right back.",
    "Hang on to that. Music first.",
    "Pardon me a moment -- there's a record to name.",
    "Let me get this next one lined up. Don't go anywhere.",
]
RESUMES = [
    "Okay. Where was I.",
    "Now, where was I. Right.",
    "Alright. Back to it.",
    "So. Back to the story.",
    "Right -- where we left off.",
    "Now then. Picking that back up.",
    "Thank you for waiting. Here we go.",
    "Where were we. Yes.",
    "Back to it, then.",
]



# ---------------------------------------------------------------------
#  CHART LONGEVITY, FOR SOMEBODY WHO HAS ONLY EVER STREAMED (2026-09-02).
#
#  A run on the charts in 1965 does not mean what a run means now, and
#  that is worth saying out loud. The rule this obeys: the CLAIM is the
#  sourced fact from songfacts.json, quoted as written -- nothing here
#  asserts a chart position or a length of run of its own. The second
#  half is general history about the era, not a statement about this
#  record, so no fact is invented to make the point.
# ---------------------------------------------------------------------
CHART_WORDS = ("number one", "chart", "hot 100", "top ", "weeks",
               "billboard", "gold", "million")
ERA_FRAMES = [
    ("there was no streaming then. Every one of those weeks was somebody"
    " walking into a shop and paying for it."),
    "and not one algorithm helped it along. That was people, choosing.",
    ("a record held its place back then because listeners kept asking for"
    " it, week after week, by name."),
    ("you could not skip a song in nineteen sixty-five. You bought it, and"
    " you lived with it."),
    "no playlist put it there. It got there the long way.",
    "that was a whole season of somebody's life on the radio.",
    ("a run like that today would be measured in a weekend. Back then it"
    " was measured in months."),
    ("every one of those weeks meant a shop clerk reordering it because"
    " the box was empty again."),
]
ERA_LINES = [
    "{fact}. Think about that for a second -- {frame}",
    "{fact}. And remember, {frame}",
    "Here is what that meant. {fact}. But {frame}",
    "{fact}. Put that in perspective: {frame}",
]


def chart_fact(meta):
    """The first sourced fact that actually talks about the charts.

    Returns None when a record has no charting fact on file -- and then
    no era line is rendered for it, rather than one being improvised."""
    for f in meta.get("facts") or []:
        if any(w in f.lower() for w in CHART_WORDS):
            return f
    return None


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
    os.makedirs(os.path.join("data", "patter"), exist_ok=True)
    facts = json.loads(Path(os.path.join("data", "songfacts.json")).read_text(encoding="utf-8"))
    manifest = {"songs": {}, "interrupts": [], "resumes": []}
    done = kept = 0

    for fname, meta in facts.items():
        if fname.startswith("_") or not isinstance(meta, dict):
            continue
        out = os.path.join("data", "patter",
                           f"song-{fname[:-4]}{VOICE_TAG}.mp3")
        rng = random.Random(fname)
        line = rng.choice(SONG_LINES).format(
            title=meta["title"], artist=meta["artist"], year=meta["year"])
        if os.path.exists(out):
            kept += 1
        else:
            print("rendering", out, "--", line, flush=True)
            try:
                say(line, out)
                done += 1
            except Exception as e:  # noqa: BLE001
                print("  failed:", e, flush=True)
                continue
        manifest["songs"][fname] = "data/patter/" + os.path.basename(out)

        # the queue clips (2026-09-01): "you're listening to", "after this
        # one", and one chart detail -- composable so the host can announce
        # any two-record sequence the shuffle deals him
        extra = {
            "upnow": rng.choice(UPNOW_LINES).format(**meta),
            "upnext": rng.choice(UPNEXT_LINES).format(**meta),
        }
        if meta.get("facts"):
            extra["fact"] = rng.choice(FACT_LINES).format(title=meta["title"],
                                             fact=meta["facts"][0])
        cf = chart_fact(meta)
        if cf:
            extra["era"] = rng.choice(ERA_LINES).format(
                fact=cf.rstrip("."), frame=rng.choice(ERA_FRAMES))
        for kind, text in extra.items():
            kout = os.path.join("data", "patter",
                                f"{kind}-{fname[:-4]}{VOICE_TAG}.mp3")
            if os.path.exists(kout):
                kept += 1
            else:
                print("rendering", kout, "--", text[:70], flush=True)
                try:
                    say(text, kout)
                    done += 1
                except Exception as e:  # noqa: BLE001
                    print("  failed:", e, flush=True)
                    continue
            manifest.setdefault(kind, {})[fname] =                 "data/patter/" + os.path.basename(kout)

    for kind, lines in (("interrupt", INTERRUPTS), ("resume", RESUMES)):
        for i, line in enumerate(lines, 1):
            out = os.path.join("data", "patter", f"{kind}-{i}{VOICE_TAG}.mp3")
            if os.path.exists(out):
                kept += 1
            else:
                print("rendering", out, "--", line, flush=True)
                try:
                    say(line, out)
                    done += 1
                except Exception as e:  # noqa: BLE001
                    print("  failed:", e, flush=True)
                    continue
            manifest[kind + "s"].append("data/patter/" + os.path.basename(out))

    with open(os.path.join("data", f"patter{VOICE_TAG}.json"), "w",
                 encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1)
    print(f"rendered {done}, kept {kept}; manifest written")


if __name__ == "__main__":
    main()
