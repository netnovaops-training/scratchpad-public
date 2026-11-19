#!/usr/bin/env python3
"""Turn a Spotify or YouTube playlist into a Navidrome playlist of YOUR files.

    python3 playlist-match.py --from https://open.spotify.com/playlist/37i9...
    python3 playlist-match.py --from https://youtube.com/playlist?list=PL...
    python3 playlist-match.py --from tracks.txt --name "Friday Set"

WHAT THIS DOES AND DOES NOT DO.

It reads the TRACK LIST -- artist and title, text -- and finds those
songs in the 36,000 files already on the NAS. It downloads no audio from
anywhere. Spotify is read through its public Web API, which returns
metadata and never audio; YouTube through yt-dlp's --flat-playlist, which
lists a playlist's titles without touching a single media stream.

That is a deliberate limit, not an oversight. It is also the better
result for a DJ set: a 1411kbps FLAC off the NAS beats a 128k re-encode
of a YouTube upload, and the files are already tagged the way the tagging
software left them.

Anything the library does not have is written to a `missing` file so it
can be bought rather than guessed at.

---------------------------------------------------------------------
MATCHING, AND WHY IT REFUSES TO GUESS

Matching "Daft Punk - Around the World" against a real library is fuzzy:
the file might be "Daft Punk - Around The World (Radio Edit)", or tagged
"Daft Punk feat. nobody", or be a live version that is a different song
wearing the same name.

So there are three outcomes, not two:

  MATCHED     score >= STRONG. Goes in the playlist.
  AMBIGUOUS   score between WEAK and STRONG. Goes in the report with the
              candidate, NOT in the playlist.
  MISSING     nothing close. Goes in the report.

A wrong track in a DJ set is worse than a missing one -- a gap is a gap,
but the wrong song plays in front of people. So the middle band is
surfaced for a human rather than resolved by a threshold.
"""
import argparse
import base64
import difflib
import hashlib
import json
import os
import random
import re
import string
import subprocess
import sys
import urllib.parse
import urllib.request


def _http_only(target):
    """Return target (a URL string or urllib Request) if its scheme is http or https, else raise ValueError.

    urlopen also follows file:// and custom schemes (Bandit B310). Every caller here talks to an
    http(s) service, so anything else is a mistake or a planted URL. Added 2026-11-19.
    """
    url = str(getattr(target, "full_url", target))
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError(f"refusing a non-http(s) URL: {url[:80]}")
    return target


STRONG = 0.88
WEAK = 0.62
# Below this many characters, difflib's ratio is not trustworthy: "Part 2"
# and "Part 3" score 0.83 because five of six characters agree. Short
# titles must match exactly after normalisation or they do not match.
SHORT = 10

# Qualifiers that describe the same recording and should be ignored.
# "live", "acoustic", "demo", "instrumental" are NOT here on purpose --
# those are genuinely different recordings, and a DJ set that silently
# substitutes the live cut for the studio one is the failure this whole
# script exists to avoid.
# Markers that mean THIS IS A DIFFERENT RECORDING. If one side has one
# and the other does not, the two are not the same track no matter how
# similar the text is.
#
# This exists because the first test run scored "Smells Like Teen Spirit"
# against "Smells Like Teen Spirit - Live" at 0.90 and called it a MATCH.
# Leaving "live" out of QUALIFIERS was not enough: the strings are still
# 90% identical, so difflib matched them anyway. The comment claiming
# these were safe was true about stripping and wrong about scoring.
DISTINCT = re.compile(
    r"\b(live|acoustic|unplugged|demo|instrumental|karaoke|"
    r"remix|rmx|cover|reprise|edit\s*version|mashup|bootleg|"
    r"sped\s*up|slowed|nightcore|8d)\b")

QUALIFIERS = (r"radio\s*edit|single\s*version|album\s*version|extended|"
              r"original\s*mix|official(?:\s*(?:audio|video|music\s*video))?|"
              r"lyrics?(?:\s*video)?|audio|video|hd|hq|4k|"
              r"\d{0,4}\s*re-?master(?:ed)?(?:\s*version)?|"
              r"explicit|clean|bonus\s*track|digital\s*remaster(?:ed)?")

ND_URL = os.environ.get("ND_URL", "http://192.168.1.69:4533")
ND_USER = os.environ.get("ND_USER", "")
ND_PASS = os.environ.get("ND_PASS", "")


