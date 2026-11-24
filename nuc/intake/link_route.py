#!/usr/bin/env python3
"""Shared links that are YouTube videos or songs -> MeTube (yt-dlp) instead of a PDF of the page.

T0889 step 2 (2026-11-22, autopilot). Jason, 2026-11-19: the drop's link job is mainly
"YouTube -> mp3 (audio) and mp4 (video)"; mp3 -> Navidrome, mp4 -> Jellyfin.

WHAT THIS MODULE DOES
- youtube_kind(url): is this a YouTube / YouTube Music / Shorts link? Returns "music", "video" or None.
- pick_format(url, text): "mp3" or "mp4". Words in the shared text win ("mp3", "audio", "song" / "mp4",
  "video"); otherwise music.youtube.com -> mp3 and every other YouTube link -> mp4.
- metube_payload(url, fmt): the JSON body MeTube's POST /add expects.
- send_to_metube(url, fmt): hands the link to MeTube and returns at once (MeTube downloads in its own
  queue), so no download ever runs on the phone's request thread.

OFF UNLESS METUBE_URL IS SET. With METUBE_URL unset, enabled() is False and share.py keeps today's
behaviour (every link is printed to PDF). That is on purpose: see the destination problem below.

OUTCOMES (checked 2026-11-22 against the running Pi container, METUBE_VERSION=2026.08.28)
- MeTube's /add REQUIRES url, download_type and quality (main.py parse_download_options); "format" must be
  in VALID_VIDEO_FORMATS {any, mp4, ios} for video and VALID_AUDIO_FORMATS {m4a, mp3, opus, wav, flac} for
  audio. Older MeTube took {"url", "quality", "format"} only; that body is refused (400) by this version.
- "folder" is accepted because the Pi runs MeTube with CREATE_CUSTOM_DIRS=true; it is relative to
  DOWNLOAD_DIR (/downloads).
- DESTINATION PROBLEM (not solved here): MeTube's /downloads is /mnt/downloads on the Pi's SD card
  (/dev/mmcblk0p2, the root fs), not the NAS. Navidrome reads NAS /volume1/Music (NFS volume
  alex_nas_music) and Jellyfin is not on the Pi, so files queued today would stay on the SD card.
  Turning METUBE_URL on waits for the Ask on T0889 (where MeTube's files should land).
"""
from __future__ import annotations

import os
import re
import urllib.parse

import httpx

# Base URL of MeTube as seen from the intake container, e.g. http://192.168.1.69:8081. Unset = off.
METUBE = os.environ.get("METUBE_URL", "").rstrip("/")
# Folders under MeTube's DOWNLOAD_DIR, one per library. Defaults are plain names, set per machine.
FOLDER = {"mp3": os.environ.get("METUBE_FOLDER_AUDIO", "music"),
          "mp4": os.environ.get("METUBE_FOLDER_VIDEO", "video")}

_VIDEO_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
_MUSIC_HOSTS = {"music.youtube.com"}
# A YouTube id is 11 characters of [A-Za-z0-9_-]; checked so "youtube.com/about" is not a video.
_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_AUDIO_WORDS = re.compile(r"\b(mp3|audio|song|music)\b", re.I)
_VIDEO_WORDS = re.compile(r"\b(mp4|video)\b", re.I)


def enabled() -> bool:
    """True only when METUBE_URL is set; share.py checks this before routing any link here."""
    return bool(METUBE)


def youtube_kind(url: str | None) -> str | None:
    """Return "music" for a YouTube Music link, "video" for a YouTube / Shorts / youtu.be link, else None.

    Only https/http links on the exact YouTube hosts count; look-alikes such as youtube.com.evil.example
    or evil.example/?u=youtube.com are None. Playlists (list= with no v=) count, MeTube takes them.
    """
    if not url:
        return None
    try:
        p = urllib.parse.urlsplit(url.strip())
    except ValueError:
        return None
    if p.scheme not in ("http", "https") or not p.hostname:
        return None
    host = p.hostname.lower()
    q = urllib.parse.parse_qs(p.query)
    parts = [s for s in p.path.split("/") if s]
    if host in _MUSIC_HOSTS:
        return "music" if (q.get("v") or q.get("list")) else None
    if host not in _VIDEO_HOSTS:
        return None
    if host.endswith("youtu.be"):
        return "video" if parts and _ID.match(parts[0]) else None
    if parts[:1] == ["watch"] and q.get("v") and _ID.match(q["v"][0]):
        return "video"
    if len(parts) >= 2 and parts[0] in ("shorts", "live", "embed") and _ID.match(parts[1]):
        return "video"
    if parts[:1] == ["playlist"] and q.get("list"):
        return "video"
    return None


