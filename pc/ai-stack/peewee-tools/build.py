#!/usr/bin/env python3
# =============================================================================
#  build.py -- the dial is a folder of folders.
#
#  Every person on the site is one directory under web/public/peewee/people/:
#
#      people/eddie-murphy/
#          person.json     who they are: name, frequency, era, the Peewee
#                          connection, bridge lines
#          story-1.txt     the tellings, rotated so a repeat visit (or a
#          story-2.txt     repeat phone call) hears a different one
#          photo-*.jpg     their photographs from Peewee's collection
#          story-*.mp3     the same tellings in Randy's voice (narrate.py)
#
#  This script crawls those folders and writes data/stations.json, which is
#  all the website reads and all the phone line will read. ADDING A PERSON
#  IS THEREFORE: make a folder, drop in photos, write person.json and a
#  story or two, run  python peewee/build.py.  Nothing else. No code.
#
#  Order on the dial comes from the "freq" in each person.json, lowest
#  first. Peewee himself sits at AM 520 -- the first voice, bottom of the
#  band -- and nothing here may take that from him.
#
#  A folder with "enabled": false in its person.json is skipped entirely:
#  present, editable, invisible. That is the family's veto, one key deep.
# =============================================================================
import io, json, os, re, sys

SITE = os.environ.get("PEEWEE_SITE") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "web", "public", "peewee")
PEOPLE = os.path.join(SITE, "people")


def freq_num(f):
    m = re.search(r"(\d+)", f or "")
    return int(m.group(1)) if m else 9999



# ---------------------------------------------------------------------
#  WHO CAN HOST (Jason, 2026-09-02). The site used to hard-code a
#  two-way switch between Randy and Jason. This is the table instead,
#  and a voice is only OFFERED once its clips actually exist on disk --
#  otherwise the picker shows a name that silently falls back to Randy,
#  which reads as a bug rather than as a missing render.
#
#  To add a host: put a clean 10-30s sample at voices/<Name>.wav, add a
#  row here, then
#      VOICE_TAG=-tony NARRATE_VOICE=Tony python narrate.py
#      VOICE_TAG=-tony NARRATE_VOICE=Tony python patter.py
#      VOICE_TAG=-tony NARRATE_VOICE=Tony python intros.py
#      VOICE_TAG=-tony NARRATE_VOICE=Tony python handoffs.py
#  and rebuild. Nothing in the page changes.
# ---------------------------------------------------------------------
HOST_VOICES = [
    ("randy", "Randy", ""),
    ("jason", "Jason", "-jason"),
    ("tony", "Tony", "-tony"),
]


def hosts_available(people_dir):
    """A host counts as ready when at least one telling exists in it."""
    out = []
    for key, name, tag in HOST_VOICES:
        found = 0
        for slug in os.listdir(people_dir):
            d = os.path.join(people_dir, slug)
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                if re.fullmatch(r"story-\d+" + re.escape(tag) + r"\.mp3", f):
                    found += 1
                    break
        if found:
            out.append({"key": key, "name": name, "tag": tag,
                        "tellings": found})
    return out


