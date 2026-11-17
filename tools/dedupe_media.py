#!/usr/bin/env python3
"""dedupe_media.py: set aside duplicate media files (2026-11-14, Claude, for Jason).

Jason, 11-14: "only remove actual duplicates ... keep all system and program files; you may remove
media that is duplicated". After the 11-12 cleanup broke things, this script NEVER deletes: extra
copies are MOVED to <drive>:\\_duplicates-hold-2026-11-14\\<original path> on the same drive (a rename,
instant, fully undoable). Jason empties the holding folders himself once he is happy.

    py -3 -I dedupe_media.py              dry run: finds duplicates, writes the plan CSV, moves nothing
    py -3 -I dedupe_media.py --apply      moves the extra copies, writes the log CSV and undo script

How a duplicate is decided: same size, then the full SHA-256 of both files must match. Name and size
alone never count. Hard links (two names for one file) are skipped: moving one frees nothing.

Which copy is kept:
  - copies inside a sync folder (SynologyDrive, OneDrive) are never moved (moving one would also
    delete it on the NAS / cloud side) and never count as "the kept copy": every group keeps at
    least one ordinary, non-synced copy. A group with only one non-synced copy is left alone.
  - among the non-synced copies, the first rule that separates them wins:
    1. the organized libraries, in this order: G:\\photos-library (sorted by year), M:\\Pictures,
       M:\\Music, M:\\Videos, the user Pictures/Music/Videos; backups (M:\\vault-backup) come last
    2. the oldest modified time, then the shortest path
  Tried first (2026-11-14 dry run): "sync copy wins, then oldest" kept the M:\\vault-backup copies and
  set aside the year-sorted G:\\photos-library ones, and would have left the NEF raws only inside
  SynologyDrive. Changed to the rules above before anything was moved.

Never scanned (whole subtree skipped): Windows, Program Files, ProgramData, AppData, Python installs,
recycle bin, System Volume Information, any code project (a folder holding .git, package.json,
Cargo.toml, pyproject.toml, Dockerfile, compose files), any folder holding .exe/.dll (app resources),
game libraries, D:\\Garden for all (the plant pipeline and gallery read their files by path),
D:\\_parked, D:\\vault-sealed, D:\\docker-backups, D:\\scratchpad, D:\\Robin-garden-template, the
holding folders themselves.
"""
import argparse, csv, hashlib, os, shutil, sys, time
from collections import defaultdict

HOLD = '_duplicates-hold-2026-11-14'
OUTDIR = r'D:\_duplicates-hold-2026-11-14'
ROOTS = ['D:\\', 'M:\\', 'G:\\', r'C:\Users\USER']
MEDIA = {'.jpg', '.jpeg', '.png', '.gif', '.heic', '.webp', '.nef', '.cr2', '.dng', '.raw', '.arw',
         '.tif', '.tiff', '.mp4', '.mov', '.mkv', '.avi', '.mp3', '.flac', '.wav', '.m4a', '.aac', '.ogg'}
MIN_BYTES = 10 * 1024  # icons and thumbnails under 10 KB are not worth the risk
SKIP_NAMES = {'windows', 'program files', 'program files (x86)', 'programdata', 'appdata', '$recycle.bin',
              'system volume information', '.git', 'node_modules', '.venv', 'venv', 'site-packages',
              '__pycache__', 'steamapps', 'steamlibrary', 'steam', 'epic games', 'gog galaxy', 'xboxgames',
              'riot games', 'ea games', 'ubisoft', 'battle.net', HOLD.lower(), '.synologyworkingdirectory'}
SKIP_PATHS = [p.lower() for p in [
    r'D:\Garden for all', r'D:\_parked', r'D:\vault-sealed', r'D:\docker-backups', r'D:\scratchpad',
    r'D:\Robin-garden-template', r'D:\secrets-ui-test',r'C:\Users\USER\Projects', r'C:\Users\USER\.claude', r'C:\Users\USER\.docker',
    r'C:\Users\USER\.ollama', r'C:\Users\USER\.lmstudio']]
CODE_MARKERS = {'.git', 'package.json', 'cargo.toml', 'pyproject.toml', 'dockerfile', 'docker-compose.yml',
                'compose.yml', 'compose.yaml', 'requirements.txt'}
SYNC = ('\\synologydrive', '\\onedrive')
LIBRARY = [p.lower() for p in [r'G:\photos-library', r'M:\Pictures', r'M:\Music', r'M:\Videos',
                               r'C:\Users\USER\Pictures', r'C:\Users\USER\Music', r'C:\Users\USER\Videos']]
BACKUP = [p.lower() for p in [r'M:\vault-backup']]


