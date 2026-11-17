"""The Pee-Wee story, exactly as the site tells it, as one MP4.

JASON, 2026-09-23: "I just wanted the exact story I had made into a mp4.
Nothing changed other than that" -- and then: "The pictures from famous
people don't need to be included."

FOUR ACTS. The insignia -- an atom supercharged into a supernova, whose
shockwave is a transmitter's rings -- is Jason's own mark and is silent;
see intro_fx.py. Then the PORTRAIT CARD, which is the site's own opening
reproduced rather than redesigned: his face in its gold ring warming from
black-and-white into colour, over "On the air", with the three lines of
credit arriving one every four and a half seconds. See portrait_act.py --
the film went straight to the map before, and the first thing the site
shows of him was missing from it entirely. Then the map act is the biography -- born in Schenectady, the door
that opened at WABY in 1955, Troy, the Ten Eyck Record Shop, Evergreen --
told over Albany with the camera flying between the four places, exactly as
the page's own intro does it. See mapfilm.py. Then the story: the Fridays,
and the records he gave away.

TWO THINGS THE FIRST VERSION GOT WRONG, both worth writing down.

ONE TELLING, NOT FIVE. story-1 through story-5 are five ALTERNATIVE
tellings of the same story -- index.html rotates them so a returning
listener hears a different one -- not five parts of a long one. The first
version concatenated all five and produced eight and a half minutes of the
same story told over and over. story-5 is the one that was rewritten with
the Boys & Girls Club and the Friday records in it, it is the only one with
an authored picture map, and it is the one on the phone line. It is THE
story; the rest are drafts of it.

THE PICTURES COME FROM THE STORY, NOT FROM A FOLDER. The first version
pulled `photos/`, which is the station wall -- the dial of broadcasters and
public figures -- and put Michelle Obama in the middle of a family history.
people/<slug>/story-images.json already says which photograph belongs to
which paragraph, with the reason written next to it. That map is the site's
own editorial work and this reads it rather than inventing a slideshow.

FAMILY PHOTOGRAPHS ONLY. The map names two kinds: photo-*.jpg, which are
the family's own, and woj-*.jpg, which are Office of War Information
photographs of a Wings Over Jordan broadcast in 1943 -- not this family, and
excluded here at Jason's word. That leaves paragraphs 2, 3, 9 and 10 with a
photograph and six without.

WHAT GOES ON SCREEN FOR THE OTHER SIX: his own words, set in the page's own
type, while they are being spoken. No stand-in photograph, no stock image,
nothing implying a picture exists where one does not. For an oral history
about a voice that is not a compromise -- it is the honest frame.

TIMING COMES FROM THE PARAGRAPH CLIPS. The narration is already cut into
story-5-p1 ... p10, so each picture is on screen for exactly as long as the
paragraph it belongs to. Nothing to measure and nothing to re-measure when
the story is rewritten.

NOTHING IS UPSCALED PAST 1.6x. photo-5 is 311x512. Blown up to fill a 1080p
frame it would be four times its own size and look it, so small photographs
sit smaller and stay sharp.

    python make_video.py                 # insignia, map act, then story-5
    python make_video.py --fast          # 720p, to check it quickly
    python make_video.py --no-map        # the story on its own
    python make_video.py --no-intro      # without either opening card
    python make_video.py --mark-first    # insignia before his portrait
    python make_video.py --telling 4     # a different telling, if one is wanted
"""
from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont

import intro_fx
import mapfilm
import portrait_act

sys.stdout.reconfigure(encoding="utf-8", newline="\n")

HERE = pathlib.Path(__file__).resolve().parent
SITE = HERE.parent / "web/public/peewee"
PERSON = "peewee-harris"
AUDIO = SITE / "people" / PERSON
OUT = HERE.parent / "peewee-video"
# CLIPS LIVE IN OUT, NOT IN A SUBDIRECTORY, and that is a workaround with
# a reason. Docker Desktop's file-sharing layer does not always show a
# directory created moments earlier: ffmpeg in the container reported
# "No such file or directory" for an output path that existed in Explorer
# and that the very next `docker run` could list. OUT itself is old and
# always visible, so a filename prefix does the same job with no race.
CLIP = "clip-"

# DROP A VIDEO IN HERE AND IT IS IN THE FILM. Filename order, straight
# after the opening cards. Made once so the folder exists to be found.
IMPORTS = HERE / "import"
IMPORT_EXT = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".mkv")