# ---------------------------------------------------------------- helpers
def norm(s: str) -> str:
    """Fold a title to something comparable.

    Strips the noise that differs between a streaming service's metadata
    and a local file's tags, and nothing that distinguishes two songs:
    bracketed qualifiers, feat. credits, punctuation, articles, case.
    Deliberately KEEPS digits -- "Part 2" is not "Part 3".
    """
    s = s.lower()
    # feat. credits, in every shape they arrive in
    s = re.sub(r"\((?:feat|ft|with)\.?[^)]*\)", " ", s)
    s = re.sub(r"\[(?:feat|ft|with)\.?[^\]]*\]", " ", s)
    s = re.sub(r"\b(?:feat|ft)\.?\s.*$", " ", s)
    # Qualifiers in brackets: "(Radio Edit)", "[2011 Remastered]"
    s = re.sub(r"[\(\[]\s*(?:" + QUALIFIERS + r")\s*[\)\]]", " ", s)
    # And the same qualifiers after a dash, which is how streaming
    # services write them: "Dreams - 2004 Remaster". Without this the
    # dash form scored 0.46 against the plain title and was reported
    # MISSING for a track sitting in the library.
    s = re.sub(r"\s-\s*(?:" + QUALIFIERS + r")\s*$", " ", s)
    # Anything else still bracketed is noise for comparison purposes.
    s = re.sub(r"\[[^\]]*\]", " ", s)
    s = s.replace("&", " and ")
    s = s.translate(str.maketrans("", "", string.punctuation))
    s = re.sub(r"^\s*(?:the|a|an)\s+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def score(a: str, b: str) -> float:
    na, nb = norm(a), norm(b)
    if not na or not nb:
        return 0.0
    # Short titles: exact or nothing. See SHORT.
    if len(na) < SHORT or len(nb) < SHORT:
        return 1.0 if na == nb else 0.0
    r = difflib.SequenceMatcher(None, na, nb).ratio()

    # A live/remix/acoustic marker on one side only caps the result below
    # STRONG, so it lands in the ambiguous report instead of the playlist.
    # Capped rather than zeroed on purpose: sometimes the live cut IS the
    # one wanted, and that is a decision for a person, not a threshold.
    da, db = bool(DISTINCT.search(na)), bool(DISTINCT.search(nb))
    if da != db:
        r = min(r, (STRONG + WEAK) / 2)
    return r


# ------------------------------------------------------------- sources
def from_spotify(url: str):
    """Public Web API. Metadata only -- this endpoint returns no audio."""
    cid = os.environ.get("SPOTIFY_CLIENT_ID", "")
    sec = os.environ.get("SPOTIFY_CLIENT_SECRET", "")
    if not (cid and sec):
        sys.exit("  SPOTIFY_CLIENT_ID / _SECRET are not set.\n"
                 "  developer.spotify.com -> create an app -> copy both\n"
                 "  into ai-stack/.env. Client-credentials only; it never\n"
                 "  touches your Spotify account.")
    m = re.search(r"playlist[/:]([A-Za-z0-9]+)", url)
    if not m:
        sys.exit(f"  not a Spotify playlist url: {url}")
    pid = m.group(1)

    basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    req = urllib.request.Request(
        "https://accounts.spotify.com/api/token",
        data=b"grant_type=client_credentials",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    token = json.load(urllib.request.urlopen(_http_only(req), timeout=20))["access_token"]  # nosec B310

    out, url_next = [], (f"https://api.spotify.com/v1/playlists/{pid}/tracks"
                         "?fields=items(track(name,artists(name))),next&limit=100")
    while url_next:
        r = urllib.request.Request(url_next,
                                   headers={"Authorization": f"Bearer {token}"})
        d = json.load(urllib.request.urlopen(_http_only(r), timeout=30))  # nosec B310
        for it in d.get("items", []):
            t = it.get("track") or {}
            if not t.get("name"):
                continue  # removed or region-locked entries come back null
            out.append((", ".join(a["name"] for a in t.get("artists", [])),
                        t["name"]))
        url_next = d.get("next")
    return out


def from_youtube(url: str):
    """yt-dlp --flat-playlist: lists titles, downloads nothing.

    --skip-download is redundant with --flat-playlist and set anyway, so
    that a future edit cannot turn this into a downloader by removing one
    flag.
    """
    if not shutil_which("yt-dlp"):
        sys.exit("  yt-dlp is not installed here.")
    p = subprocess.run(
        ["yt-dlp", "--flat-playlist", "--skip-download", "--no-warnings",
         "--dump-single-json", url],
        capture_output=True, text=True, timeout=180, check=False)
    if p.returncode != 0:
        sys.exit(f"  yt-dlp failed: {p.stderr.strip()[:200]}")
    data = json.loads(p.stdout)
    out = []
    for e in data.get("entries", []):
        title = e.get("title") or ""
        if not title or title in ("[Deleted video]", "[Private video]"):
            continue
        # Uploads are usually "Artist - Title"; uploader is the fallback
        # and is often a label channel rather than the artist, so it is
        # used only when the title has no separator.
        if " - " in title:
            artist, _, name = title.partition(" - ")
        else:
            artist, name = e.get("uploader") or "", title
        out.append((artist.strip(), name.strip()))
    return out


def from_file(path: str):
    """One track per line, "Artist - Title" or just a title."""
    out = []
    with open(path, encoding="utf-8") as fh_:
        for line in fh_:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if " - " in line:
                a, _, t = line.partition(" - ")
                out.append((a.strip(), t.strip()))
            else:
                out.append(("", line))
    return out


def shutil_which(name):
    from shutil import which
    return which(name)


# ---------------------------------------------------------- navidrome
def nd_call(endpoint: str, **params):
    """Subsonic API. Salted-token auth -- the password never crosses the
    wire, even on the LAN."""
    if not (ND_USER and ND_PASS):
        sys.exit("  ND_USER / ND_PASS not set (Navidrome login).")
    salt = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
    # The Subsonic API defines the token as md5(password + salt); the hash is the protocol, not our choice.
    token = hashlib.md5((ND_PASS + salt).encode(), usedforsecurity=False).hexdigest()
    q = {"u": ND_USER, "t": token, "s": salt, "v": "1.16.1",
         "c": "playlist-match", "f": "json", **params}
    url = f"{ND_URL}/rest/{endpoint}?{urllib.parse.urlencode(q, doseq=True)}"
    d = json.load(urllib.request.urlopen(_http_only(url), timeout=30))["subsonic-response"]  # nosec B310
    if d.get("status") != "ok":
        sys.exit(f"  navidrome: {d.get('error', {}).get('message', d)}")
    return d


def find(artist: str, title: str):
    """Best local candidate for one track, with its score."""
    query = f"{artist} {title}".strip()
    d = nd_call("search3", query=query, songCount=25,
                artistCount=0, albumCount=0)
    songs = d.get("searchResult3", {}).get("song", [])
    if not songs and artist:
        # Retry on the title alone. A tag that spells the artist
        # differently should not hide a file that is right there.
        d = nd_call("search3", query=title, songCount=25,
                    artistCount=0, albumCount=0)
        songs = d.get("searchResult3", {}).get("song", [])

    best, best_s = None, 0.0
    for s in songs:
        st = score(title, s.get("title", ""))
        sa = score(artist, s.get("artist", "")) if artist else 1.0
        # Title carries the weight. A compilation can credit "Various
        # Artists" and still be exactly the right recording.
        combined = st * 0.75 + sa * 0.25
        if combined > best_s:
            best, best_s = s, combined
    return best, best_s


# ---------------------------------------------------------------- main
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True)
    ap.add_argument("--name", default="")
    ap.add_argument("--report", default="playlist-report.txt")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.src.startswith("http") and "spotify" in a.src:
        tracks, kind = from_spotify(a.src), "spotify"
    elif a.src.startswith("http"):
        tracks, kind = from_youtube(a.src), "youtube"
    else:
        tracks, kind = from_file(a.src), "file"

    if not tracks:
        sys.exit("  no tracks found in that playlist")
    name = a.name or f"{kind} import"
    print(f"  {len(tracks)} tracks from {kind}")
    print()

    matched, ambiguous, missing = [], [], []
    for artist, title in tracks:
        song, s = find(artist, title)
        label = f"{artist} - {title}" if artist else title
        if song and s >= STRONG:
            matched.append((song, label, s))
            print(f"  ok   {s:.2f}  {label}")
        elif song and s >= WEAK:
            ambiguous.append((song, label, s))
            print(f"  ??   {s:.2f}  {label}")
            print(f"              maybe: {song.get('artist')} - {song.get('title')}")
        else:
            missing.append(label)
            print(f"  --         {label}")

    print()
    print(f"  matched {len(matched)}, ambiguous {len(ambiguous)}, "
          f"missing {len(missing)}")

    with open(a.report, "w", encoding="utf-8") as fh:
        fh.write(f"# {name} -- {len(tracks)} tracks from {kind}\n\n")
        fh.write(f"## Ambiguous ({len(ambiguous)}) -- NOT added, check these\n")
        for song, label, s in ambiguous:
            fh.write(f"{s:.2f}  {label}\n      -> {song.get('artist')} - "
                     f"{song.get('title')}  [id {song.get('id')}]\n")
        fh.write(f"\n## Missing ({len(missing)}) -- not in the library\n")
        for label in missing:
            fh.write(f"{label}\n")
    print(f"  report -> {a.report}")

    if a.dry_run:
        print("  DRY RUN -- no playlist created")
        return 0
    if not matched:
        print("  nothing matched; no playlist created")
        return 1

    d = nd_call("createPlaylist", name=name,
                songId=[s.get("id") for s, _, _ in matched])
    pid = d.get("playlist", {}).get("id", "?")
    print(f"  created '{name}' in Navidrome with {len(matched)} tracks "
          f"(id {pid})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
