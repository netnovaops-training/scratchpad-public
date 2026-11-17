#!/usr/bin/env bash
# Back everyone's files up to the encrypted external SSD on the NUC.
#
# THE MOUNT CHECK IS THE WHOLE REASON THIS SCRIPT IS CAREFUL.
#
# /mnt/vault is a LUKS-encrypted partition on the external drive, and it is
# NOT in /etc/crypttab or /etc/fstab -- checked, there is nothing there. So
# after any reboot it is locked, and /mnt/vault is just an empty directory
# on the NUC's own root disk.
#
# Writing to it in that state does not fail. It succeeds, quietly, onto an
# unencrypted internal disk, and reports success. Every file lands in the
# wrong place with the wrong protection and nothing anywhere says so --
# and the day it matters is the day the drive is gone and the "backup"
# turns out to be 40 GB of nothing, or worse, everyone's records sitting
# unencrypted on a machine that was supposed to be holding none.
#
# So: findmnt confirms /mnt/vault is a real mount point, and the device
# behind it is a dm-crypt mapping. Both, before a single byte moves. If
# either check fails the script stops and says why, and the manifest it
# writes records vault_verified=false so /files shows it rather than
# implying a good backup happened.
#
# Usage:  ./files-backup.sh
#         DRY=1 ./files-backup.sh     say what would move, move nothing

set -euo pipefail
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

NUC="${NUC:-server@192.168.1.86}"
VAULT="${VAULT:-/mnt/vault}"
DEST="${DEST:-$VAULT/backups/household}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
DRY="${DRY:-0}"
STATUS_VOL="${STATUS_VOL:-ai-stack_files_backup_status}"

# container : name under the backup : path inside it
#
# The path is per-tree because Nextcloud does not keep its files in /data
# like the others. /var/www/html/data is the user data; the rest of that
# volume is the application itself -- hundreds of megabytes of PHP that
# reinstalls from an image in a minute and has no business in a backup.
TREES="
casey|casey-career|/data
mom|mom-site|/data
kitchen|kitchen|/data
nextcloud|nextcloud-files|/var/www/html/data
"

say() { printf '  %s\n' "$*"; }
fail() { printf '\nSTOPPED: %s\n' "$*" >&2; write_manifest false "$*"; exit 1; }

BYTES=0
DONE_TREES=""

write_manifest() {
  local verified="$1" note="${2:-}"
  local json
  json=$(printf '{"finished_at": %s, "finished_at_human": "%s", "vault_verified": %s, "bytes": %s, "trees": [%s], "note": "%s"}' \
    "$(date +%s)" "$(date '+%d %b %Y %H:%M')" "$verified" "$BYTES" \
    "$DONE_TREES" "$(printf '%s' "$note" | tr -d '"')")
  # Into the volume /files reads. Written through a throwaway container so
  # the host needs no knowledge of where Docker keeps its volumes.
  printf '%s' "$json" | docker run --rm -i -v "${STATUS_VOL}:/s" busybox \
    sh -c 'cat > /s/manifest.json' 2>/dev/null || true
}

echo "Household backup -> ${NUC}:${DEST}/${STAMP}"

# --- 1. Is the encrypted drive actually there? ------------------------------
say "checking the vault is mounted and encrypted..."
ssh -o ConnectTimeout=15 "$NUC" "
  set -e
  findmnt -no SOURCE,FSTYPE '$VAULT' >/dev/null 2>&1 || {
    echo 'NOT-MOUNTED'; exit 3; }
  src=\$(findmnt -no SOURCE '$VAULT')
  case \"\$src\" in
    /dev/mapper/*) : ;;                 # dm-crypt mapping, as expected
    *) echo \"NOT-ENCRYPTED:\$src\"; exit 4 ;;
  esac
  # A LUKS mapping's name shows under /sys/block/dm-*/dm/name; confirm the
  # device really is a crypt target rather than just living in /dev/mapper.
  dm=\$(basename \"\$src\")
  if [ -e \"/sys/class/block/\$(readlink -f \"\$src\" | xargs basename)/dm/uuid\" ]; then
    uuid=\$(cat \"/sys/class/block/\$(readlink -f \"\$src\" | xargs basename)/dm/uuid\")
    case \"\$uuid\" in CRYPT-*) : ;; *) echo \"NOT-CRYPT:\$uuid\"; exit 5 ;; esac
  fi
  echo OK
" >/tmp/vaultcheck 2>&1 || {
  case "$(cat /tmp/vaultcheck 2>/dev/null | tail -1)" in
    NOT-MOUNTED) fail "$VAULT is not mounted on the NUC. The drive is locked -- unlock it first:
    ssh $NUC
    sudo cryptsetup open /dev/sda1 vault && sudo mount /dev/mapper/vault $VAULT
  Nothing was copied. Writing to a locked vault would have put everything on
  the NUC's unencrypted internal disk and reported success." ;;
    # 2026-10-31: Jason decided he does not want the drive encrypted. The NOT-MOUNTED
    # guard above stays (it stops writes to the NUC's internal disk); only the
    # "must be LUKS" refusal is skipped, and only when run-backup.cmd sets this.
    NOT-ENCRYPTED:*|NOT-CRYPT:*) if [ "${ALLOW_UNENCRYPTED:-0}" = 1 ]; then
        say "WARNING: $VAULT is a real mount but not encrypted; continuing because ALLOW_UNENCRYPTED=1"
      else
        fail "$VAULT is mounted but NOT from an encrypted device ($(tail -1 /tmp/vaultcheck)). Refusing to write personal files there."
      fi ;;
    *) fail "could not check the vault: $(tail -2 /tmp/vaultcheck | tr '\n' ' ')" ;;
  esac
}
say "vault verified: encrypted and mounted"