IMAGE = "jrottenberg/ffmpeg:6-alpine"
FPS = 30
MAX_UPSCALE = 1.6

# The family's own. Everything else the map names is archive material from
# somewhere else and is excluded -- see the note at the top.
OURS = "photo-"

PAPER = (247, 246, 243)
RACK = (28, 31, 36)


def run(args):
    r = subprocess.run(args, capture_output=True)
    return r.returncode, (r.stdout or b"").decode("utf-8", "replace"), \
        (r.stderr or b"").decode("utf-8", "replace")


def ff(args, tool="ffmpeg", mount_imports=False):
    mounts = ["-v", f"{SITE}:/in:ro", "-v", f"{OUT}:/out"]
    if mount_imports:
        mounts += ["-v", f"{IMPORTS}:/imports:ro"]
    return run(["docker", "run", "--rm"] + mounts
               + ["--entrypoint", tool, IMAGE] + args)


def duration(rel):
    code, out, err = ff(["-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", f"/in/{rel}"], tool="ffprobe")
    try:
        return float(out.strip())
    except ValueError:
        sys.exit(f"could not measure {rel}: {err.strip()[:200]}")


def duration_of(path):
    """Seconds of a file in OUT, for the length check at the end."""
    code, out, err = ff(["-v", "error", "-show_entries", "format=duration",
                         "-of", "csv=p=0", f"/out/{path.name}"], tool="ffprobe")
    try:
        return float(out.strip())
    except ValueError:
        return 0.0


