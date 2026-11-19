#!/usr/bin/env python3
"""Write an accurate transcript beside every reference clip.

WHY THIS IS NOT OPTIONAL ANY MORE
Two reasons, one new and one latent.

1. F5-TTS REQUIRES ref_text. Its infer() signature takes ref_file AND
   ref_text with no default, and it does not transcribe for you -- the
   class has a transcribe() method but omitting ref_text does not call it.
   Hand it a transcript that does not match the audio and the clone comes
   out wrong in a way that is hard to attribute.

2. The .txt files next to the clips were transcripts of the FULL takes.
   Once trim-references.py cut every clip to its best 14 seconds, those
   files described audio that no longer exists. Nothing in the code reads
   them -- they were notes for humans -- so nothing broke. But a stale
   note is worse than no note, and the moment F5-TTS starts reading them
   it stops being cosmetic.

The transcription is done by the faster-whisper service ALREADY RUNNING in
this stack, not by a new dependency. It is the right tool, it is warm, and
it is on the same GPU.

    python transcribe-references.py            # show what it would write
    python transcribe-references.py --apply
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.request
import uuid
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


VOICES = Path(os.environ.get("VOICES_DIR", "/voices"))
STT = os.environ.get("STT_URL", "http://stt:8092/v1/audio/transcriptions")


def _multipart(path: Path) -> tuple[bytes, str]:
    """Build a multipart body by hand -- the image has no `requests`."""
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(path.name)[0] or "audio/wav"
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'.encode(),
        f"Content-Type: {ctype}\r\n\r\n".encode(),
        path.read_bytes(),
        f"\r\n--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="model"\r\n\r\nwhisper-1\r\n',
        f"--{boundary}--\r\n".encode(),
    ])
    return body, f"multipart/form-data; boundary={boundary}"


def transcribe(path: Path) -> str:
    body, ctype = _multipart(path)
    req = urllib.request.Request(STT, data=body, headers={"Content-Type": ctype})
    with urllib.request.urlopen(_http_only(req), timeout=600) as r:  # nosec B310
        return (json.loads(r.read()).get("text") or "").strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    wavs = sorted(p for p in VOICES.glob("*.wav"))
    if not wavs:
        print(f"no clips in {VOICES}")
        return 1

    for wav in wavs:
        txt = wav.with_suffix(".txt")
        try:
            text = transcribe(wav)
        except Exception as exc:  # noqa: BLE001
            print(f"  {wav.name:<22} FAILED  {str(exc)[:80]}")
            continue
        if not text:
            print(f"  {wav.name:<22} empty transcript -- skipped")
            continue
        print(f"  {wav.name:<22} {len(text):>4} chars  {text[:70]}...")
        if args.apply:
            # Keep the old note if there was one: it described the full take
            # and may be the only record of what was said outside the cut.
            if txt.exists() and not txt.with_suffix(".full.txt").exists():
                txt.replace(txt.with_suffix(".full.txt"))
            txt.write_text(text + "\n", encoding="utf-8", newline="\n")

    if not args.apply:
        print("\n  dry run. Re-run with --apply to write the .txt files.")
    else:
        print("\n  written. Previous notes kept as <voice>.full.txt")
    return 0


if __name__ == "__main__":
    sys.exit(main())
