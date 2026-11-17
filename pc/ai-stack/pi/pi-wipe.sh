#!/bin/bash
# Wipe the Pi back to a clean compute/monitoring node -- without bricking it.
#
# Jason, 2026-09-10: "i want to wipe it ... but keep 1143 as the active port
# to communite i want to keep the host and password but wipe all user files
# and offload some resouces to it".
#
# ---------------------------------------------------------------------------
# WHY THIS EXISTS INSTEAD OF THE FIVE-LINER.
#
# The script this replaces had, as its third line:
#
#     sudo apt purge $(apt-mark showmanual) -y
#
# On THIS machine `apt-mark showmanual` returns 289 packages, and that list
# was read before writing this. It contains:
#
#     linux-image-rpi-2712    the kernel
#     raspi-firmware          the boot firmware
#     rpi-eeprom              the bootloader
#     systemd  init  udev     PID 1
#     libc6  bash  coreutils  userspace
#     dpkg  apt  sudo         the tools you would repair it with
#     network-manager         networking
#     wpasupplicant           wifi
#     firmware-brcm80211      the onboard wifi/BT radio driver
#
# apt refuses to remove Essential packages without --allow-remove-essential,
# so it would probably abort -- but the kernel, the boot firmware and the
# radio driver are NOT marked essential. Those go. A Pi with no kernel and no
# wifi driver is recovered with a card reader, not over SSH.
#
# And openssh-server is marked AUTO on this box, not manual. So step 3 leaves
# it and step 4's `autoremove --purge` takes it as an orphan -- PURGE, which
# deletes /etc/ssh/sshd_config, which is the only place port 1143 is written
# down. The request was "keep 1143 as the active port"; that command deletes
# the file that defines it, the host keys, and the daemon, in one go.
#
# ---------------------------------------------------------------------------
# WHAT THIS DOES DIFFERENTLY.
#
# 1. PINS what must survive. `apt-mark manual` on the kernel, firmware and
#    openssh-server FIRST. autoremove cannot take a manually-marked package,
#    so the command that removes orphans can no longer remove the way back in.
#
# 2. NAMES what to remove. An explicit list of the software actually being
#    retired -- apache2, mariadb, native caddy, the desktop, cups, influxdb,
#    cloud-init. A list you can read is a list you can be wrong about safely;
#    $(apt-mark showmanual) is not.
#
# 3. KEEPS ~/.ssh. `rm -rf ~/*` was asked for and is right for user files,
#    but authorized_keys is not a user file, it is the door. It is moved
#    aside and put back.
#
# 4. VERIFIES SSH BEFORE IT IS TOO LATE. `sshd -t` and a listener check run
#    at the end, while this session is still open. If sshd is broken, that is
#    the moment to find out -- not after the disconnect.
#
# 5. DRY RUN BY DEFAULT. Nothing happens without --go.
#
#   ./pi-wipe.sh              # show what would happen
#   ./pi-wipe.sh --go         # do it
#   ./pi-wipe.sh --go --keep-desktop
# ---------------------------------------------------------------------------
set -u

GO=0
KEEP_DESKTOP=0
NEWHOST="sachin"

for a in "$@"; do
    case "$a" in
        --go)            GO=1 ;;
        --keep-desktop)  KEEP_DESKTOP=1 ;;
        --host=*)        NEWHOST="${a#--host=}" ;;
        *) echo "unknown option: $a"; exit 2 ;;
    esac
done

ME="$(whoami)"
OLDHOST="$(hostname)"

run() {
    if [ "$GO" -eq 1 ]; then
        echo "    + $*"
        "$@"
    else
        echo "    would: $*"
    fi
}

shell() {
    if [ "$GO" -eq 1 ]; then
        echo "    + $1"
        sh -c "$1"
    else
        echo "    would: $1"
    fi
}

# --- guard --------------------------------------------------------------
# Refuse to run anywhere but the Pi. This purges a desktop and clears a home
# directory; running it on the wrong box by pasting it into the wrong
# terminal is the obvious way for it to go wrong.
if ! grep -qi raspberry /proc/device-tree/model 2>/dev/null; then
    echo "  REFUSING: this is not a Raspberry Pi."
    echo "  (/proc/device-tree/model does not say so.)"
    exit 1
fi
if [ "$(id -u)" -eq 0 ]; then
    echo "  REFUSING: run as jason, not root. It uses sudo where it needs to,"
    echo "  and it needs to know whose home directory to clear."
    exit 1
fi

echo
echo "  Raspberry Pi: $(tr -d '\0' < /proc/device-tree/model)"
echo "  host:         $OLDHOST  ->  $NEWHOST"
echo "  user:         $ME  (password unchanged -- /etc/shadow is not touched)"
echo "  ssh port:     $(sudo sshd -T 2>/dev/null | awk '/^port /{print $2}')  (kept)"
echo "  disk:         $(df -h / | awk 'NR==2{print $3" used of "$2}')"
if [ "$GO" -eq 0 ]; then
    echo
    echo "  *** DRY RUN. Nothing below actually happens. Add --go. ***"
fi
echo

# --- 1. pin what must survive -------------------------------------------
echo "  [1/8] pinning the packages that must not be autoremoved"
echo "        (a manually-marked package is one autoremove cannot take)"
KEEP="openssh-server openssh-sftp-server sudo
      linux-image-rpi-2712 linux-image-rpi-v8
      raspi-firmware rpi-eeprom raspberrypi-sys-mods raspi-config
      firmware-brcm80211 firmware-realtek firmware-atheros firmware-mediatek
      network-manager wpasupplicant dhcpcd-base nftables
      ca-certificates initramfs-tools kmod udev systemd systemd-sysv
      docker-ce docker-ce-cli docker-compose-plugin containerd.io"
