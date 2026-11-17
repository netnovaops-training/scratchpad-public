#!/bin/bash
# Cut what the Pi writes to its SD card, so an always-on node does not eat it.
#
# Jason, 2026-09-10: "dont put anything heavy for my memory card".
#
# ---------------------------------------------------------------------------
# THE MEASUREMENT THAT PROMPTED THIS.
#
# Twenty-four minutes after boot, doing nothing in particular:
#
#     /sys/block/mmcblk0/stat  ->  read 1591 MiB, written 3818 MiB
#
# That is roughly 9 GB/hour, 220 GB/day, on a consumer card manufactured
# 11/2018 that does not report life_time or pre_eol_info at all -- so there
# is no warning before it goes. And it is the root filesystem, so when it
# goes it takes the machine.
#
# Almost none of those writes are useful. They are logs, journal, atime
# updates, and ext4 flushing every five seconds. This script removes each
# of those in turn. It does not make the card immortal; it changes the
# timescale from months to years, which is long enough to get an NVMe HAT
# on it without urgency.
#
# WHAT THIS COSTS YOU, stated plainly rather than buried: logs no longer
# survive a reboot, and up to ten minutes of writes can be lost in a hard
# power cut. Both are the right trade for this machine -- it is a node that
# runs containers and answers probes, its logs ship to Loki on the PC
# anyway, and every service on it rebuilds its state from elsewhere. Neither
# would be the right trade on the NUC, which holds the vault.
#
#   ./pi-sdcard-guard.sh          # show what would change
#   ./pi-sdcard-guard.sh --go
#   ./pi-sdcard-guard.sh --report # just measure, change nothing
# ---------------------------------------------------------------------------
set -u

GO=0
REPORT_ONLY=0
for a in "$@"; do
    case "$a" in
        --go)     GO=1 ;;
        --report) REPORT_ONLY=1 ;;
        *) echo "unknown option: $a"; exit 2 ;;
    esac
done

DEV=mmcblk0

measure() {
    awk -v up="$(cut -d. -f1 /proc/uptime)" '{
        w = $7 * 512 / 1048576
        printf "  written since boot: %.0f MiB over %d min  =  %.1f GB/day\n",
               w, up/60, (w / (up/86400)) / 1024
    }' "/sys/block/$DEV/stat"
}

echo
echo "  card:  $(cat /sys/block/$DEV/device/name 2>/dev/null) manufactured $(cat /sys/block/$DEV/device/date 2>/dev/null)"
echo "  wear reporting: $(cat /sys/block/$DEV/device/life_time 2>/dev/null || echo 'NOT AVAILABLE -- no warning before failure')"
measure
echo

if [ "$REPORT_ONLY" -eq 1 ]; then
    echo "  top writers right now:"
    sudo iotop -b -n1 -o -a 2>/dev/null | head -12 || \
        echo "    (install iotop to see them: sudo apt install -y iotop)"
    exit 0
fi

run() {
    if [ "$GO" -eq 1 ]; then echo "    + $*"; "$@"; else echo "    would: $*"; fi
}
write_file() {
    # $1 path, $2 content
    if [ "$GO" -eq 1 ]; then
        echo "    + write $1"
        printf '%s\n' "$2" | sudo tee "$1" >/dev/null
    else
        echo "    would write $1:"
        printf '%s\n' "$2" | sed 's/^/        /'
    fi
}

[ "$GO" -eq 0 ] && echo "  *** DRY RUN. Add --go. ***" && echo

# --- 1. the journal: the single biggest writer ---------------------------
# systemd-journald writes binary journals continuously and, by default,
# persists them to /var/log/journal. Storage=volatile keeps the journal in
# RAM (/run) instead. `journalctl` still works exactly as it does now for
# the current boot -- which is the only boot anyone ever actually reads --
# and `journalctl -b -1` stops working, which nobody has ever used here.
echo "  [1/6] journald -> RAM, capped at 64M"
write_file /etc/systemd/journald.conf.d/volatile.conf \
'# Written by pi-sdcard-guard.sh. The journal is the largest single source
# of SD card writes on an idle Pi. Logs ship to Loki on the PC; the local
# copy only needs to cover the current boot.
[Journal]
Storage=volatile
RuntimeMaxUse=64M
RuntimeMaxFileSize=16M
ForwardToSyslog=no
Compress=yes'
run sudo rm -rf /var/log/journal
echo

# --- 2. /var/log on tmpfs ------------------------------------------------
# Everything that still writes plain files under /var/log -- and there is
# always something -- writes into RAM instead.
echo "  [2/6] /var/log -> tmpfs (64M)"
if grep -q '^tmpfs /var/log' /etc/fstab 2>/dev/null; then
    echo "        already in fstab"
