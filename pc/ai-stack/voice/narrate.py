#!/usr/bin/env python3
"""Turn a document into an audiobook in one of the house's cloned voices.

Jason, 2026-09-10: "have a reader and an audio book that can use TTS of
voice models I have to read aloud documents too".

The hard part already existed. LuxTTS is running with nine cloned voices,
`/v1/audio/speech` already normalises page-punctuation into speech-punctuation
and turns blank lines into real silence, and the indexer already knows how to
get text out of a PDF or an EPUB. This is the piece between them.

RUNS ON THE PC. The voice service binds to 127.0.0.1:8091 and is not on the
LAN, so this cannot run on the NUC. That is the right way round -- the GPU is
here.

ONE FILE PER TRACK, AND NOTHING IS CONCATENATED. ffmpeg is not installed on
this machine, and rather than add it, the job is shaped so it is not needed:
each request produces one finished mp3 and each mp3 is one track.
Audiobookshelf, Plex and every phone player read a folder of numbered files
as one book. It also makes the whole thing RESUMABLE -- a track whose file
already exists is skipped, so an interrupted eight-hour run picks up where it
stopped instead of starting again.

A TEXTBOOK IS NOT A SENSIBLE AUDIOBOOK, and the estimate says so before you
commit. Saunders 9th is 5.7 million characters -- around 127 hours of speech,
and rather longer than that to generate. The tool prints the estimate and
refuses anything over an hour without --yes. Chapters and page ranges are the
point of --pages.

Usage:
    python narrate.py "path/to/book.pdf" --voice Mom --out M:/audiobooks
    python narrate.py notes.md --voice Nellie --pages 40-58 --yes
    python narrate.py --list-voices
"""
import argparse
import html
import os
import pathlib
import re
import sys
import time
import zipfile

import httpx

VOICE_URL = os.getenv("VOICE_URL", "http://127.0.0.1:8091")
VOICE_SECRET = os.getenv("VOICE_SECRET", "")

# The service caps `input` at 5000 characters. Staying under it with room to
# spare means a paragraph is never cut mid-sentence to fit.
TRACK_CHARS = int(os.getenv("NARRATE_TRACK_CHARS", "4200"))

# Speech runs about 150 words a minute and an English word averages a little
# over five characters with its space, so ~13 characters a second. Used only
# for the estimate; nothing depends on it being exact.
CHARS_PER_SECOND = 13.0


# --------------------------------------------------------------------------
# getting the words out

def read_pdf(p: pathlib.Path, first: int, last: int) -> str:
    from pypdf import PdfReader
    pages = PdfReader(str(p)).pages
    lo = max(1, first) - 1
    hi = min(len(pages), last) if last else len(pages)
    out = []
    for i in range(lo, hi):
        try:
            out.append(pages[i].extract_text() or "")
        except Exception:  # noqa: BLE001
            out.append("")
    return "\n\n".join(out)


def read_epub(p: pathlib.Path) -> str:
    out = []
    with zipfile.ZipFile(p) as z:
        for n in sorted(x for x in z.namelist()
                        if x.lower().endswith((".xhtml", ".html", ".htm"))):
            raw = z.read(n).decode("utf-8", "replace")
            raw = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw,
                         flags=re.DOTALL | re.IGNORECASE)
            # Paragraph tags become blank lines, which the service turns into
            # real silence. Losing them would produce one breathless block.
            raw = re.sub(r"</(p|div|h[1-6]|li)>", "\n\n", raw, flags=re.IGNORECASE)
            out.append(html.unescape(re.sub(r"<[^>]+>", " ", raw)))
    return "\n\n".join(out)


def read_text(p: pathlib.Path, first: int, last: int) -> str:
    s = p.suffix.lower()
    if s == ".pdf":
        return read_pdf(p, first, last)
    if s == ".epub":
        return read_epub(p)
    if s in (".txt", ".md", ".csv"):
        return p.read_text(encoding="utf-8", errors="replace")
    raise SystemExit(f"  don't know how to read {s} -- pdf, epub, txt or md")


# --------------------------------------------------------------------------
# cutting it into tracks

# A sentence ends at .!? followed by space and something that starts a new
# one. NOT at the dot in "0.5 mg" or "Dr." -- a textbook is full of both, and
# splitting there produces a track that stops mid-clause.
_SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z(\"'“‘])")