for p in $KEEP; do
    if dpkg -s "$p" >/dev/null 2>&1; then
        run sudo apt-mark manual "$p"
    fi
done
echo

# --- 2. docker: containers, images, volumes -----------------------------
# The nine containers here are the old stack -- juns-garden, plant_orchestrator,
# mariadb, momhelp, the DNS experiments. Docker itself stays; what it is
# running does not.
echo "  [2/8] removing every container, image, volume and network"
docker ps -a --format '      {{.Names}}  ({{.Image}})' 2>/dev/null
shell "docker ps -aq | xargs -r docker rm -f"
shell "docker system prune -af --volumes"
echo

# --- 3. purge the retired software --------------------------------------
# NAMED, not computed. Each of these is something that was actually running
# on this Pi and is not part of a compute/monitoring node.
echo "  [3/8] purging the retired software"
PURGE="apache2 apache2-bin apache2-data apache2-utils
       mariadb-server mariadb-client mariadb-common
       caddy
       cups cups-daemon cups-browsed cups-common
       influxdb influxdb2 influxdb3-core
       nfs-common rpcbind
       cloud-init
       unbound
       samba samba-common-bin"
if [ "$KEEP_DESKTOP" -eq 0 ]; then
    # The desktop is well over a gigabyte and a standing attack surface on a
    # box whose job is now "run containers and answer probes".
    # --keep-desktop opts out.
    PURGE="$PURGE lightdm wayvnc xserver-xorg xserver-xorg-core
           raspberrypi-ui-mods accountsservice
           lxde-core lxpanel pcmanfm gvfs"
fi
FOUND=""
for p in $PURGE; do
    dpkg -s "$p" >/dev/null 2>&1 && FOUND="$FOUND $p"
done
if [ -n "$FOUND" ]; then
    echo "        installed, and will go:"
    for f in $FOUND; do echo "          $f"; done
    run sudo apt-get purge -y $FOUND
else
    echo "        none of them are installed"
fi
echo

echo "  [4/8] removing orphans and clearing the package cache"
echo "        (safe now -- step 1 pinned the kernel, firmware and sshd)"
run sudo apt-get autoremove --purge -y
run sudo apt-get clean
echo

# --- 5. wipe user files, keeping the door --------------------------------
echo "  [5/8] clearing /home/$ME, keeping ~/.ssh"
echo "        4.3G: juns-garden, garden-brain, plant_orchestrator, iot,"
echo "        InfluxDB, matrix-chat, certs, backups"
run mkdir -p /tmp/keep-ssh
shell "cp -a ~/.ssh/. /tmp/keep-ssh/ 2>/dev/null || true"
# `find -mindepth 1 -delete`, NOT `rm -rf ~/* ~/.*`. In bash, `.*` globs to
# `.` and `..`; GNU rm refuses those two by name, but the pattern is one
# shell option (dotglob, or a non-GNU rm) away from walking out of the home
# directory entirely. find never leaves the tree it was given.
shell "find \$HOME -mindepth 1 -maxdepth 1 -not -name .ssh -exec rm -rf {} +"
run cp -rT /etc/skel "/home/$ME"
shell "mkdir -p ~/.ssh && cp -a /tmp/keep-ssh/. ~/.ssh/ && chmod 700 ~/.ssh && chmod 600 ~/.ssh/* 2>/dev/null; true"
shell "chown -R $(id -u):$(id -g) \$HOME"
run sudo rm -rf /var/www
echo

# --- 6. logs -------------------------------------------------------------
echo "  [6/8] truncating logs"
shell "sudo find /var/log -type f -exec truncate -s 0 {} +"
run sudo journalctl --vacuum-size=50M
echo

# --- 7. hostname ---------------------------------------------------------
echo "  [7/8] renaming the host to $NEWHOST"
# hostnamectl AND /etc/hosts. Without the hosts entry sudo stalls for about
# ten seconds on every single call while it fails to resolve its own name.
run sudo hostnamectl set-hostname "$NEWHOST"
shell "sudo sed -i \"s/\\b${OLDHOST}\\b/${NEWHOST}/gI\" /etc/hosts"
echo

# --- 8. verify the way back in -------------------------------------------
# This runs while the session is still open. That is the entire point of
# doing it here rather than finding out after the disconnect.
echo "  [8/8] verifying SSH before you disconnect"
if [ "$GO" -eq 0 ]; then
    echo "        would run: sshd -t, listener check, authorized_keys check"
    echo
    echo "  DRY RUN COMPLETE. Nothing was changed. Re-run with --go."
    exit 0
fi

fail=0
if sudo sshd -t 2>&1; then
    echo "        sshd config parses OK"
else
    echo "        *** sshd config is BROKEN -- do not disconnect ***"; fail=1
fi
port=$(sudo sshd -T 2>/dev/null | awk '/^port /{print $2}')
echo "        sshd port:           ${port:-UNKNOWN}"
[ "$port" = "1143" ] || { echo "        *** NOT 1143 ***"; fail=1; }
echo "        listening:           $(ss -lnt 2>/dev/null | grep -c ':1143') socket(s)"
echo "        authorized_keys:     $(wc -l < ~/.ssh/authorized_keys 2>/dev/null || echo 0) key(s)"
echo "        password auth:       $(sudo sshd -T 2>/dev/null | awk '/^passwordauthentication/{print $2}')"
echo "        docker:              $(systemctl is-active docker)"
echo "        free:                $(df -h / | awk 'NR==2{print $4}')"
echo
if [ "$fail" -ne 0 ]; then
    echo "  DO NOT CLOSE THIS SESSION. Fix sshd first."
    exit 1
fi
echo "  Done. Reboot when ready:  sudo reboot"
echo "  Then:  ssh -p 1143 jason@192.168.1.69   (now $NEWHOST)"
