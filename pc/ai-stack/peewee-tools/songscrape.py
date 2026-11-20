#!/usr/bin/env python3
# =============================================================================
#  songscrape.py -- brief song facts from Wikipedia (2026-09-01, Jason:
#  "very brief information... how popular they were... what genre").
#
#  For every record in the crate: a Wikipedia API search, the top page's
#  plain text, and the LOCAL model extracts at most: genre, one popularity
#  line, one more relevant line -- from the fetched text ONLY. The same
#  no-invention contract as the tellings: the model answers null over
#  guessing, and nulls are dropped rather than shown.
#
#  (DuckDuckGo's html endpoint serves a JS-gate shell now; for songs the
#  encyclopedia is the better liner notes anyway.)
#
#  Curated entries are respected: an existing record only GAINS a genre and
#  (up to) one new fact; a record with no entry at all gets built whole.
#  Source URLs land in data/songfacts-sources.json for the audit trail.
# =============================================================================
import json
import os
import urllib.parse
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


OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("DRAFT_MODEL", "gemma4:12b")
SITE = os.environ.get("PEEWEE_SITE") or os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "web", "public", "peewee"))
# Wikipedia asks API users to identify themselves; a real contact beats a
# fake browser string and is why the 403s stop.
UA = {"User-Agent": "PeeWeeTribute/1.0 (family memorial site; contact: "
                    "user3@example.com)"}


def wiki(query, limit=6000):
    """Search, then the top page's plain text. Returns (url, text) or
    (None, None)."""
    api = "https://en.wikipedia.org/w/api.php?"
    q = urllib.parse.urlencode({"action": "query", "list": "search",
                                "srsearch": query, "srlimit": 1,
                                "format": "json"})
    req = urllib.request.Request(api + q, headers=UA)
    with urllib.request.urlopen(_http_only(req), timeout=20) as r:  # nosec B310
        hits = json.loads(r.read()).get("query", {}).get("search", [])
    if not hits:
        return None, None
    title = hits[0]["title"]
    q = urllib.parse.urlencode({"action": "query", "prop": "extracts",
                                "explaintext": 1, "titles": title,
                                "format": "json", "exchars": limit})
    req = urllib.request.Request(api + q, headers=UA)
    with urllib.request.urlopen(_http_only(req), timeout=25) as r:  # nosec B310
        pages = json.loads(r.read()).get("query", {}).get("pages", {})
    text = next(iter(pages.values()), {}).get("extract", "")
    url = "https://en.wikipedia.org/wiki/" + urllib.parse.quote(
        title.replace(" ", "_"))
    return (url, text) if text else (None, None)


def extract(title_hint, source_text):
    prompt = (
        "From the source text below, and ONLY from it, give brief facts "
        f"about the song '{title_hint}'. Reply as JSON with keys: "
        '"title", "artist", "year" (number or null), "genre" (two words '
        'max, or null), "facts" (at most 2 strings, each ONE short '
        "sentence: how popular it was -- chart peaks, sales -- or one "
        "relevant thing). If the source does not say, use null; NEVER "
        "guess.\n\nSOURCE:\n" + source_text)
    body = json.dumps({"model": MODEL, "stream": False, "format": "json",
                       "options": {"temperature": 0.1},
                       "messages": [{"role": "user",
                                     "content": prompt}]}).encode()
    req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(_http_only(req), timeout=600) as r:  # nosec B310
        return json.loads(json.loads(r.read())["message"]["content"])


def main():
    os.chdir(SITE)
    music = json.loads(Path("data/music.json").read_text(encoding="utf-8"))
    facts = json.loads(Path("data/songfacts.json").read_text(encoding="utf-8"))
    try:
        sources = json.loads(Path("data/songfacts-sources.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        sources = {}

    changed = 0
    for fname in music:
        entry = facts.get(fname)
        needs_all = not isinstance(entry, dict)
        needs_genre = isinstance(entry, dict) and not entry.get("genre")
        if not (needs_all or needs_genre):
            continue
        hint = (entry or {}).get("title") or \
            fname.replace(".mp3", "").replace("-", " ")
        query = f"{hint} song" if needs_all else \
            f"{entry['title']} {entry['artist']} song"
        print(f"-- {fname}: searching '{query}'", flush=True)
        try:
            url, text = wiki(query)
            if not text:
                print("   nothing found", flush=True)
                continue
            got = extract(hint, text)
        except Exception as e:  # noqa: BLE001
            print("   failed:", e, flush=True)
            continue

        if needs_all:
            if not (got.get("title") and got.get("artist")):
                print("   source too thin; leaving it unknown", flush=True)
                continue
            facts[fname] = {
                "title": got["title"], "artist": got["artist"],
                "year": got.get("year"), "slugs": [],
                "genre": got.get("genre"),
                "facts": [f for f in (got.get("facts") or []) if f][:2],
            }
            print(f"   built: {got['title']} -- {got['artist']}", flush=True)
        else:
            if got.get("genre"):
                entry["genre"] = got["genre"]
                print(f"   genre: {got['genre']}", flush=True)
            new = [f for f in (got.get("facts") or []) if f
                   and f not in entry.get("facts", [])]
            if new and len(entry.get("facts", [])) < 4:
                entry["facts"] = (entry.get("facts") or []) + new[:1]
                print(f"   + {new[0][:60]}", flush=True)
        sources[fname] = url
        changed += 1

    if changed:
        Path("data/songfacts.json").write_text(json.dumps(facts, indent=2,
                                               ensure_ascii=False), encoding="utf-8", newline="\n")
        Path("data/songfacts-sources.json").write_text(json.dumps(sources, indent=2), encoding="utf-8", newline="\n")
    print(f"enriched {changed} records")


if __name__ == "__main__":
    main()