else
    run sudo cp /etc/fstab /etc/fstab.bak-sdguard
    if [ "$GO" -eq 1 ]; then
        echo 'tmpfs /var/log tmpfs defaults,noatime,nosuid,nodev,mode=0755,size=64M 0 0' \
            | sudo tee -a /etc/fstab >/dev/null
        echo "    + appended tmpfs /var/log to /etc/fstab"
    else
        echo "    would append: tmpfs /var/log tmpfs defaults,noatime,...,size=64M 0 0"
    fi
fi
echo

# --- 3. noatime and a lazy commit on the root filesystem -----------------
# atime writes a timestamp to disk every time a file is READ -- a write
# caused by a read, which is exactly backwards for flash. relatime (the
# default) reduces it but does not remove it.
#
# commit=600 lets ext4 batch its metadata writeback into ten-minute windows
# instead of flushing every five seconds. THIS IS THE ONE WITH A REAL COST:
# a hard power cut can lose up to ten minutes. On a steady supply, on a node
# whose state is all rebuildable, that is a good trade. It would not be on
# the NUC.
echo "  [3/6] root filesystem: noatime, commit=600"
CUR=$(findmnt -no OPTIONS / 2>/dev/null)
echo "        currently: $CUR"
if echo "$CUR" | grep -q noatime; then
    echo "        noatime already set"
else
    if [ "$GO" -eq 1 ]; then
        sudo sed -i -E '/\s\/\s+ext4\s/ s/(\sdefaults[^[:space:]]*|\sdiscard[^[:space:]]*)?\s+/&/; s|(^[^#].*\s/\s+ext4\s+)([^[:space:]]+)|\1\2,noatime,commit=600|' /etc/fstab
        echo "    + patched /etc/fstab root options"
        grep -E '\s/\s+ext4' /etc/fstab | sed 's/^/        /'
    else
        echo "    would add noatime,commit=600 to the root line in /etc/fstab"
    fi
fi
echo

# --- 4. stop the periodic background writers -----------------------------
# man-db rebuilds its index, apt refreshes package lists, fstrim runs --
# each a burst of writes on a machine that will never have a man page read
# on it. The apt timers are the ones that matter: they write the whole
# package list weekly.
echo "  [4/6] disabling periodic writers"
for t in man-db.timer apt-daily.timer apt-daily-upgrade.timer \
         e2scrub_all.timer dpkg-db-backup.timer; do
    if systemctl list-unit-files "$t" 2>/dev/null | grep -q "$t"; then
        run sudo systemctl disable --now "$t"
    fi
done
# fstrim is KEPT ON PURPOSE. It is a small weekly write that tells the card
# which blocks are free, which is what lets its controller wear-level at
# all. Disabling it to save writes makes the card die sooner, not later.
echo "        fstrim.timer left enabled -- it is what keeps the card healthy"
echo

# --- 5. kernel writeback tuning ------------------------------------------
# dirty_writeback_centisecs is how often the kernel wakes to flush dirty
# pages: 500 (5s) by default, 1500 (15s) here. vfs_cache_pressure lowered so
# directory entries stay cached instead of being re-read. swappiness is
# raised, not lowered -- swap here is zram, which is compressed RAM, so
# swapping costs no card writes at all and is strictly better than evicting
# the page cache and re-reading from the card.
echo "  [5/6] kernel writeback tuning"
write_file /etc/sysctl.d/60-sdcard.conf \
'# Written by pi-sdcard-guard.sh.
# Flush dirty pages less often; batch the writes that do happen.
vm.dirty_writeback_centisecs = 1500
vm.dirty_expire_centisecs = 6000
vm.dirty_ratio = 20
vm.dirty_background_ratio = 10
# Swap is zram -- compressed RAM, zero card writes. Prefer it to dropping
# cached pages that would have to be re-read from the card.
vm.swappiness = 100
vm.vfs_cache_pressure = 50'
run sudo sysctl --system
echo

# --- 6. docker: cap logs globally ----------------------------------------
# Every compose file in this repo caps its own logs, but a container started
# by hand does not, and an unbounded json-file log is how a card fills up
# overnight. This makes the cap the default for anything on this machine.
echo "  [6/6] docker: 10M x 3 log cap as the daemon default"
write_file /etc/docker/daemon.json \
'{
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}'
if [ "$GO" -eq 1 ]; then
    run sudo systemctl reload docker || run sudo systemctl restart docker
fi
echo

if [ "$GO" -eq 0 ]; then
    echo "  DRY RUN COMPLETE. Nothing changed. Re-run with --go."
    exit 0
fi

echo "  Done. /var/log and the fstab changes need a reboot to take effect."
echo "  Afterwards, check the rate again:"
echo "      ./pi-sdcard-guard.sh --report"
echo
echo "  Expect roughly 220 GB/day to fall to single-digit GB/day. If it does"
echo "  not, --report names the process still doing it."