def tidy(text: str) -> str:
    """Collapse the layout noise, keep the paragraph breaks."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # A hyphen at a line end is a word broken by typesetting, not a compound.
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Single newlines inside a paragraph are line wrapping; blank lines are
    # real breaks and the service reads them as silence, so only the former
    # are flattened.
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    return text.strip()


def into_tracks(text: str, limit: int = TRACK_CHARS) -> list:
    """Blocks of at most `limit` characters, split only between sentences."""
    tracks, cur = [], ""
    for para in text.split("\n\n"):
        para = para.strip()
        if not para:
            continue
        # A paragraph longer than a whole track has to be broken; do it
        # between sentences rather than mid-word.
        pieces = [para] if len(para) <= limit else _SENT.split(para)
        for piece in pieces:
            if not piece.strip():
                continue
            if len(piece) > limit:
                # One unbroken run longer than a track -- rare, and a hard
                # cut is the only option left. Cut at a space.
                while len(piece) > limit:
                    cut = piece.rfind(" ", 0, limit) or limit
                    tracks.append((cur + "\n\n" + piece[:cut]).strip())
                    cur, piece = "", piece[cut:].strip()
            if len(cur) + len(piece) + 2 > limit:
                if cur:
                    tracks.append(cur.strip())
                cur = piece
            else:
                cur = f"{cur}\n\n{piece}" if cur else piece
    if cur.strip():
        tracks.append(cur.strip())
    return tracks


# --------------------------------------------------------------------------
# speaking

def speak(text: str, voice: str, fmt: str = "mp3", timeout: int = 900) -> bytes:
    headers = {"x-voice-secret": VOICE_SECRET} if VOICE_SECRET else {}
    r = httpx.post(f"{VOICE_URL}/v1/audio/speech", timeout=timeout,
                   headers=headers,
                   json={"input": text, "voice": voice,
                         # normalize does the work this script should not
                         # duplicate: em-dashes, curly quotes, year ranges,
                         # initialisms -- and blank lines become real pauses.
                         "normalize": True,
                         "response_format": fmt})
    r.raise_for_status()
    return r.content


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", help="pdf, epub, txt or md")
    ap.add_argument("--voice", default="Mom")
    ap.add_argument("--out", default="M:/audiobooks")
    ap.add_argument("--pages", default="", help="PDF only, e.g. 40-58")
    ap.add_argument("--format", default="mp3",
                    choices=["mp3", "wav", "flac", "ogg", "opus"])
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation for anything over an hour")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--list-voices", action="store_true")
    args = ap.parse_args()

    if args.list_voices:
        v = httpx.get(f"{VOICE_URL}/health", timeout=20).json()
        print("  voices:", ", ".join(v.get("voices", [])))
        print("  default:", v.get("default_voice"))
        return 0

    if not args.path:
        ap.error("give me a file, or --list-voices")

    src = pathlib.Path(args.path)
    if not src.is_file():
        print(f"  no such file: {src}")
        return 1

    first, last = 1, 0
    if args.pages:
        m = re.match(r"^(\d+)\s*-\s*(\d+)$", args.pages.strip())
        if not m:
            print("  --pages wants something like 40-58")
            return 1
        first, last = int(m.group(1)), int(m.group(2))

    print(f"  reading {src.name}")
    text = tidy(read_text(src, first, last))
    if not text:
        print("  no text came out of that file -- a scan with no text layer?")
        return 1

    tracks = into_tracks(text)
    secs = len(text) / CHARS_PER_SECOND
    print(f"  {len(text):,} characters -> {len(tracks)} track(s)")
    print(f"  roughly {secs / 60:.0f} minutes of audio "
          f"({secs / 3600:.1f} hours)")

    # A textbook read end to end is days of generation. Say so plainly rather
    # than starting an eight-hour job because somebody pointed at a big file.
    if secs > 3600 and not args.yes and not args.dry_run:
        print()
        print("  That is over an hour of audio, and generating it takes")
        print("  longer than listening to it. Narrate a chapter with")
        print("  --pages, or pass --yes if you really mean the whole thing.")
        return 2

    name = re.sub(r"[^\w\s.-]", "", src.stem)[:80].strip()
    if args.pages:
        name += f" (p{first}-{last})"
    dest = pathlib.Path(args.out) / name
    if args.dry_run:
        print(f"  DRY RUN -- would write {len(tracks)} file(s) to {dest}")
        print(f"  first track starts: {tracks[0][:120]}...")
        return 0

    dest.mkdir(parents=True, exist_ok=True)
    print(f"  writing to {dest}")

    started, made, skipped = time.time(), 0, 0
    for i, block in enumerate(tracks, 1):
        out = dest / f"{i:04d}.{args.format}"
        if out.exists() and out.stat().st_size > 0:
            skipped += 1
            continue
        t0 = time.time()
        try:
            audio = speak(block, args.voice, args.format)
        except Exception as e:  # noqa: BLE001
            # One failed track should not lose the other nine hundred. The
            # file is not written, so a re-run retries exactly this one.
            print(f"  [{i}/{len(tracks)}] FAILED: {e}", flush=True)
            continue
        # Written via a temp name: a half-written mp3 that exists would be
        # skipped by the resume check and leave a silent gap in the book.
        tmp = out.with_suffix(out.suffix + ".part")
        tmp.write_bytes(audio)
        tmp.replace(out)
        made += 1
        done = made + skipped
        rate = (time.time() - started) / max(made, 1)
        left = (len(tracks) - done) * rate
        print(f"  [{i}/{len(tracks)}] {len(audio) / 1024:>6.0f} KB  "
              f"{time.time() - t0:>5.0f}s  eta {left / 60:>5.0f}m", flush=True)

    print(f"\n  {made} written, {skipped} already there -> {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
