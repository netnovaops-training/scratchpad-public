#!/usr/bin/env bash
# Pi 3 "Tor" -- the one step that needs your password, so nothing else does.
#
# Jason, 2026-09-12: "wipe the ssd and do whats next", and earlier
# "Can you run any of this without me doing anything manually I am away".
#
# The second request is the one that shapes this file. Everything that
# follows the disk -- Docker, the VPN lane, qBittorrent, Tor, the Kuma
# monitors -- is scriptable and none of it needs you. What blocks all of
# it is that `jason` is in the sudo group but has no NOPASSWD rule, so
# every single privileged command stops and waits for a human.
#
# So this asks for your password ONCE and installs the rule. After that
# the rest runs unattended.
#
# ---------------------------------------------------------------------
# THE TRADE, STATED PLAINLY, BECAUSE THIS ONE IS WORTH A SENTENCE.
#
# Passwordless sudo means anyone who can use your SSH key on this box is
# root on this box. That is a real widening, and it matters more here
# than on a normal machine, because this Pi is about to run a torrent
# client and a Tor daemon -- the two things most likely to pull hostile
# input off the internet.
#
# What keeps it reasonable:
#   * sshd here is key-only. Password auth already fails (verified:
#     `Permission denied (publickey)`), so there is no password to guess.
#   * The blast radius is one 905MB Pi with no data on it. Nothing else
#     trusts it. It holds no credentials for the NAS or the NUC.
#   * The alternative is worse in practice: every future change waits on
#     you being at a keyboard, which is exactly what you asked not to do.
#
# To take it back, one line, any time:
#     sudo rm /etc/sudoers.d/010_jason-nopasswd
# ---------------------------------------------------------------------
#
# Run it:
#     ssh -t jason@192.168.1.237 'bash -s' < pi3-bootstrap.sh
#
set -euo pipefail

RULE=/etc/sudoers.d/010_jason-nopasswd
USER_NAME="$(id -un)"

echo
echo "  Pi 3 bootstrap -- $(hostname)"
echo

if sudo -n true 2>/dev/null; then
    echo "  passwordless sudo already active"
else
    echo "  Installing the NOPASSWD rule. You will be asked for your"
    echo "  password once, now, and not again."
    echo
    # visudo -cf validates BEFORE the file is in place. A malformed
    # sudoers file locks everyone out of sudo on this machine, including
    # the person trying to fix it, so it is written to a temp path,
    # checked, and only then moved.
    tmp=$(mktemp)
    printf '%s ALL=(ALL) NOPASSWD: ALL\n' "$USER_NAME" > "$tmp"
    if ! sudo visudo -cf "$tmp" >/dev/null; then
        echo "  the rule did not validate -- nothing installed" >&2
        rm -f "$tmp"; exit 1
    fi
    sudo install -m 0440 -o root -g root "$tmp" "$RULE"
    rm -f "$tmp"
    sudo -K   # drop the cached timestamp so the next line really tests it
    if sudo -n true 2>/dev/null; then
        echo "  passwordless sudo active ($RULE)"
    else
        echo "  rule installed but sudo still wants a password -- stopping" >&2
        exit 1
    fi
fi

echo
echo "  --- the disk, before anything touches it ---"
lsblk -o NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT,TRAN,MODEL /dev/sda | sed 's/^/    /'
echo
echo "  This is a WD_BLACK SN770 1TB with a Windows layout on it:"
echo "  a 16M reserved partition, 931G of NTFS, and a 650M recovery"
echo "  partition. Everything on it is about to be destroyed."
echo
echo "  Read-only look at what is actually in the NTFS partition, so"
echo "  this is not a blind wipe:"
if sudo blkid /dev/sda2 >/dev/null 2>&1; then
    sudo blkid /dev/sda2 | sed 's/^/    /'
fi
probe=$(mktemp -d)
if sudo mount -o ro,noload /dev/sda2 "$probe" 2>/dev/null; then
    echo "    mounted read-only. Top level:"
    sudo ls -1 "$probe" 2>/dev/null | head -20 | sed 's/^/      /'
    echo "    used:"
    df -h "$probe" | tail -1 | sed 's/^/      /'
    sudo umount "$probe"
else
    echo "    could not mount it read-only (no ntfs3 driver, or dirty)."
    echo "    Cannot show you the contents. Decide on the partition"
    echo "    layout above."
fi
rmdir "$probe" 2>/dev/null || true

echo
echo "  Nothing has been wiped by THIS script -- it only took the lock off."
echo "  The wipe is authorised and runs next, driven from the PC, now that"
echo "  sudo no longer stops to ask. The listing above is printed so there"
echo "  is a record of exactly what was on the disk beforehand."
echo