def pick_format(url: str, text: str | None = None) -> str:
    """Choose "mp3" or "mp4" for a YouTube link; shared words beat the host default.

    The share sheet has no format picker, so a word in the shared text is the only way to choose from
    the phone. Both kinds of word present -> host default (ambiguous, do not guess).
    """
    words = text or ""
    audio, video = bool(_AUDIO_WORDS.search(words)), bool(_VIDEO_WORDS.search(words))
    if audio and not video:
        return "mp3"
    if video and not audio:
        return "mp4"
    return "mp3" if youtube_kind(url) == "music" else "mp4"


def metube_payload(url: str, fmt: str) -> dict:
    """Build MeTube 2026.08's POST /add body for an mp3 or mp4 download (see OUTCOMES above)."""
    if fmt == "mp3":
        body = {"download_type": "audio", "format": "mp3", "quality": "best"}
    elif fmt == "mp4":
        body = {"download_type": "video", "format": "mp4", "quality": "best", "codec": "auto"}
    else:
        raise ValueError(f"fmt must be mp3 or mp4, not {fmt!r}")
    body.update(url=url, folder=FOLDER[fmt], auto_start=True)
    return body


async def send_to_metube(url: str, fmt: str, client: httpx.AsyncClient | None = None) -> dict:
    """Queue one link on MeTube; return a result row in share.py's shape (name, status, why).

    MeTube answers {"status": "ok"} or {"status": "error", "msg": ...} with HTTP 200, so the body is read,
    not only the status code. Any failure is a row with status "error"; the caller decides the fallback.
    """
    if not METUBE:
        return {"name": url, "status": "error", "why": "the video downloader is not switched on"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=15)
    try:
        r = await client.post(f"{METUBE}/add", json=metube_payload(url, fmt))
        r.raise_for_status()
        data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        return {"name": url, "status": "error", "why": "the video downloader did not answer; try again later",
                "detail": f"{type(e).__name__}: {e}"}
    finally:
        if own:
            await client.aclose()
    if data.get("status") != "ok":
        return {"name": url, "status": "error", "why": "the video downloader refused the link",
                "detail": str(data.get("msg", data))[:300]}
    where = "Navidrome (music)" if fmt == "mp3" else "Jellyfin (videos)"
    return {"name": url, "status": "queued", "why": f"downloading as {fmt}; it will show up in {where}"}


# ---- T0889 step 4 (2026-11-22, autopilot): plain file links, decided by Content-Type --------------------
# A link straight to a file (a PDF, a Word file, a photo) is downloaded and filed like an upload, instead
# of printing the browser's PDF viewer or image page to a PDF. The upload logic (app.route_file +
# classify.py) then sends documents to Paperless and images to the photo shelf, exactly as for a phone
# upload; this module only decides "is it a file, and what is it called".
# Anything else (text/html, unknown types) returns None and keeps today's path: PDF of the page.
FILE_TYPES = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "application/vnd.oasis.opendocument.text": ".odt",
    "application/rtf": ".rtf",
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
    "image/heic": ".heic", "image/heif": ".heif", "image/tiff": ".tif",
}
# Servers that send "application/octet-stream" (or nothing) for a file: trust the name's extension, but
# only for these. image/svg+xml is left out on purpose: an SVG can carry script, and a PDF of it is safer.
_EXT_OK = set(FILE_TYPES.values()) | {".jpeg", ".tiff"}
# Link downloads run on the phone's request (there is no queue yet, T0884), so they get a smaller cap than
# uploads (app.MAX_MB, 2 GB). Big files belong on the resumable upload path or the torrent client.
LINK_MAX_MB = int(os.environ.get("LINK_MAX_MB", "200"))


def file_kind(info: dict) -> str | None:
    """Extension (".pdf", ".jpg", ...) when a probe() result is a direct file link we file, else None."""
    ext = FILE_TYPES.get(info.get("content_type") or "")
    if ext:
        return ext
    if (info.get("content_type") or "") in ("", "application/octet-stream", "binary/octet-stream"):
        for name in (info.get("filename") or "", urllib.parse.urlsplit(info.get("url") or "").path):
            suffix = os.path.splitext(name)[1].lower()
            if suffix in _EXT_OK:
                return suffix
    return None


def file_name(info: dict, ext: str) -> str:
    """A file name for the download: Content-Disposition name, else the URL's last part, else "shared".

    The extension is forced to the one file_kind() chose, so a "report.php" that serves a PDF is filed as
    "report.php.pdf" and the classifier sees what the file really is.
    """
    raw = info.get("filename") or urllib.parse.unquote(
        urllib.parse.urlsplit(info.get("url") or "").path.rsplit("/", 1)[-1])
    raw = re.sub(r"[^A-Za-z0-9 ._()-]+", "_", raw).strip(" ._")[:100] or "shared"
    same = {ext, ".jpeg"} if ext == ".jpg" else {ext, ".tiff"} if ext == ".tif" else {ext}
    return raw if os.path.splitext(raw)[1].lower() in same else raw + ext