def face(px, bold=False):
    for name in (("arialbd.ttf", "seguisb.ttf") if bold
                 else ("arial.ttf", "segoeui.ttf")):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def words_card(path, w, h, text):
    """A paragraph of the story, set to be read while it is spoken.

    Sized to the text rather than the other way round: a 24-word paragraph
    and a 70-word one cannot share a point size without one of them either
    overflowing the frame or floating in the middle of an empty one.
    """
    im = Image.new("RGB", (w, h), RACK)
    d = ImageDraw.Draw(im)
    body = " ".join(text.split())

    for pt in (0.052, 0.046, 0.040, 0.034, 0.029, 0.025, 0.021):
        f = face(int(h * pt))
        wrapped = textwrap.wrap(body, width=max(20, int(52 * 0.040 / pt)))
        line_h = int(h * pt * 1.42)
        if len(wrapped) * line_h <= h * 0.74 and len(wrapped) <= 12:
            break

    y = (h - len(wrapped) * line_h) // 2
    for line in wrapped:
        tw = d.textbbox((0, 0), line, font=f)[2]
        d.text(((w - tw) // 2, y), line, font=f, fill=PAPER)
        y += line_h
    im.save(path, "PNG")


def photo_clip(src, index, secs, w, h, zoom_in):
    """One family photograph: blurred fill behind, the print in front."""
    frames = max(2, int(round(secs * FPS)))
    with Image.open(AUDIO / src) as im:
        iw, ih = im.size

    # Fit the frame, but never magnify past MAX_UPSCALE -- a 311x512 print
    # made to fill 1080 lines is four times its own size and looks it.
    fit = min(int(w * 0.90) / iw, int(h * 0.88) / ih)
    scale = min(fit, MAX_UPSCALE)
    fw, fh = (int(iw * scale) // 2) * 2, (int(ih * scale) // 2) * 2

    z = (f"min(1+0.035*on/{frames},1.035)" if zoom_in
         else f"max(1.035-0.035*on/{frames},1.0)")
    fade_out = max(0.0, secs - 0.5)
    graph = (
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,"
        f"crop={w}:{h},boxblur=26:2,eq=brightness=-0.16:saturation=0.7[bg];"
        f"[0:v]scale={fw}:{fh}:flags=lanczos,setsar=1,"
        f"zoompan=z='{z}':d={frames}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        f":s={fw}x{fh}:fps={FPS},"
        f"pad={fw+8}:{fh+8}:4:4:color=0xF7F6F3[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,"
        f"fade=t=in:st=0:d=0.4,fade=t=out:st={fade_out:.2f}:d=0.5,"
        f"format=yuv420p[v]"
    )
    code, _, err = ff([
        "-y", "-loop", "1", "-i", f"/in/people/{PERSON}/{src}",
        "-filter_complex", graph, "-map", "[v]",
        "-t", f"{secs:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-video_track_timescale", "30000",
        f"/out/{CLIP}{index:03d}.mp4"])
    if code != 0:
        sys.exit(f"paragraph {index} ({src}) failed:\n{err[-1200:]}")


def card_clip(png, index, secs, w, h):
    fade_out = max(0.0, secs - 0.6)
    graph = (f"[0:v]scale={w}:{h},fade=t=in:st=0:d=0.6,"
             f"fade=t=out:st={fade_out:.2f}:d=0.6,format=yuv420p[v]")
    code, _, err = ff([
        "-y", "-loop", "1", "-i", f"/out/{png}",
        "-filter_complex", graph, "-map", "[v]",
        "-t", f"{secs:.3f}", "-r", str(FPS),
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-video_track_timescale", "30000",
        f"/out/{CLIP}{index:03d}.mp4"])
    if code != 0:
        sys.exit(f"card {png} failed:\n{err[-1200:]}")


def pipe_encoder(w, h, out_name):
    """An ffmpeg that takes raw frames on stdin, with its stderr ON DISK.

    NOT stderr=PIPE, and this cost a full render to learn. ffmpeg is
    chatty, a pipe buffer is 64KB, and nothing read that pipe until every
    frame had been written -- so it filled, ffmpeg blocked writing to it,
    stopped reading stdin, and the writer died on a broken pipe several
    thousand frames in. The traceback pointed at the write and said
    nothing about the cause. A file cannot fill.
    """
    log = open(OUT / "encode.log", "wb")
    proc = subprocess.Popen(
        ["docker", "run", "--rm", "-i", "-v", f"{OUT}:/out",
         "--entrypoint", "ffmpeg", IMAGE,
         "-y", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{w}x{h}",
         "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", "19",
         "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-video_track_timescale", "30000", out_name],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
    return proc, log


def finish_encoder(proc, log, what):
    proc.stdin.close()
    code = proc.wait()
    log.close()
    if code != 0:
        tail = (OUT / "encode.log").read_bytes()[-1500:].decode("utf-8", "replace")
        sys.exit(what + " failed:" + chr(10) + tail)
    (OUT / "encode.log").unlink(missing_ok=True)


def has_audio(path_in_container, mount_imports=False):
    code, out, _ = ff(["-v", "error", "-select_streams", "a",
                       "-show_entries", "stream=index", "-of", "csv=p=0",
                       path_in_container], tool="ffprobe",
                      mount_imports=mount_imports)
    return bool(out.strip())


def normalise_import(src, index, w, h):
    """One of Jason's own clips, made to match everything else.

    Jason, 2026-09-23: "Can I import the 5 second clips". Anything dropped
    in peewee-tools/import/ lands in the film, in filename order, straight
    after the opening cards.

    SCALED TO FIT AND PILLARBOXED, never cropped. A phone clip is usually
    vertical, and cropping it to 16:9 throws most of the picture away --
    the same reason the family photographs are not cropped either.

    RE-ENCODED TO THE EXACT PARAMETERS OF EVERY OTHER SEGMENT: same size,
    fps, profile, pixel format and timebase. That is not tidiness, it is
    the condition for joining the film with a stream copy rather than a
    second full encode.

    VIDEO ONLY HERE. Its sound is pulled out separately in main(), so the
    audio timeline stays one list built in one place -- the two times this
    film went out of sync, it was because picture and sound were assembled
    from lists that disagreed.
    """
    graph = (f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,"
             f"setsar=1,fps={FPS},format=yuv420p[v]")
    code, _, err = ff([
        "-y", "-i", f"/imports/{src.name}",
        "-filter_complex", graph, "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "19",
        "-profile:v", "high", "-pix_fmt", "yuv420p",
        "-video_track_timescale", "30000",
        f"/out/{CLIP}{index:03d}.mp4"], mount_imports=True)
    if code != 0:
        sys.exit("could not import " + src.name + ": " + err[-1200:])
    return duration_of(OUT / f"{CLIP}{index:03d}.mp4")


def build_audio(segments, out_name="audio.m4a"):
    """One soundtrack, from a list of (kind, value, seconds).

    kind is "silence" for the opening cards, "file" for a narration clip,
    or "import" for one of Jason's own. Built with ffmpeg's concat FILTER
    rather than the demuxer, because these are genuinely different things
    -- generated silence, mp3 narration, whatever codec came off a phone --
    and the demuxer needs them all identical.

    THE WHOLE POINT IS THAT THERE IS ONE LIST. Picture is assembled from
    the clip files in order; this is assembled from the same order, in the
    same loop in main(). When they were two lists that had to agree, they
    twice did not, and nothing noticed until somebody watched it.
    """
    inputs, filters, labels = [], [], []
    for i, (kind, value, secs) in enumerate(segments):
        if kind == "silence":
            inputs += ["-f", "lavfi", "-t", f"{secs:.3f}",
                       "-i", "anullsrc=r=48000:cl=stereo"]
        elif kind == "import":
            inputs += ["-i", f"/imports/{value}"]
        else:
            inputs += ["-i", f"/in/people/{PERSON}/{value}"]
        # Everything to one shape before the concat, or the filter refuses.
        filters.append(f"[{i}:a]aresample=48000,aformat="
                       f"sample_fmts=fltp:channel_layouts=stereo,"
                       f"atrim=0:{secs:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[a{i}]")
    graph = (";".join(filters) + ";" + "".join(labels)
             + f"concat=n={len(segments)}:v=0:a=1[out]")
    code, _, err = ff(["-y"] + inputs + [
        "-filter_complex", graph, "-map", "[out]",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        f"/out/{out_name}"], mount_imports=True)
    if code != 0:
        sys.exit("building the soundtrack failed: " + err[-1500:])
    return duration_of(OUT / out_name)


def render_portrait(w, h, index):
    """The page's own opening card: his face and the three credit lines.

    Jason, 2026-09-23: "there's no picture like it is on his page ... I want
    it exactly as is on this page." The site shows this before the map and
    the film did not have it at all. Silent, like the insignia.
    """
    act = portrait_act.PortraitAct(SITE, w, h)
    proc, log = pipe_encoder(w, h, f"/out/{CLIP}{index:03d}.mp4")
    for i in range(int(round(act.total * FPS))):
        proc.stdin.write(act.frame(i / FPS).convert("RGB").tobytes())
    finish_encoder(proc, log, "the portrait card")
    print(f"  portrait card: {act.total:.0f}s, silent")
    return act.total


def render_insignia(w, h, index):
    """The cold open: the atom, the supernova, the wave. Jason's mark.

    SILENT, AND FIRST. It carries no narration, so the whole soundtrack is
    delayed by exactly its length at the final mux. That delay is the one
    number that can put this film out of sync, which is why the length
    check at the end of main() exists.
    """
    intro = intro_fx.Intro(w, h)
    proc, log = pipe_encoder(w, h, f"/out/{CLIP}{index:03d}.mp4")
    n = int(round(intro.total * FPS))
    for i in range(n):
        proc.stdin.write(intro.frame(i / FPS).convert("RGB").tobytes())
    finish_encoder(proc, log, "the insignia")
    print(f"  insignia: {intro.total:.0f}s, silent")
    return intro.total


def render_journey(w, h, index):
    """The map act: the camera flies where the biography goes.

    FRAMES GO STRAIGHT DOWN A PIPE INTO ffmpeg, never to disk. 7,300 PNGs
    at 1080p is twenty-odd gigabytes of temporary files to write and read
    back for a four-minute clip; raw RGB on stdin costs nothing and is
    faster than either end of that.

    EACH LEG IS A FLIGHT THEN A HOLD, cut to its own narration. The flight
    is a fixed three seconds however far it travels -- Albany to Troy is
    six miles and Troy to Colonie is further, and a camera whose speed
    depends on distance reads as hurried on one leg and sleepy on the next.
    What is left of the paragraph is the hold, with a slow push in so the
    frame is never quite still.

    The card fades in ON ARRIVAL, not on departure: naming a place while
    the camera is still crossing the county toward it tells the viewer the
    answer before the shot has asked the question.
    """
    film = mapfilm.MapFilm(SITE, w, h)
    segs = json.loads((SITE / "data/journey.json").read_text(
        encoding="utf-8"))["segments"]

    legs = []
    for seg in segs:
        name = pathlib.Path(seg["audio"]).name
        if not (AUDIO / name).is_file():
            continue
        place = seg.get("place") or ""
        if place in ("None", "null"):
            place = ""
        legs.append((name, place if place in film.places else ""))
    if not legs:
        return None, []

    secs = [duration(f"people/{PERSON}/{n}") for n, _ in legs]
    print(f"  map act: {len(legs)} legs, {sum(secs)/60:.1f} minutes")

    proc, log = pipe_encoder(w, h, f"/out/{CLIP}{index:03d}.mp4")

    FLIGHT = 3.0
    cam = mapfilm.cam_for(film, None)
    lit = set()
    written = 0
    for (name, place), length in zip(legs, secs):
        target = mapfilm.cam_for(film, place or None)
        frames = max(1, int(round(length * FPS)))
        fly = min(int(FLIGHT * FPS), frames // 2) if place else 0
        start = cam
        if place:
            lit.add(place)
        card = film.places.get(place) if place else None

        for i in range(frames):
            if fly and i < fly:
                c = mapfilm.interp(start, target, i / fly)
                alpha = 0.0
            else:
                # The hold: a slow push in, about 4% over the paragraph, so
                # the frame breathes without anybody noticing a move.
                t = (i - fly) / max(1, frames - fly)
                c = (target[0], target[1], target[2] * (1 - 0.04 * t))
                alpha = min(1.0, (i - fly) / (0.8 * FPS))

            # The very first and very last seconds of the whole act fade
            # from and to black; everything between is continuous.
            fade = 1.0
            if written < FPS:
                fade = written / FPS
            proc.stdin.write(film.frame(c, lit, card=card,
                                        card_alpha=alpha, fade=fade)
                             .convert("RGB").tobytes())
            written += 1
        cam = (target[0], target[1], target[2] * 0.96)
        print(f"    {name} -> {place or 'the whole corridor'}", flush=True)

    finish_encoder(proc, log, "the map act")
    return [n for n, _ in legs], secs


def main():
    fast = "--fast" in sys.argv
    w, h = (1280, 720) if fast else (1920, 1080)
    telling = "story-5"
    if "--telling" in sys.argv:
        telling = "story-" + sys.argv[sys.argv.index("--telling") + 1]

    paras = [p for p in AUDIO.glob(f"{telling}-p*.mp3") if "jason" not in p.name]
    paras.sort(key=lambda p: int(p.stem.split("-p")[1]))
    if not paras:
        sys.exit(f"no paragraph clips for {telling} in {AUDIO}")

    try:
        mapping = json.loads((AUDIO / "story-images.json")
                             .read_text(encoding="utf-8")).get(telling, {})
    except FileNotFoundError:
        mapping = {}

    text = [p.strip() for p in
            (AUDIO / f"{telling}.txt").read_text(encoding="utf-8").split("\n\n")
            if p.strip()]

    for old_clip in OUT.glob(f"{CLIP}*.mp4"):
        old_clip.unlink()

    secs = [duration(f"people/{PERSON}/{p.name}") for p in paras]
    total = sum(secs)
    ours = sum(1 for i in range(len(paras))
               if (mapping.get(str(i + 1), {}).get("image", "")).startswith(OURS))
    print(f"  {telling}: {len(paras)} paragraphs, {total/60:.1f} minutes")
    print(f"  {ours} with a family photograph, {len(paras)-ours} set as words")

    # THE MAP ACT COMES FIRST. Jason, 2026-09-23: "I wanted to have a the
    # map going with the project." It is the biography -- born in
    # Schenectady, the door that opened at WABY, Troy, the record shop,
    # Evergreen -- and the story of the Fridays is the second half.
    # HIS CARD FIRST, THEN THE MARK. Jason, 2026-09-23: "Put them before."
    # So the film opens on Pee-Wee -- his face, his name, his three lines --
    # and the insignia follows it, the way a distributor's mark sits after a
    # cold open rather than in front of one. Pass --mark-first to swap them
    # back; the silent prefix is the same length either way, so nothing
    # downstream changes.
    # ONE LIST FOR PICTURE AND SOUND. Every segment appends to `track` as
    # it is rendered, so the soundtrack is built from the same order, in
    # the same loop, as the clips. This film has gone out of sync twice,
    # both times because picture and sound came from two lists that were
    # meant to agree and quietly did not.
    track = []
    n = 0
    if "--no-intro" not in sys.argv:
        order = ((render_insignia, render_portrait) if "--mark-first" in sys.argv
                 else (render_portrait, render_insignia))
        for fn in order:
            track.append(("silence", None, fn(w, h, n)))
            n += 1

    # Jason's own clips, straight after the opening cards.
    IMPORTS.mkdir(exist_ok=True)
    mine = sorted(f for f in IMPORTS.iterdir()
                  if f.is_file() and f.suffix.lower() in IMPORT_EXT)
    if mine:
        print(f"  importing {len(mine)} clip(s) from {IMPORTS.name}/")
    for f in mine:
        secs = normalise_import(f, n, w, h)
        kind = "import" if has_audio(f"/imports/{f.name}", True) else "silence"
        track.append((kind, f.name, secs))
        print(f"    {f.name}  {secs:.1f}s"
              + ("" if kind == "import" else "  (no sound of its own)"))
        n += 1

    map_secs = []
    if "--no-map" not in sys.argv:
        names, map_secs = render_journey(w, h, n)
        n += 1
        track += [("file", nm, sec) for nm, sec in zip(names or [], map_secs)]

    OFFSET = n
    photo_n = 0
    for i, clip_secs in enumerate(secs):
        img = mapping.get(str(i + 1), {}).get("image", "")
        if img.startswith(OURS) and (AUDIO / img).is_file():
            photo_n += 1
            print(f"  [{i+1:2}/{len(paras)}] {img}", flush=True)
            photo_clip(img, i + OFFSET, clip_secs, w, h, zoom_in=(photo_n % 2 == 1))
        else:
            # No photograph of this family for this beat, so nothing is put
            # in its place. The words are what there is.
            print(f"  [{i+1:2}/{len(paras)}] words", flush=True)
            words_card(OUT / f"w{i:03d}.png", w, h,
                       text[i] if i < len(text) else "")
            card_clip(f"w{i:03d}.png", i + OFFSET, clip_secs, w, h)

    # --- join ------------------------------------------------------------
    (OUT / "clips.txt").write_text(
        "".join(f"file '{p.name}'\n"
                for p in sorted(OUT.glob(f"{CLIP}*.mp4"))),
        encoding="utf-8", newline="\n")
    code, _, err = ff(["-y", "-f", "concat", "-safe", "0",
                       "-i", "/out/clips.txt", "-c", "copy",
                       "/out/video-only.mp4"])
    if code != 0:
        sys.exit(f"joining failed:\n{err[-1500:]}")

    track += [("file", p.name, sec) for p, sec in zip(paras, secs)]
    build_audio(track)

    final = OUT / (f"peewee-{telling}-720p.mp4" if fast
                   else f"peewee-{telling}.mp4")
    # NO DELAY ANY MORE. The soundtrack already contains the silence for
    # the opening cards, in the right place, because it was built from the
    # same list as the picture. There is no offset left to get wrong.
    code, _, err = ff([
        "-y", "-i", "/out/video-only.mp4", "-i", "/out/audio.m4a",
        "-map", "0:v", "-map", "1:a",
        "-c:v", "copy", "-c:a", "copy",
        "-movflags", "+faststart", f"/out/{final.name}"])
    if code != 0:
        sys.exit(f"the final mux failed:\n{err[-1500:]}")

    for junk in (OUT / "video-only.mp4", OUT / "audio.m4a"):
        junk.unlink(missing_ok=True)
    for png in OUT.glob("w*.png"):
        png.unlink()
    for clip in OUT.glob(f"{CLIP}*.mp4"):
        clip.unlink()
    (OUT / "clips.txt").unlink(missing_ok=True)

    mb = final.stat().st_size / 1048576
    whole = sum(sec for _, _, sec in track)
    print(f"\n  {final}")
    print(f"  {mb:.1f} MB, {whole/60:.1f} minutes, {w}x{h} H.264 + AAC")
    if map_secs:
        print(f"  the map act runs {sum(map_secs)/60:.1f} min, "
              f"then the story {total/60:.1f} min")

    # A LENGTH CHECK, BECAUSE THE WAY THIS BREAKS IS SILENT. Picture and
    # sound are built from separate lists that must describe the same film;
    # when they drifted apart the file still played, still looked right for
    # the first three minutes, and simply went quiet. Nothing errored.
    vlen = duration_of(final)
    alen = sum(sec for _, _, sec in track)
    if abs(vlen - alen) > 1.5:
        print(f"  WARNING: {vlen:.0f}s of picture against {alen:.0f}s of "
              f"sound -- they should match. Something is not in both lists.")

    (OUT / "youtube.json").write_text(json.dumps({
        "title": "Pee-Wee Harris — The Fridays",
        "description": (
            "Edward “Pee-Wee” Harris: twenty-two years on WABY, the "
            "Ten Eyck Record Shop, and the records he gave away free to young "
            "people on Fridays.\n\n"
            "Told by his family. Family photographs restored by Jason Gibson."
            "\n\nhttps://peewee.home.example.net"),
        "tags": ["Pee-Wee Harris", "WABY", "Albany", "radio history",
                 "oral history", "Capital Region", "R&B"],
        "category": "People & Blogs",
    }, indent=2) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
