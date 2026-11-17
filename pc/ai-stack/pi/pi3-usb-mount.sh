#!/bin/bash
# Mount the USB stick NON-DESTRUCTIVELY and give the Pi a scratch area on it.
#
# WHY NOT THE RUNBOOK'S `usb-disk-setup.sh --disk sda`:
#   That erases the disk. This stick is not blank. It is Jason's Ventoy
#   boot stick AND a photo working drive:
#     Legacy/            35,530 files, 11 GB, 3,594 images/videos
#                        (incl. cropped/ and finshed/ -- EDITED work)
#     ubuntu-26.04-live-server-amd64.iso   2.9 GB
#     memtest86-usb.img                    1.0 GB
#   102 GB of the 116 GB is free, which is all the scratch space the Pi 3
#   will ever need. So we use the free space and leave the rest alone.
#
# Mount options chosen deliberately:
#   nofail     -- a missing/unplugged stick must never block boot. A Pi 3
#                 that will not boot because a USB stick moved is a Pi 3
#                 you have to go find a monitor for.
#   x-systemd.device-timeout=10  -- and never hang 90s waiting for it.
#   uid/gid    -- exfat has no POSIX ownership; it is assigned at mount.
#   umask=0022 -- files readable, not world-writable.
set -euo pipefail

USB_DEV=/dev/sda1
MOUNT=/mnt/usb
ME=$(id -un)
UID_N=$(id -u)
GID_N=$(id -g)

echo "=== 1. confirm this is the stick we surveyed, not some other disk ==="
LABEL=$(lsblk -no LABEL "$USB_DEV" 2>/dev/null || true)
FSTYPE=$(lsblk -no FSTYPE "$USB_DEV" 2>/dev/null || true)
echo "    $USB_DEV  label='$LABEL'  fstype='$FSTYPE'"
if [ "$LABEL" != "Ventoy" ] || [ "$FSTYPE" != "exfat" ]; then
  echo "    REFUSING: expected the exfat 'Ventoy' partition. Something moved." >&2
  exit 1
fi

echo
echo "=== 2. exfat driver ==="
if grep -qw exfat /proc/filesystems; then
  echo "    kernel exfat driver present (fast path)"
else
  echo "    no kernel driver -- installing exfat userspace tools"
  sudo apt-get update -qq
  sudo apt-get install -y -qq exfat-fuse exfatprogs
fi

echo
echo "=== 3. mount by UUID, via fstab, so it survives a reboot ==="
UUID=$(sudo blkid -s UUID -o value "$USB_DEV")
echo "    UUID=$UUID"
sudo mkdir -p "$MOUNT"
OPTS="uid=$UID_N,gid=$GID_N,umask=0022,nofail,x-systemd.device-timeout=10"
if grep -q "UUID=$UUID" /etc/fstab; then
  echo "    already in fstab, leaving it"
else
  # Keep a dated backup -- fstab mistakes are how a headless box stops booting.
  sudo cp /etc/fstab "/etc/fstab.bak.$(date +%Y%m%d-%H%M%S)"
  echo "UUID=$UUID  $MOUNT  exfat  $OPTS  0  0" | sudo tee -a /etc/fstab >/dev/null
  echo "    added (fstab backed up)"
fi

sudo systemctl daemon-reload
mountpoint -q "$MOUNT" || sudo mount "$MOUNT"
echo "    mounted:"
df -h "$MOUNT" | sed 's/^/      /'

echo
echo "=== 4. our own scratch area -- nothing else on the stick is touched ==="
mkdir -p "$MOUNT/pi3/downloads" "$MOUNT/pi3/tor-logs"
ls -la "$MOUNT" | sed 's/^/    /'

echo
echo "=== 5. proof the existing data is still there ==="
printf '    Legacy/ files: %s\n' "$(find "$MOUNT/Legacy" -type f 2>/dev/null | wc -l)"
printf '    ISOs/images:   %s\n' "$(ls -1 "$MOUNT"/*.iso "$MOUNT"/*.img 2>/dev/null | wc -l)"
