#!/usr/bin/env python3
"""Cut every voice reference down to the window the models actually want.

THE FINDING THIS EXISTS FOR (measured 2026-09-13, all nine clips):

    Jason.wav           133.2 s      Randy-full-90s.wav   87.8 s
    Jason-take2.wav      53.1 s      Randy.wav            30.3 s @ 32 kHz
    Nellie.wav           33.0 s      Randy-tuned.wav      30.3 s
    Mom.wav              28.6 s      Randy-old-30s.wav    30.3 s @ 32 kHz
    Mom-tight.wav        24.0 s

Every cloning TTS in use or under consideration -- Chatterbox, F5-TTS,
XTTS v2, Qwen3-TTS -- documents a reference of roughly 5 to 15 seconds.
None of them wants two minutes. A long clip does not give the model more
to work with: it averages the voice across every mood, pace and room tone
in the recording, which is exactly the "not accurate enough" Jason hears.

So this is the cheapest large gain available, and it costs nothing but a
few seconds of ffmpeg. It is worth doing BEFORE swapping engines, because
a better model on a 133-second reference will still lose to a worse model
on a good 14-second one.

HOW THE WINDOW IS CHOSEN
Not "the first 15 seconds", which usually catches a throat-clear and a
false start. Instead:
  1. ffmpeg silencedetect marks the quiet parts.
  2. Those invert into speech regions.
  3. Every candidate window starts at a speech ONSET -- never mid-word --
     and is scored by how much actual speech it contains.
  4. The best-scoring window wins, and the cut is nudged to end in a pause
     so the clip does not stop mid-syllable.

Originals are moved to voices/raw/ and never deleted. If a cut sounds
wrong, the source is right there.

    python trim-references.py            # dry run: show what it would do
    python trim-references.py --apply    # do it
    python trim-references.py --restore  # put the originals back
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

VOICES = Path("/voices")
RAW = VOICES / "raw"

# The target. 14 s sits in the middle of every published range (Chatterbox
# ~5 s+, F5-TTS 5-15 s, Qwen3-TTS 4-10 s) rather than at one model's edge,
# so the same clip suits whichever engine is serving.
TARGET = 14.0
MIN_KEEP = 8.0

# One rate for every voice. Randy was 32 kHz while everything else was
# 24 kHz, which means the two were conditioned differently for no reason
# anybody chose.
RATE = 24000

# -32 dB for 0.35 s is a pause between phrases, not a gap between words.
SILENCE_DB = -32
SILENCE_MIN = 0.35


def run(*args: str) -> str:
    p = subprocess.run(args, capture_output=True, text=True, check=False)
    return p.stdout + p.stderr


def duration(path: Path) -> float:
    out = run("ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=nw=1:nk=1", str(path)).strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


def silences(path: Path) -> list[tuple[float, float]]:
    out = run("ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
              "-af", f"silencedetect=noise={SILENCE_DB}dB:d={SILENCE_MIN}",
              "-f", "null", "-")
    starts = [float(m) for m in re.findall(r"silence_start: ([0-9.]+)", out)]
    ends = [float(m) for m in re.findall(r"silence_end: ([0-9.]+)", out)]
    return list(zip(starts, ends + [duration(path)] * (len(starts) - len(ends))))


def speech_regions(path: Path) -> list[tuple[float, float]]:
    """Invert the silences into spans where somebody is actually talking."""
    total = duration(path)
    quiet = silences(path)
    regions: list[tuple[float, float]] = []
    cursor = 0.0
    for s, e in quiet:
        if s > cursor:
            regions.append((cursor, s))
        cursor = max(cursor, e)
    if cursor < total:
        regions.append((cursor, total))
    return [(a, b) for a, b in regions if b - a > 0.15]


def speech_in(regions, a: float, b: float) -> float:
    return sum(max(0.0, min(b, e) - max(a, s)) for s, e in regions)


def best_window(path: Path) -> tuple[float, float]:
    """The densest TARGET-second window that begins at a speech onset."""
    total = duration(path)
    if total <= TARGET:
        return 0.0, total
    regions = speech_regions(path)
    if not regions:
        # No detectable pauses at all: skip a likely throat-clear and take
        # the middle, which beats the first seconds of any recording.
        start = min(1.0, max(0.0, (total - TARGET) / 2))
        return start, start + TARGET

    best = (-1.0, 0.0)
    for onset, _ in regions:
        if onset + MIN_KEEP > total:
            break
        end = min(onset + TARGET, total)
        score = speech_in(regions, onset, end)
        # Prefer a window that ends in a pause: find the last speech region
        # ending inside it and cut just after, so it does not stop mid-word.
        if score > best[0]:
            best = (score, onset)
    start = best[1]
    end = min(start + TARGET, total)
    tail = [e for _, e in regions if start < e <= end]
    if tail and end - tail[-1] < 1.5:
        end = min(tail[-1] + 0.25, total)   # let the last word finish
    if end - start < MIN_KEEP:
        end = min(start + TARGET, total)
    return start, end


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", action="store_true")
    args = ap.parse_args()

    if args.restore:
        if not RAW.exists():
            print("nothing to restore -- voices/raw/ does not exist")
            return 1
        for src in sorted(RAW.glob("*.wav")):
            shutil.copy2(src, VOICES / src.name)
            print(f"  restored {src.name}")
        return 0

    RAW.mkdir(exist_ok=True)
    print(f"{'VOICE':<22}{'WAS':>8}{'WINDOW':>18}{'NOW':>8}   {'RATE'}")
    print("-" * 72)

    for wav in sorted(VOICES.glob("*.wav")):
        was = duration(wav)
        start, end = best_window(wav)
        kept = end - start
        print(f"{wav.name:<22}{was:>7.1f}s{f'{start:.1f}-{end:.1f}s':>18}"
              f"{kept:>7.1f}s   {RATE}")
        if not args.apply:
            continue

        preserved = RAW / wav.name
        if not preserved.exists():
            shutil.copy2(wav, preserved)

        tmp = wav.with_suffix(".trimmed.wav")
        out = run("ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                  "-ss", f"{start:.3f}", "-t", f"{kept:.3f}",
                  "-i", str(preserved),
                  "-ar", str(RATE), "-ac", "1", "-sample_fmt", "s16",
                  # Gentle level match so no voice conditions louder than
                  # another. Not compression -- just a common target.
                  "-af", "loudnorm=I=-19:TP=-2:LRA=11",
                  str(tmp))
        if not tmp.exists() or tmp.stat().st_size < 4096:
            print(f"    FAILED, left original in place: {out.strip()[:160]}")
            tmp.unlink(missing_ok=True)
            continue
        tmp.replace(wav)

    if not args.apply:
        print("\n  dry run. Re-run with --apply to write.")
    else:
        print(f"\n  done. Originals in {RAW}/ -- --restore puts them back.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
