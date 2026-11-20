"""Pulling a recipe out of a YouTube video's own spoken transcript --
Jason's own request (2026-08-25): "can we wire in on the same way
/recipe and kitchen is But I want use the internet and, YouTuber
transcripts and make a helpful walkthrough for my mom." "Wired in the
same way" means invisibly: importer.from_url() detects a YouTube link
and routes here instead of the normal HTML fetch, so the existing paste-
a-link box just works for a video too -- no separate button, no new
screen, nothing Mama has to know is different.

WHY yt-dlp, NOT THE OFFICIAL YOUTUBE DATA API
-----------------------------------------------
The Data API (v3) needs a Google Cloud project, an API key, and OAuth
scopes for caption DOWNLOAD specifically (the free/simple key only gets
you caption METADATA -- language, id -- never the text). yt-dlp reads
the same public caption tracks a viewer's own browser does, no key, no
quota, actively maintained against YouTube's own frequent changes. It is
a real, deliberate exception to requirements.txt's own "nothing added"
rule for exactly that reason -- there is no lighter way to do this
honestly.

NO FFMPEG, ON PURPOSE. `download=False` below never fetches the video or
audio at all -- only `extract_info()`'s own metadata dict, which
includes each caption track's own URL. This module fetches THAT url
directly (plain httpx, the caption file is JSON), same as fetching any
other page. Nothing here ever touches video/audio bytes, so there is
nothing for ffmpeg to transcode.

MANUAL CAPTIONS FIRST, AUTO-GENERATED AS A FALLBACK. A creator's own
uploaded captions are punctuated and generally accurate; YouTube's own
auto-generated ones are unpunctuated best-effort speech-to-text with
real error rates on ingredient names and quantities specifically (the
words most worth getting right here). Both are tried because most
cooking channels never upload their own -- auto-generated is what
usually exists -- but manual wins whenever it does.
"""
import asyncio
import re

import httpx
import yt_dlp

from . import core

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

_URL_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?v=|shorts/|embed/)|youtu\.be/)([\w-]{11})", re.I)


def video_id(url: str) -> str:
    """The 11-character video id, or "" if this isn't a YouTube URL at
    all -- importer.from_url() uses an empty result here to decide
    whether to route to this module or the normal page-fetch path."""
    m = _URL_RE.search(url or "")
    return m.group(1) if m else ""


def is_youtube_url(url: str) -> bool:
    return bool(video_id(url))


def _caption_text(events: list) -> str:
    """YouTube's own json3 caption format is a list of "events", each a
    span of time with one or more text "segs" -- concatenated back into
    plain prose, paragraphed on real pauses (a gap of 2+ seconds between
    one event ending and the next starting reads as a natural break in
    what's being said, same as a written recipe's own paragraphing)."""
    lines, current = [], []
    prev_end = 0.0
    for ev in events:
        segs = ev.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue
        start = (ev.get("tStartMs") or 0) / 1000.0
        if current and start - prev_end > 2.0:
            lines.append(" ".join(current))
            current = []
        current.append(text)
        prev_end = start + (ev.get("dDurationMs") or 0) / 1000.0
    if current:
        lines.append(" ".join(current))
    return "\n\n".join(lines)


async def fetch(url: str) -> dict:
    """{"title", "description", "thumbnail", "transcript", "source"} for
    one video. Raises core.Blocked with a specific, actionable reason --
    same contract crawl.fetch() already has, so importer.from_url()'s
    existing error handling doesn't need a special case for this path.
    """
    vid = video_id(url)
    if not vid:
        raise core.Blocked("That doesn't look like a YouTube link.")
    clean_url = f"https://www.youtube.com/watch?v={vid}"

    opts = {
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "en-US", "en-GB", "en-orig"],
    }
    def _extract():
        with yt_dlp.YoutubeDL(opts) as ydl:
            return ydl.extract_info(clean_url, download=False)

    try:
        # extract_info() is synchronous network I/O -- yt-dlp has no
        # async API -- so it runs off the event loop the same way any
        # other blocking call in an async function has to.
        info = await asyncio.to_thread(_extract)
    except yt_dlp.utils.DownloadError as e:
        raise core.Blocked(f"Could not open that video ({e}).")
    if not info:
        raise core.Blocked("Could not read anything from that video.")

    track = _pick_track(info.get("subtitles") or {})
    if not track:
        track = _pick_track(info.get("automatic_captions") or {})
    if not track:
        raise core.Blocked(
            "That video has no captions (manual or auto-generated) for me to "
            "read -- without them there's no text to work from. Paste the "
            "recipe in by hand instead if you have it written down somewhere.")

    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": UA}) as client:
        r = await client.get(track["url"])
        r.raise_for_status()
    data = r.json()
    transcript = _caption_text(data.get("events") or [])
    if len(transcript) < 100:
        raise core.Blocked(
            "That video's captions were too short or empty to find a real "
            "recipe in.")

    return {
        "title": (info.get("title") or "").strip(),
        "description": (info.get("description") or "").strip(),
        "thumbnail": info.get("thumbnail") or "",
        "transcript": transcript,
        "source": clean_url,
    }


def _pick_track(tracks: dict) -> "dict | None":
    """One caption track, preferring plain English over a dialect
    variant, and json3 over every other format yt-dlp lists (vtt/srv1/
    srv3/ttml) -- json3 is YouTube's own structured format, no cue-
    timing markup to strip back out, no rolling-caption duplicate lines
    the way auto-generated vtt tends to have."""
    for lang in ("en", "en-US", "en-GB", "en-orig"):
        formats = tracks.get(lang)
        if not formats:
            continue
        for fmt in formats:
            if fmt.get("ext") == "json3":
                return fmt
        return formats[0]
    return None
