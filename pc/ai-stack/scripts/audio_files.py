#!/usr/bin/env python3
"""audio_files.py: keep every website audio file in web/public/Audio-Files, with links at the old
paths so page URLs, sign-in rules and the containers keep working. No second copy of any file.

Written 2026-11-12 (Claude) for step 2 of Jason's cleanup ("any audio ... for my entire website
will be now in Audio-Files", "a link that makes a shortcut without duplicating the files",
"use symbolic links for linux", "test in both before it goes live"). Runs on BOTH machines:

    python3 audio_files.py <web/public> plan    # what would move (read-only)
    python3 audio_files.py <web/public> apply   # move + link; safe to re-run (picks up new clips)
    python3 audio_files.py <web/public> check   # every page/JSON audio reference still resolves

    NUC (Linux, live site):   ~/nuc-homelab/web/public
    PC  (Windows, studio copy): C:\\Jasons New Site\\www\\ai-stack\\web\\public

How the old paths keep working:
  Linux    audio-only folders -> relative directory symlink (new clips land in Audio-Files);
           mixed folders (peewee/people/<name>, g/<id>) -> relative per-file symlinks.
           Relative, so they resolve inside the Caddy container too (/srv/public mount).
  Windows  audio-only folders -> directory junction for host tools (peewee-tools), PLUS a bind
           mount of the Audio-Files folder over the old path in docker-compose (caddy, studio,
           hermes, hermes-http), because a junction does not resolve inside a Linux container.
           Mixed folders -> NTFS hard links (same data, two names; no admin rights needed).
  Re-run `apply` after the studio records new clips into people/<name>: they are moved and
  linked like the rest. URLs never change, so no page, JSON or Caddy edit is needed.
"""
import json
import os
import re
import shutil
import subprocess
import sys

AUDIO = re.compile(r"(?i)\.(mp3|m4a|wav|ogg|opus|flac|aac)$")
AUDIO_DIRS = ["library/audio", "peewee/music", "peewee/static-sounds", "peewee/audio",
              "peewee/data/patter", "peewee/data/intros"]
MIXED_PARENTS = ["peewee/people", "g"]  # each child folder holds audio next to pages/images
WIN = os.name == "nt"


def p(root, rel):
    return os.path.join(root, *rel.split("/"))


def is_junction(path):
    try:
        return bool(os.lstat(path).st_file_attributes & 0x400) if WIN else False
    except (OSError, AttributeError):
        return False


def linked(path):
    return os.path.islink(path) or is_junction(path)


def mixed_files(root):
    for parent in MIXED_PARENTS:
        base = p(root, parent)
        if not os.path.isdir(base):
            continue
        for child in sorted(os.listdir(base)):
            d = os.path.join(base, child)
            if not os.path.isdir(d) or linked(d):
                continue
            for dp, _dns, fns in os.walk(d):
                for f in sorted(fns):
                    if AUDIO.search(f):
                        yield os.path.relpath(os.path.join(dp, f), root).replace(os.sep, "/")


def same_file(a, b):
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def todo(root):
    """(kind, rel) still to do. kind: dir | file."""
    out = []
    for rel in AUDIO_DIRS:
        src = p(root, rel)
        if os.path.isdir(src) and not linked(src):
            out.append(("dir", rel))
    dest = p(root, "Audio-Files")
    for rel in mixed_files(root):
        src, dst = p(root, rel), p(dest, rel)
        if os.path.islink(src) or (WIN and os.path.exists(dst) and same_file(src, dst)):
            continue
        out.append(("file", rel))
    return out


def link_dir(src, dst):
    if WIN:
        r = subprocess.run(["cmd", "/c", "mklink", "/J", src, dst], capture_output=True, text=True)
        return r.returncode == 0
    os.symlink(os.path.relpath(dst, os.path.dirname(src)), src, target_is_directory=True)
    return True


def link_file(src, dst):
    if WIN:
        os.link(dst, src)  # hard link: no admin needed, Docker bind mounts see a normal file
    else:
        os.symlink(os.path.relpath(dst, os.path.dirname(src)), src)


def apply(root):
    dest = p(root, "Audio-Files")
    ok = fail = 0
    for kind, rel in todo(root):
        src, dst = p(root, rel), p(dest, rel)
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if kind == "dir":
                if os.path.exists(dst):  # re-run: merge files that appeared at the old path
                    for f in os.listdir(src):
                        shutil.move(os.path.join(src, f), os.path.join(dst, f))
                    os.rmdir(src)
                else:
                    shutil.move(src, dst)
                if not link_dir(src, dst):
                    raise OSError("link failed")
            else:
                if os.path.exists(dst):
                    raise OSError("target exists")
                shutil.move(src, dst)
                link_file(src, dst)
            ok += 1
        except OSError as e:
            fail += 1
            print("FAIL", kind, ascii(rel), e)
    print("APPLY %d linked, %d failed" % (ok, fail))
    return 1 if fail else 0


def references(root):
    """Audio paths the pages and JSON point at, as filesystem paths under root."""
    refs = set()
    pat = re.compile(r"""["'(]([^"'()<>\s]+\.(?:mp3|m4a|wav|ogg|opus))["')]""", re.I)

    def scan(page_dir, text, prefixes=("",)):
        for m in pat.finditer(text):
            u = m.group(1)
            if u.startswith(("http:", "https:", "data:", "//")):
                continue
            for pre in prefixes:
                path = os.path.normpath(os.path.join(root, u.lstrip("/")) if u.startswith("/")
                                        else os.path.join(page_dir, pre, u))
                refs.add((path, pre or "", u))
    for dp, _dns, fns in os.walk(root):
        if os.path.relpath(dp, root).replace(os.sep, "/").split("/")[0] in ("Audio-Files", "code"):
            continue
        for f in fns:
            if not f.endswith((".html", ".json")):
                continue
            fp = os.path.join(dp, f)
            try:
                text = open(fp, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            if rel.startswith("peewee/data/"):
                # Peewee JSON paths are relative to the page (peewee/); bare file names are
                # played from music/ or static-sounds/ by the page code.
                for m in pat.finditer(text):
                    u = m.group(1)
                    if "/" in u:
                        refs.add((os.path.normpath(p(root, "peewee/" + u)), "", u))
                    else:
                        cands = [p(root, "peewee/music/" + u), p(root, "peewee/static-sounds/" + u)]
                        refs.add((next((c for c in cands if os.path.exists(c)), cands[0]), "", u))
            else:
                scan(dp, text)
    return refs


def check(root):
    refs = references(root)
    bad = [r for r in refs if not os.path.isfile(r[0])]
    dest = p(root, "Audio-Files")
    n_af = sum(len([f for f in fns if AUDIO.search(f)]) for _d, _n, fns in os.walk(dest)) if os.path.isdir(dest) else 0
    left = [r for k, r in todo(root)]
    print("CHECK references %d, unresolved %d, files in Audio-Files %d, not yet moved %d"
          % (len(refs), len(bad), n_af, len(left)))
    for path, _pre, u in sorted(bad)[:15]:
        print("  unresolved:", ascii(u))
    return 1 if bad else 0


def plan(root):
    t = todo(root)
    print("PLAN dirs %d, files %d" % (sum(1 for k, _ in t if k == "dir"), sum(1 for k, _ in t if k == "file")))
    for k, r in t:
        if k == "dir":
            print("  dir", r)
    return 0


if __name__ == "__main__":
    root, mode = os.path.abspath(sys.argv[1]), sys.argv[2]
    sys.exit({"plan": plan, "apply": apply, "check": check}[mode](root))