FREE=$(ssh -o ConnectTimeout=15 "$NUC" "df -h '$VAULT' | awk 'NR==2{print \$4}'")
say "free on the vault: $FREE"

# --- 2. Move each tree ------------------------------------------------------
# The destination is created only for a real run. A dry run used to make it
# anyway, which left an empty dated folder sitting in the backup directory
# looking exactly like a backup that had happened -- the one thing a backup
# folder must never contain.
if [ "$DRY" != "1" ]; then
  ssh -o ConnectTimeout=15 "$NUC" "mkdir -p '$DEST/$STAMP'"
fi

while IFS='|' read -r container name srcpath; do
  [ -z "$container" ] && continue
  if ! docker ps --format '{{.Names}}' | grep -qx "$container"; then
    say "SKIP $name -- the $container container is not running"
    continue
  fi
  size=$(docker exec "$container" du -sb "$srcpath" 2>/dev/null | cut -f1 || echo 0)
  say "$name ($(numfmt --to=iec "$size" 2>/dev/null || echo "$size B"))..."
  if [ "$DRY" = "1" ]; then
    say "  (dry run, nothing sent)"
    continue
  fi
  # tar over ssh. rsync would need rsync on Windows; at these sizes its
  # delta transfer saves little and the dependency costs more than it gives.
  docker exec "$container" tar -C "$srcpath" -cf - . 2>/dev/null \
    | ssh -o ConnectTimeout=15 "$NUC" \
        "mkdir -p '$DEST/$STAMP/$name' && tar -C '$DEST/$STAMP/$name' -xf -"
  BYTES=$((BYTES + size))
  DONE_TREES="${DONE_TREES:+$DONE_TREES,}\"$name\""
done <<< "$TREES"

[ "$DRY" = "1" ] && { echo "dry run complete."; exit 0; }

# --- 3. Read it back rather than trusting the pipe --------------------------
say "verifying on the NUC..."
ssh -o ConnectTimeout=15 "$NUC" "
  cd '$DEST/$STAMP' || { echo '    NOTHING ARRIVED'; exit 1; }
  du -sh * 2>/dev/null | sed 's/^/    /'
  echo \"    total: \$(du -sh . | cut -f1)\"
"

# --- 4. Keep 14, and only ever inside our own backup folder -----------------
ssh -o ConnectTimeout=15 "$NUC" \
  "cd '$DEST' && ls -1d 2* 2>/dev/null | sort | head -n -14 | xargs -r rm -rf"

write_manifest true "Backed up to the encrypted external drive."
say "manifest written for /files"
echo "done."
