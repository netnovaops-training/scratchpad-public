#!/bin/bash
# Format and mount a USB SSD for the download lane. Run ON THE PI.
#
# Jason, 2026-09-12: "im going to use an ssd through usb once setup" --
# for the Pi 3 that is taking over torrenting from the Pi 5.
#
# ===========================================================================
# WHY THIS IS A SCRIPT AND NOT THREE COMMANDS.
#
# It formats a disk. Getting the device wrong means formatting the wrong
# one, and on a machine whose root filesystem is also removable storage
# that is a real possibility rather than a theoretical one. So this
# REFUSES to touch anything that is mounted, anything holding the root
# filesystem, and anything that is not USB-attached -- and it prints what
# it found and waits for the device name to be typed, rather than picking.
#
# ===========================================================================
# THE PI 3 SPECIFICS THAT MATTER, because two of them corrupt data silently.
#
# POWER. A Pi 3 supplies about 600mA per port, 1.2A across all four. A
# bus-powered SSD can draw more than that under sustained write, and when
# it browns out the drive does not report an error -- it drops off the bus
# mid-write. The filesystem finds out later. Use a POWERED USB HUB, or a
# genuine 2.5A+ supply with max_usb_current=1 in /boot/firmware/config.txt.
# This script checks dmesg for the symptoms afterwards.
#
# USB 2.0 SHARED WITH ETHERNET. On a Pi 3 the network adapter hangs off the
# same USB controller as the disk. Around 35 MB/s is the realistic ceiling
# and disk and network contend for it. That is fine for this job -- Jason:
# "speed is not important" -- but it is why the Pi 5 keeps the work that
# needs throughput.
#
# EXT4, NOT EXFAT OR NTFS. exFAT has no journal, so an unclean unmount --
# which is exactly what a brownout produces -- can lose the directory
# structure rather than one file. NTFS on Linux goes through a userspace
# driver that costs CPU this machine does not have spare.
#
#   ./usb-disk-setup.sh            # show what is attached, change nothing
#   ./usb-disk-setup.sh --disk sda # format and mount that disk
# ===========================================================================
set -e

MOUNT="${MOUNT:-/mnt/downloads}"
DISK=""
for a in "$@"; do
    case "$a" in
        --disk=*) DISK="${a#--disk=}" ;;
        --disk)   shift; DISK="$1" ;;
        *) ;;
    esac
done

echo
echo "  BLOCK DEVICES"
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,TRAN,MODEL | sed 's/^/    /'
echo

ROOTDISK=$(lsblk -no PKNAME "$(findmnt -no SOURCE /)" 2>/dev/null | head -1)
echo "    root filesystem is on: ${ROOTDISK:-unknown}  (never offered below)"
echo

if [ -z "$DISK" ]; then
    echo "  USB disks that could be used:"
    found=0
    for d in /sys/block/sd*; do
        [ -e "$d" ] || continue
        n=$(basename "$d")
        [ "$n" = "$ROOTDISK" ] && continue
        tran=$(lsblk -no TRAN "/dev/$n" 2>/dev/null | head -1)
        [ "$tran" = "usb" ] || continue
        sz=$(lsblk -no SIZE "/dev/$n" | head -1)
        mdl=$(lsblk -no MODEL "/dev/$n" | head -1)
        mnt=$(lsblk -no MOUNTPOINT "/dev/$n" | tr -d ' \n')
        printf "    %-6s %-8s %-22s %s\n" "$n" "$sz" "$mdl" \
               "${mnt:+MOUNTED at $mnt -- will be refused}"
        found=1
    done
    [ "$found" -eq 0 ] && echo "    none found -- is it plugged in and powered?"
    echo
    echo "  Re-run with the device, e.g.:  $0 --disk sda"
    echo "  THIS ERASES THAT DISK."
    exit 0
fi

DEV="/dev/$DISK"
[ -b "$DEV" ] || { echo "  $DEV is not a block device"; exit 1; }
[ "$DISK" = "$ROOTDISK" ] && { echo "  REFUSING: $DISK holds the root filesystem"; exit 1; }
[ "$(lsblk -no TRAN "$DEV" | head -1)" = "usb" ] || {
    echo "  REFUSING: $DISK is not USB-attached. This script is for the"
    echo "  external SSD; anything internal is not what it was written for."
    exit 1; }
if lsblk -no MOUNTPOINT "$DEV" | grep -q '[^[:space:]]'; then
    echo "  REFUSING: something on $DISK is mounted. Unmount it first, and"
    echo "  look at what it is while you are there."
    exit 1
fi

echo "  ABOUT TO ERASE $DEV"
lsblk -o NAME,SIZE,FSTYPE,MODEL "$DEV" | sed 's/^/    /'
echo
printf "  Type the disk name again to confirm: "
read -r CONFIRM
[ "$CONFIRM" = "$DISK" ] || { echo "  did not match -- nothing done"; exit 1; }

echo
echo "  partitioning"
sudo wipefs -a "$DEV"
sudo parted -s "$DEV" mklabel gpt mkpart primary ext4 0% 100%
sleep 2
PART="${DEV}1"
[ -b "$PART" ] || PART="${DEV}p1"

echo "  formatting ext4"
# -m 0 because reserving 5% for root on a download disk wastes gigabytes
# for a guarantee nothing here needs.
sudo mkfs.ext4 -F -m 0 -L downloads "$PART"

UUID=$(sudo blkid -s UUID -o value "$PART")
echo "  UUID: $UUID"

sudo mkdir -p "$MOUNT"
# BY UUID, NOT BY /dev/sda1. Device names are assignment order -- plug in a
# second disk, or reboot with a USB stick in, and sda becomes sdb. fstab
# entries by device name are how a machine boots with the wrong disk
# mounted somewhere important.
#
# nofail so the Pi still boots with the disk unplugged, and
# x-systemd.device-timeout so it does not wait 90 seconds first.
LINE="UUID=$UUID $MOUNT ext4 defaults,noatime,nofail,x-systemd.device-timeout=10 0 2"
if grep -q "$MOUNT" /etc/fstab; then
    echo "  /etc/fstab already mentions $MOUNT -- not touching it, check by hand"
else
    echo "$LINE" | sudo tee -a /etc/fstab >/dev/null
    echo "  added to /etc/fstab"
fi

sudo systemctl daemon-reload
sudo mount "$MOUNT"
sudo mkdir -p "$MOUNT"/{incomplete,complete,general,private}
sudo chown -R "$(id -u):$(id -g)" "$MOUNT"
chmod 700 "$MOUNT/private"

echo
echo "  mounted:"
findmnt "$MOUNT" | sed 's/^/    /'
df -h "$MOUNT" | tail -1 | sed 's/^/    /'

echo
echo "  write test"
dd if=/dev/zero of="$MOUNT/.speed" bs=1M count=200 conv=fsync 2>&1 | tail -1 | sed 's/^/    /'
rm -f "$MOUNT/.speed"

echo
echo "  POWER CHECK -- the failure that corrupts data silently:"
if sudo dmesg | grep -iE "under-voltage|over-current|usb .*disconnect|reset high-speed" | tail -3 | grep -q .; then
    sudo dmesg | grep -iE "under-voltage|over-current|usb .*disconnect|reset high-speed" | tail -3 | sed 's/^/    /'
    echo "    ^ THIS IS THE SYMPTOM. Use a powered hub before trusting it."
else
    echo "    nothing in dmesg -- good so far. Check again after a real download."
fi

echo
echo "  Now set DOWNLOADS=$MOUNT in the stack .env and bring the lane up."