def main():
    stations = []
    for slug in sorted(os.listdir(PEOPLE)):
        d = os.path.join(PEOPLE, slug)
        pj = os.path.join(d, "person.json")
        if not os.path.isdir(d) or not os.path.exists(pj):
            continue
        p = json.load(io.open(pj, encoding="utf-8"))
        if p.get("enabled") is False:
            print(f"  {slug}: disabled, skipped")
            continue
        files = sorted(os.listdir(d))
        stories = [f for f in files if re.fullmatch(r"story-\d+\.txt", f)]
        # PHOTOGRAPHS OF THE PERSON, and nothing else. A file prefixed
        # era- or woj- is period material used to illustrate a
        # paragraph -- public domain, from somewhere else, captioned as
        # such. Letting it into this list would put it in the photo
        # strip beside the family's own pictures with no caption at all,
        # which is precisely the mistake the captions exist to prevent.
        photos = [f for f in files
                  if f.lower().endswith((".jpg", ".jpeg", ".png"))
                  and not f.lower().startswith(("era-", "woj-"))]
        audio = []
        for s in stories:
            mp3 = s.replace(".txt", ".mp3")
            audio.append(f"people/{slug}/{mp3}" if mp3 in files else None)
        # THE SEAMS. Each telling's paragraphs, rendered separately by
        # narrate.py, in order. The player walks these instead of the one
        # long file so the broadcast can interrupt between them and then
        # resume. Empty list = no parts rendered; fall back to `audio`.
        story_parts = []
        for s in stories:
            stem = s[:-4]
            ps = sorted((f for f in files
                         if re.fullmatch(re.escape(stem) + r"-p\d+\.mp3", f)),
                        key=lambda f: int(re.search(r"-p(\d+)\.mp3$", f).group(1)))
            # ALL OR NOTHING. A half-rendered telling would publish two of
            # its three paragraphs and quietly drop the last one, which
            # sounds like the story simply stops. Unless the set matches
            # the paragraph count, fall back to the whole-file render.
            body = io.open(os.path.join(d, s), encoding="utf-8").read().strip()
            want = len([b for b in re.split(r"\n\s*\n", body) if b.strip()])
            story_parts.append([f"people/{slug}/{f}" for f in ps]
                               if len(ps) == want and want > 1 else [])
        handoff_next = [f"people/{slug}/{f}" for f in files
                        if re.fullmatch(r"handoff-next-\d+\.mp3", f)]
        handoff_in = [f"people/{slug}/{f}" for f in files
                      if re.fullmatch(r"handoff-in-\d+\.mp3", f)]
        handoff_dear = [f"people/{slug}/{f}" for f in files
                        if re.fullmatch(r"handoff-dear-\d+\.mp3", f)]
        stations.append({
            "slug": slug,
            "name": p.get("name", slug),
            "freq": p.get("freq", "AM 1600"),
            "era": p.get("era", ""),
            "photo": f"people/{slug}/{photos[0]}" if photos else None,
            "photos": [f"people/{slug}/{f}" for f in photos],
            "caption": p.get("caption"),
            "stories": [f"people/{slug}/{f}" for f in stories],
            "audio": audio,
            "story_parts": story_parts,
            # the host's patter, pre-rendered per ARRIVING artist so any
            # random order works (handoffs.py)
            "handoff_next": handoff_next,
            "handoff_in": handoff_in,
            "handoff_dear": handoff_dear,
            "bridges": p.get("bridges", []),
            "relation": p.get("relation"),
            "draft": p.get("draft", True),
        })
        print(f"  {slug}: {len(stories)} tellings, {len(photos)} photos, "
              f"{sum(1 for a in audio if a)} narrated, "
              f"{sum(len(x) for x in story_parts)} parts")

    # Pee-Wee is PINNED to the top of the dial regardless of frequency.
    # His real station was WABY -- AM 1400, the middle of the band -- and the
    # truth of that number beats the tidiness of a sort: the page opens on
    # him because he is the star, not because of arithmetic on his callsign.
    stations.sort(key=lambda s: (s["slug"] != "peewee-harris",
                                 freq_num(s["freq"])))
    if not stations or stations[0]["slug"] != "peewee-harris":
        sys.exit("REFUSING: Pee-Wee's folder is missing -- this is his dial")

    wall = sorted(f for f in os.listdir(os.path.join(SITE, "photos"))
                  if f.endswith(".jpg"))
    out = os.path.join(SITE, "data", "stations.json")
    hosts = hosts_available(PEOPLE)
    print("hosts ready: " + ", ".join(
        f"{h['name']} ({h['tellings']} tellings)" for h in hosts))
    json.dump({"stations": stations, "wall": wall, "hosts": hosts},
              io.open(out, "w", encoding="utf-8", newline="\n"), indent=1)
    print(f"{len(stations)} stations on the dial")


if __name__ == "__main__":
    main()
