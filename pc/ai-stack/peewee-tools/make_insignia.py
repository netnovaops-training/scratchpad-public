"""Render the insignia on its own, for any project.

JASON, 2026-09-23: "this will be my insignia going for the two feature
projects like this that have plans for" -- and then: "allow me to add to
other projects".

So this is the mark as a standalone deliverable rather than something only
the Pee-Wee film can produce. It writes one MP4 that can be dropped in
front of anything: a cut in an editor, a YouTube upload, a client reel.

    python make_insignia.py
    python make_insignia.py --name "SUPERNOVA AV" --sub "Albany, New York"
    python make_insignia.py --seconds 5 --out reel-open.mp4
    python make_insignia.py --palette ink --size 3840x2160
    python make_insignia.py --still poster.png

THE PALETTES ARE NAMED, NOT TYPED IN. A mark that appears on two projects
in two slightly different ambers is not a mark, it is a coincidence. Each
named palette below is a whole set -- ground, structure, light, highlight
-- chosen together, so picking one cannot produce a combination nobody
looked at.

IT STAYS THE SAME SHAPE AT EVERY LENGTH. The four beats are fractions of
the whole, so a five-second cut and a twelve-second cut have the same
rhythm and the flash lands in the same place. Shortening it does not mean
cutting the end off.

NO AUDIO, ON PURPOSE. A mark with its own sting fights whatever it is
sitting in front of. Add one in the edit if a project wants it.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import intro_fx

sys.stdout.reconfigure(encoding="utf-8", newline="\n")

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "peewee-video"
IMAGE = "jrottenberg/ffmpeg:6-alpine"
FPS = 30

# (ground, structure, light, highlight). Whole sets, chosen together.
PALETTES = {
    # The Pee-Wee film: bakelite and brass, a radio set in a front room.
    "bakelite": ((26, 18, 13), (156, 122, 60), (232, 163, 61), (243, 233, 215)),
    # The portfolio's rack palette -- for anything about the infrastructure.
    "rack":     ((15, 17, 20), (63, 68, 74), (53, 208, 127), (236, 234, 229)),
    # Ink and gold: for something formal that is not about equipment.
    "ink":      ((12, 14, 22), (70, 78, 104), (214, 178, 96), (240, 238, 232)),
    # The house on paper -- a light ground, for print and for a deck.
    "paper":    ((247, 246, 243), (198, 192, 182), (196, 68, 46), (28, 31, 36)),
    # Cut The Fluff. Taken from the app's own dark theme rather than
    # invented next to it, so the mark and the screen it opens are the
    # same violet: its ground, its raised surface, its accent, its ink.
    # Jason, 2026-09-26: "can we use the At and Nova explosion that we had
    # for Peewee. I want to have that for the intro for cut the fluff".
    # Structure is lifted well clear of the ground on purpose: the first
    # try used the app's own container violet (62,50,96) and the shells
    # and rings all but vanished. Bakelite's brass sits about six times
    # the luminance of its ground; this matches that relationship.
    "fluff":    ((20, 16, 24), (124, 104, 168), (201, 184, 236), (244, 241, 250)),
}


def arg(flag, default=None):
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


def main():
    name = arg("--name", "SITE OWNER")
    sub = arg("--sub", "")
    seconds = float(arg("--seconds", "8"))
    pal_name = arg("--palette", "bakelite")
    size = arg("--size", "1920x1080")
    out_name = arg("--out", "insignia.mp4")
    still = arg("--still")

    if pal_name not in PALETTES:
        sys.exit(f"palette must be one of: {', '.join(PALETTES)}")
    try:
        w, h = (int(v) for v in size.lower().split("x"))
    except ValueError:
        sys.exit("--size looks like 1920x1080")
    if w % 2 or h % 2:
        sys.exit("width and height must both be even -- H.264 requires it")

    OUT.mkdir(parents=True, exist_ok=True)
    intro = intro_fx.Intro(w, h, total=seconds,
                           wordmark=(name, sub) if name else None,
                           palette=PALETTES[pal_name])

    # --- a single frame, for a poster or a favicon ------------------------
    if still:
        # 88% of the way through: the debris has thinned, the rings are
        # wide, and the name is fully up. The one frame that reads as the
        # whole mark rather than as a moment in it.
        path = OUT / still
        intro.frame(seconds * 0.88).save(path)
        print(f"  {path}  ({w}x{h})")
        return

    print(f"  {name or 'no wordmark'} · {pal_name} · {seconds:.0f}s · {w}x{h}")
    log = open(OUT / "insignia.log", "wb")
    proc = subprocess.Popen(
        ["docker", "run", "--rm", "-i", "-v", f"{OUT}:/out",
         "--entrypoint", "ffmpeg", IMAGE,
         "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-preset", "slow", "-crf", "18",
         "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", f"/out/{out_name}"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)

    frames = int(round(seconds * FPS))
    for i in range(frames):
        proc.stdin.write(intro.frame(i / FPS).convert("RGB").tobytes())
    proc.stdin.close()
    code = proc.wait()
    log.close()
    if code != 0:
        tail = (OUT / "insignia.log").read_bytes()[-1200:].decode("utf-8", "replace")
        sys.exit("ffmpeg failed:\n" + tail)
    (OUT / "insignia.log").unlink(missing_ok=True)

    path = OUT / out_name
    print(f"  {path}")
    print(f"  {path.stat().st_size/1048576:.1f} MB, silent, "
          f"drop it in front of anything")


if __name__ == "__main__":
    main()
