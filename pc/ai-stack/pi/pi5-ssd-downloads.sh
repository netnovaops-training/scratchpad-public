#!/usr/bin/env bash
# Move the Pi 5's torrent storage from sshfs-over-the-network to a local SSD.
#
#     ssh -p 1143 -t jason@192.168.1.69 'bash ~/pi5-ssd-downloads.sh'
#
# WHY THE SSD MOVED FROM THE PI 3 TO THE PI 5.
#
# The Pi 3 cannot power it. A WD SN770 in a USB-NVMe adapter declares
# 500mA and draws more under write; a Pi 3's USB ports budget 600mA in
# total, shared with the Ethernet chip, which is on the same USB bus.
# The first wipe attempt browned the board out mid-command --
# throttled=0x50005, under-voltage AND throttling both live -- and the
# Pi has not come back since. max_usb_current=1 raises that budget to
# 1.2A and is the only software lever; it was not enough.
#
# The Pi 5 has USB 3.0 with a real current budget and is on the official
# 27W supply, which is exactly what fixed its own under-voltage history
# (1,276 events -> throttled=0x0).
#
# ---------------------------------------------------------------------
# THE COLLISION THIS EXISTS TO HANDLE.
#
# /mnt/downloads on this Pi is ALREADY a mount -- sshfs to the NUC:
#
#   server@192.168.1.86:/home/user/downloads  ->  /mnt/downloads
#   via x-systemd.automount in /etc/fstab
#
# usb-disk-setup.sh mounts a USB disk at that same path. Doing that
# without removing the sshfs entry first gives two filesystems stacked on
# one mountpoint: whichever mounted last wins, the other's data becomes
# invisible but still on disk, and the automount unit re-mounts sshfs on
# top of the SSD at the next access. That is the sort of thing found
# three weeks later when a download "disappears".
#
# So the sshfs entry is removed and its automount unit stopped BEFORE
# anything is mounted.
#
# NOTHING IS LOST: the NUC's ~/downloads holds 28K -- four empty
# directories. Checked before writing this.
#
# WHAT DOES NOT CHANGE: qbittorrent and metube already bind
# /mnt/downloads. They need no compose edit, no restart argument, no new
# path. The storage underneath changes and they do not notice.
set -euo pipefail

FSTAB=/etc/fstab
MNT=/mnt/downloads

echo "  1. checking for the disk"
usb=$(lsblk -dno NAME,TRAN | awk '$2=="usb"{print $1}')
if [ -z "$usb" ]; then
    echo "     no USB disk attached. Plug the SSD in and re-run."
    exit 1
fi
echo "     found: $(lsblk -dno NAME,SIZE,MODEL /dev/$usb | tr -s ' ')"

echo "  2. checking power, because this is why it moved here"
t=$(vcgencmd get_throttled 2>/dev/null || echo "throttled=unknown")
echo "     $t"
case "$t" in
    *0x0) echo "     clean" ;;
    *unknown) echo "     could not read" ;;
    *) echo "     !! under-voltage or throttling present. The Pi 5 on its"
       echo "        official 27W supply should read 0x0. Fix the supply"
       echo "        before trusting this disk with anything."
       echo "        Continuing anyway -- this is a warning, not a refusal." ;;
esac

echo "  3. standing down the sshfs mount at $MNT"
# Stop the automount unit first. Removing the fstab line alone is not
# enough -- systemd has already generated a .automount unit from it and
# will keep re-mounting on access until told otherwise.
unit=$(systemd-escape -p --suffix=automount "$MNT")
if systemctl is-active --quiet "$unit" 2>/dev/null; then
    sudo systemctl stop "$unit" || true
    echo "     stopped $unit"
fi
sudo umount -l "$MNT" 2>/dev/null || true

if grep -q "fuse.sshfs.*$MNT\|$MNT.*fuse.sshfs" "$FSTAB"; then
    sudo cp "$FSTAB" "$FSTAB.bak-ssd-$(date +%Y%m%d-%H%M%S)"
    # Comment rather than delete: if the SSD is ever removed, this is the
    # one line that puts downloads back, and reconstructing it from
    # memory is not something anyone should have to do.
    sudo sed -i "s|^\(.*fuse\.sshfs.*$MNT.*\)$|# superseded by the local SSD $(date +%Y-%m-%d) -- uncomment to go back\n#\1|" "$FSTAB"
    sudo sed -i "s|^\(.*$MNT.*fuse\.sshfs.*\)$|# superseded by the local SSD $(date +%Y-%m-%d) -- uncomment to go back\n#\1|" "$FSTAB"
    sudo systemctl daemon-reload
    echo "     fstab entry commented out (backup taken)"
else
    echo "     no sshfs entry found -- nothing to stand down"
fi

echo
echo "  4. now run the disk setup, which formats and mounts at $MNT:"
echo
echo "       ./usb-disk-setup.sh --disk $usb"
echo
echo "     THAT ERASES /dev/$usb. It asks you to type the name twice."
echo
echo "  5. afterwards, qbittorrent and metube pick it up with no change --"
echo "     they already bind $MNT. A restart is only needed if they were"
echo "     running while it was unmounted:"
echo
echo "       cd ~/stack && docker compose -f compose.yml -f compose-download.yml restart qbittorrent metube"
