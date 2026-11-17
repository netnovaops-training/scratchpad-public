#!/bin/bash
# Bring up anything in the Pi 5 stack that is not running.
#
# WHY THIS EXISTS. The 2026-09-13 power cut left navidrome and liquidsoap
# dead for FIFTEEN HOURS and nobody noticed. Both exited 255 at boot, and
# `docker start` fixed them instantly with no other change -- so nothing was
# broken. They simply came up before the NFS music mount was ready, failed,
# and `restart: unless-stopped` gave up after its attempts.
#
# The NUC lost eleven containers to the identical cascade the same night.
# It is not a Pi problem or a Navidrome problem; it is what happens when
# Docker starts everything at once on a boot where the NAS is still coming
# up. The fix is to look again a few minutes later, when it isn't.
#
# DELIBERATELY `docker start`, NOT `docker compose up -d`. Compose would
# happily RECREATE a container to match a changed compose file -- which is
# the right thing when a human runs it deliberately and the wrong thing for
# a boot script that should only ever restart what already exists.
#
# Idempotent and quiet: does nothing when everything is up, so it is safe
# to run every few minutes forever.
set -uo pipefail

# Overridable so the pause path can actually be exercised in a test. /run is
# root-owned: maintenance scripts run as root and create it, this script runs
# as `server` from cron on the NUC and only ever reads it, which needs no
# privilege. That asymmetry is deliberate -- an unprivileged process should
# not be able to mute the thing that repairs the stack.
PAUSE="${STACK_ENSURE_PAUSE:-/run/stack-ensure.pause}"

# WHERE THE LOG GOES, and why it is not simply /var/log. On the Pi 5 this runs
# as root from a systemd timer and /var/log is writable. On the NUC it runs
# from the `server` crontab, which CANNOT write /var/log -- so for its first
# day this script logged absolutely nothing and there was no way to tell
# whether it had ever done anything. A log that silently discards itself is
# worse than no log, because it looks like evidence of nothing happening.
LOG=/var/log/stack-ensure.log
if [ -e "$LOG" ]; then
    [ -w "$LOG" ] || LOG="$HOME/stack-ensure.log"
elif ! [ -w "$(dirname "$LOG")" ]; then
    LOG="$HOME/stack-ensure.log"
fi
log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG" 2>/dev/null; }

# A MAINTENANCE PAUSE. Deliberately stopping containers and then having this
# revive them four minutes later is worse than either thing alone. During the
# vault resize the containers are stopped precisely so /mnt/vault can be
# unmounted; starting them against the missing mount would create bare
# directories at the mountpoint and block the remount.
#
# /run rather than /etc on purpose: /run is cleared at boot, so a pause left
# behind by a crashed or power-cut maintenance script cannot silently keep the
# whole stack down forever. Worst case it expires by itself.
if [ -e "$PAUSE" ]; then
    log "paused by $PAUSE ($(cat "$PAUSE" 2>/dev/null)) -- doing nothing"
    exit 0
fi

# WAIT FOR THE NAS, NOT FOR A MOUNTPOINT.
#
# The first version of this waited for `/volume1/Music` to appear in `mount`.
# That was wrong in two ways. It is a Docker NFS volume, not an fstab entry --
# Docker mounts it when the container starts, so it cannot possibly be there
# BEFORE the thing that needs it starts, and on the NUC, whose NAS shares are
# CIFS volumes under different paths entirely, it could never appear at all.
# The NUC therefore slept the full 300 seconds on every single run, every ten
# minutes, waiting for something structurally incapable of arriving.
#
# The actual precondition is simpler and the same on every host: can this box
# reach the NAS? If it can, Docker's nfs/cifs volume mounts will succeed and
# the containers will start. If it cannot, they will exit 255 exactly as they
# did in the 2026-09-13 outage.
#
# /dev/tcp rather than ping: ICMP being answered says nothing about whether
# the file service is up, and this needs no extra package. 2049 is NFS, 445
# is SMB -- either one answering means the NAS is serving.
NAS="${STACK_ENSURE_NAS:-192.168.1.68}"
nas_up() {
    timeout 3 bash -c "echo > /dev/tcp/$NAS/2049" 2>/dev/null && return 0
    timeout 3 bash -c "echo > /dev/tcp/$NAS/445"  2>/dev/null && return 0
    return 1
}
for i in $(seq 1 30); do
    nas_up && break
    [ "$i" = 1 ] && log "waiting for the NAS at $NAS..."
    [ "$i" = 30 ] && log "NAS still unreachable after 5 minutes -- reviving anyway"
    sleep 10
done

started=0
for name in $(docker ps -a --format '{{.Names}}'); do
    state=$(docker inspect "$name" -f '{{.State.Status}}' 2>/dev/null) || continue
    [ "$state" = "running" ] && continue
    # Leave anything a human stopped on purpose alone: only revive things
    # whose restart policy says they were meant to be running.
    policy=$(docker inspect "$name" -f '{{.HostConfig.RestartPolicy.Name}}' 2>/dev/null)
    case "$policy" in
        always|unless-stopped) ;;
        *) continue ;;
    esac
    if docker start "$name" >/dev/null 2>&1; then
        log "started $name (was $state)"
        started=$((started + 1))
    else
        log "FAILED to start $name (was $state)"
    fi
done

[ "$started" -gt 0 ] && log "revived $started container(s)"
exit 0
