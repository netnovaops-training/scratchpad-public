#!/usr/bin/env bash
# Pull Google Drive down to this box, on a timer.
#
# Jason, 2026-09-06: "I also have a Google docs and drive that I want to sync
# with my external drive and Nas and my PDF reader that will allow gdocs and
# pdfs and spreadsheets."
#
# ONE WAY, DRIVE -> HERE. `rclone bisync` exists and is what "sync both ways"
# really means, but bidirectional sync is the thing that deletes work when it
# is misconfigured -- and the first run is exactly when it is misconfigured.
# This direction is the archive, and it is the one worth being boring. If a
# return path is wanted later it should be added deliberately, with a proven
# `--dry-run`, not inherited by accident from this script.
#
# `copy`, NOT `sync`. `sync` makes the destination match the source, which
# means deleting anything here that is no longer there. That is correct for a
# mirror and wrong for an archive: a file deleted in Drive by accident would
# be deleted here too, on the next run, which is precisely the event a backup
# is for.
#
# THE GOOGLE DOCS TRAP, which is the whole reason this needs a script rather
# than one obvious command. A Doc, Sheet or Slide is NOT A FILE. It has no
# bytes to download -- it lives in Google's editor and has no native format.
# Without --drive-export-formats, rclone skips every one of them and reports
# a clean run, and the "backup" of somebody's documents contains none of
# their documents. The formats below are in preference order: rclone takes
# the first one that applies to each file's type.
#
# WHERE IT WRITES, and why not the vault. /mnt/vault is the BACKUP drive and
# it is root-owned; filling it with a synced copy of Drive would put a
# growing dataset on the disk whose job is to have room for backups. This
# writes to the NUC's own disk, and the destination is one variable -- when
# the NAS is verified, point DEST at the NAS mount and nothing else changes.
#
# Runs from the `server` user's crontab. No root anywhere in this path.

set -uo pipefail

# --- refuse to run when the vault is not mounted ---------------------------
# An unmounted mountpoint is still a writable directory on the ROOT disk, so
# without this the script cheerfully writes to the wrong filesystem, fills /,
# and the files disappear the moment the vault is mounted back over the top.
# `mountpoint -q` asks the kernel the real question; a df threshold does not
# (df on an unmounted vault reports / and passes happily).
if ! mountpoint -q /mnt/vault; then
    echo "$(date '+%F %T')  vault is not mounted -- skipping" >&2
    exit 0
fi


REMOTE="${DRIVE_REMOTE:-gdrive:}"
# THE EXTERNAL DRIVE, from 2026-09-07. Jason: "let's also pull all the
# documents in my Google drive onto my own external drive."
#
# This was on the NUC's internal disk until then, deliberately: /mnt/vault is
# the BACKUP drive, and putting a growing dataset on the disk whose job is
# having room for backups is how backups start failing. He has asked for the
# external explicitly and the arithmetic is comfortable -- Drive is ~21GB
# against 609GB free -- so the concern is noted rather than overriding.
#
# WORTH KNOWING: /mnt/vault/drive is NOT swept by vault-backup.sh, which
# archives named docker volumes and ~/nuc-homelab and nothing else. So this
# copy does not get backed up into the drive it already sits on, which is
# the recursion that would otherwise be waiting here.
DEST="${DRIVE_DEST:-/mnt/vault/drive}"
RCLONE="${RCLONE_BIN:-/home/user/bin/rclone}"
LOG="${DRIVE_LOG:-/home/user/drive-sync.log}"

say() { printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

# NOTHING TO DO IS NOT A FAILURE, but it should say so once rather than
# filling the log with the same line every hour. Until `rclone config` has
# created the remote, this exits quietly.
if ! "$RCLONE" listremotes 2>/dev/null | grep -qx "${REMOTE}"; then
    say "remote ${REMOTE} is not configured yet -- run: rclone config"
    exit 0
fi

# One run at a time. A sync that takes longer than the interval would
# otherwise start overlapping itself, and two rclones writing the same tree
# is how you get half-written files.
exec 9>"/tmp/drive-sync.lock"
if ! flock -n 9; then
    say "previous run still going -- skipping this one"
    exit 0
fi

mkdir -p "$DEST"

# --- refuse to run when the vault is nearly full ----------------------------
# Added 2026-09-07, before it was needed rather than after.
#
# THE SITUATION THIS EXISTS FOR. Jason has a Google Takeout of 860GB sitting
# in Drive. This script is `rclone copy` with no size limit and no excludes,
# it runs every hour, and the vault has under 600GB free. The moment those
# archives appear in Drive, this job would begin pulling 860GB into a disk
# that cannot hold it -- and it does not stop at "full". It fills the volume
# that also holds Immich's library, the camera recordings, the database
# dumps and the Drive mirror itself, and every one of those starts failing
# at once, for a reason that would look nothing like "the sync did it".
#
# A backup job must never be the thing that takes the disk down. It stops
# and says so instead.
FREE_FLOOR_GB="${FREE_FLOOR_GB:-60}"
avail_gb=$(df -BG --output=avail "$DEST" 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "$avail_gb" ] && [ "$avail_gb" -lt "$FREE_FLOOR_GB" ]; then
    say "STOPPING: only ${avail_gb}G free on the vault, floor is ${FREE_FLOOR_GB}G"
    say "  nothing was copied. Free space, or raise FREE_FLOOR_GB deliberately."
    exit 0
fi

say "starting: ${REMOTE} -> ${DEST}"

# --track-renames is deliberately NOT in the flags below. rclone applies it
# only to `sync`; for `copy` it ignores the flag and logs an ERROR line
# saying so on every single run. A flag that implies behaviour it is not
# performing is worse than no flag -- it reads as though rename tracking is
# on when nothing of the sort is happening.

"$RCLONE" copy "$REMOTE" "$DEST" \
    --drive-export-formats docx,xlsx,pptx,svg,pdf \
    --exclude "takeout-*.zip" \
    --exclude "takeout-*.tgz" \
    --exclude "Takeout/**" \
    --drive-acknowledge-abuse \
    --create-empty-src-dirs \
    --transfers 4 \
    --checkers 8 \
    --tpslimit 10 \
    --retries 3 \
    --low-level-retries 10 \
    --stats-one-line \
    --stats 5m \
    --log-level INFO \
    --log-file "$LOG"
rc=$?

if [[ $rc -eq 0 ]]; then
    say "done: $(du -sh "$DEST" 2>/dev/null | cut -f1) in $DEST"
else
    # Loud, and with the code. A sync that half-worked and said nothing is
    # the failure mode this whole estate keeps running into.
    say "WARNING: rclone exited $rc -- the copy is incomplete"
fi
exit $rc
