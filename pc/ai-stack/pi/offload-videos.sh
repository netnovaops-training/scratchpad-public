#!/bin/bash
# Move finished downloads off the Pi and onto the NAS, and only then delete.
#
# JASON, 2026-09-24: "once the video is finished processing, it is then synced
# up to the Nas and then deleted once it's fully verified."
#
# THE ORDER IS THE WHOLE POINT: copy, verify, THEN delete. Every step below
# refuses to go forward on doubt, because the failure this guards against is
# not "the copy was slow" -- it is "the copy looked fine and the original is
# gone". A file that stays on the Pi costs disk. A file deleted after a bad
# copy costs the file.
#
# THERE IS NO USB DRIVE, which is not what the plan assumed. Checked
# 2026-09-24: the Pi has one filesystem, the 117 GB SD card, and MeTube's
# /mnt/downloads is a directory on it. So every download is SD-card wear on
# the machine whose OS lives there, and getting files off promptly is the
# point of this script rather than a tidiness nicety. If a USB drive is added
# later, mount it at /mnt/downloads and nothing here changes.
#
# WHAT COUNTS AS FINISHED. yt-dlp writes .part/.ytdl/.temp while working and
# renames on success, so those are skipped outright. On top of that a file
# must be unchanged for SETTLE seconds: a rename can land a moment before the
# last buffer is flushed, and a mux or a thumbnail write can follow it.
#
# VERIFIED MEANS THE BYTES MATCH, not that rsync exited 0. rsync reports
# success for a transfer the far end then truncates -- a full NAS, a dropped
# mount, an SMB share remounted read-only. The size is compared, then a real
# checksum of both copies. Only after both agree is the local file removed.
#
# SAFE TO RUN OVER ITSELF. A lock means a slow night cannot start a second
# copy of the same file alongside the first.
set -uo pipefail

SRC="${SRC:-/mnt/downloads}"
DEST="${DEST:-/mnt/nas-videos}"
SETTLE="${SETTLE:-180}"          # seconds a file must be unchanged
MINSIZE="${MINSIZE:-100000}"     # ignore anything under 100 KB
LOG="${LOG:-/var/log/offload-videos.log}"
LOCK=/tmp/offload-videos.lock

say() { printf '%s %s\n' "$(date '+%F %T')" "$*" | tee -a "$LOG" >&2; }

exec 9>"$LOCK"
if ! flock -n 9; then
  say "another run is still going; leaving it alone"
  exit 0
fi

[ -d "$SRC" ] || { say "FATAL: $SRC does not exist"; exit 1; }

# THE MOUNT IS CHECKED, NOT ASSUMED. If the NAS is not mounted, $DEST is an
# empty directory on the SD card -- rsync would happily "succeed" into it,
# the checksums would match, and this script would delete the originals
# having moved them a few centimetres. That is the worst outcome available,
# so it is the first thing tested.
if ! mountpoint -q "$DEST"; then
  say "FATAL: $DEST is not a mount point -- refusing to touch anything"
  exit 1
fi
if ! touch "$DEST/.offload-write-test" 2>/dev/null; then
  say "FATAL: $DEST is not writable -- refusing to touch anything"
  exit 1
fi
rm -f "$DEST/.offload-write-test"

moved=0 kept=0 failed=0 freed=0
now=$(date +%s)

# -print0/read -d '' so spaces and quotes in a YouTube title cannot split a
# filename into two. They will: titles contain everything.
while IFS= read -r -d '' f; do
  base="${f#"$SRC"/}"

  case "$f" in
    *.part|*.ytdl|*.temp|*.tmp|*.download|*.part-Frag*) continue ;;
  esac

  size=$(stat -c %s "$f" 2>/dev/null) || continue
  [ "$size" -ge "$MINSIZE" ] || continue

  mtime=$(stat -c %Y "$f" 2>/dev/null) || continue
  age=$(( now - mtime ))
  if [ "$age" -lt "$SETTLE" ]; then
    kept=$((kept+1))
    continue
  fi

  # An open file is a file still being written, whatever its timestamp says.
  if command -v fuser >/dev/null 2>&1 && fuser -s "$f" 2>/dev/null; then
    say "still open, skipping: $base"
    kept=$((kept+1))
    continue
  fi

  target="$DEST/$base"
  mkdir -p "$(dirname "$target")" || { failed=$((failed+1)); continue; }

  say "copying: $base ($(numfmt --to=iec "$size" 2>/dev/null || echo "$size B"))"
  # --inplace is deliberately NOT used: rsync's default temp-file-then-rename
  # means an interrupted copy never leaves a short file at the real name for
  # something else to treat as complete.
  if ! rsync -a --partial "$f" "$target" >>"$LOG" 2>&1; then
    say "rsync FAILED, keeping the original: $base"
    failed=$((failed+1))
    continue
  fi

  dsize=$(stat -c %s "$target" 2>/dev/null || echo -1)
  if [ "$dsize" != "$size" ]; then
    say "SIZE MISMATCH ($size vs $dsize), keeping the original: $base"
    failed=$((failed+1))
    continue
  fi

  # The real check. Reading both copies in full is the only thing that
  # actually proves the file arrived; everything above proves only that
  # nothing complained.
  a=$(sha256sum "$f" | cut -d' ' -f1)
  b=$(sha256sum "$target" | cut -d' ' -f1)
  if [ "$a" != "$b" ]; then
    say "CHECKSUM MISMATCH, keeping the original: $base"
    failed=$((failed+1))
    continue
  fi

  rm -f "$f"
  moved=$((moved+1))
  freed=$(( freed + size ))
  say "verified and removed locally: $base"

  # An empty directory left behind by a moved file, but never $SRC itself.
  d=$(dirname "$f")
  [ "$d" != "$SRC" ] && rmdir --ignore-fail-on-non-empty "$d" 2>/dev/null

done < <(find "$SRC" -type f -print0 2>/dev/null)

say "done: $moved moved, $kept not ready, $failed failed, \
$(numfmt --to=iec "$freed" 2>/dev/null || echo "$freed B") freed"
[ "$failed" -eq 0 ]