def walk_media():
    for root in ROOTS:
        for d, dirs, files in os.walk(root):
            low = d.lower()
            names = {f.lower() for f in files} | {x.lower() for x in dirs}
            if (any(low == s or low.startswith(s + '\\') for s in SKIP_PATHS)
                    or names & CODE_MARKERS
                    or any(f.lower().endswith(('.exe', '.dll')) for f in files)):
                dirs[:] = []
                continue
            dirs[:] = [x for x in dirs if x.lower() not in SKIP_NAMES]
            for f in files:
                if os.path.splitext(f)[1].lower() in MEDIA:
                    p = os.path.join(d, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if st.st_size >= MIN_BYTES:
                        yield p, st


def sha256(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for b in iter(lambda: f.read(4 << 20), b''):
            h.update(b)
    return h.hexdigest()


def keep_key(item):
    p, st = item
    low = p.lower()
    lib = next((i for i, l in enumerate(LIBRARY) if low.startswith(l + '\\')), len(LIBRARY))
    if any(low.startswith(b + '\\') for b in BACKUP):
        lib = len(LIBRARY) + 1
    return (lib, st.st_mtime, len(p), low)


def hold_path(p):
    drive, rest = os.path.splitdrive(p)
    return os.path.join(drive + '\\', HOLD, rest.lstrip('\\'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    t0 = time.time()
    by_size = defaultdict(list)
    n = 0
    for p, st in walk_media():
        by_size[st.st_size].append((p, st))
        n += 1
    print(f'scanned {n} media files in {time.time() - t0:.0f}s', flush=True)

    plan = []
    groups = 0
    for size, items in by_size.items():
        if len(items) < 2:
            continue
        # drop hard links: same volume serial + file id = one file with two names
        seen, uniq = set(), []
        for it in items:
            k = (it[1].st_dev, it[1].st_ino)
            if k not in seen:
                seen.add(k)
                uniq.append(it)
        if len(uniq) < 2:
            continue
        by_hash = defaultdict(list)
        for it in uniq:
            try:
                by_hash[sha256(it[0])].append(it)
            except OSError:
                pass
        for h, same in by_hash.items():
            if len(same) < 2:
                continue
            plain = sorted((it for it in same if not any(s in it[0].lower() for s in SYNC)), key=keep_key)
            if len(plain) < 2:
                continue
            keep = plain[0][0]
            movable = plain[1:]
            groups += 1
            for p, st in movable:
                plan.append({'kept': keep, 'moved_from': p, 'moved_to': hold_path(p), 'sha256': h, 'bytes': size})

    os.makedirs(OUTDIR, exist_ok=True)
    name = 'moved-log.csv' if a.apply else 'dry-run-plan.csv'
    total = sum(r['bytes'] for r in plan)
    print(f'{groups} duplicate groups, {len(plan)} extra copies, {total / 1e9:.2f} GB', flush=True)
    if a.apply:
        done = []
        for r in plan:
            try:
                # re-check right before moving: both files still exist and still match
                if not (os.path.exists(r['kept']) and os.path.exists(r['moved_from'])):
                    continue
                if os.path.getsize(r['moved_from']) != r['bytes'] or sha256(r['kept']) != r['sha256']:
                    continue
                os.makedirs(os.path.dirname(r['moved_to']), exist_ok=True)
                shutil.move(r['moved_from'], r['moved_to'])
                done.append(r)
            except OSError as e:
                print('FAIL', r['moved_from'], e.strerror)
        plan = done
        with open(os.path.join(OUTDIR, 'undo-duplicates.ps1'), 'w', encoding='utf-8-sig') as f:
            f.write('# undo-duplicates.ps1: puts every file dedupe_media.py set aside back where it was.\n'
                    '# Reads moved-log.csv next to this file. Skips a file if something already sits at its old path.\n'
                    '$log = Join-Path $PSScriptRoot "moved-log.csv"\n'
                    'foreach ($r in Import-Csv -LiteralPath $log) {\n'
                    '  if (-not (Test-Path -LiteralPath $r.moved_to)) { continue }\n'
                    '  if (Test-Path -LiteralPath $r.moved_from) { Write-Output "SKIP (taken) $($r.moved_from)"; continue }\n'
                    '  New-Item -ItemType Directory -Force (Split-Path -LiteralPath $r.moved_from) | Out-Null\n'
                    '  Move-Item -LiteralPath $r.moved_to -Destination $r.moved_from\n'
                    '}\n"undo finished"\n')
        print(f'moved {len(plan)} files, {sum(r["bytes"] for r in plan) / 1e9:.2f} GB held', flush=True)
    with open(os.path.join(OUTDIR, name), 'w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=['kept', 'moved_from', 'moved_to', 'sha256', 'bytes'])
        w.writeheader()
        w.writerows(plan)
    print('written', os.path.join(OUTDIR, name))


if __name__ == '__main__':
    main()
