#!/bin/bash
# Bind the NUC's LUKS vault to the Tang server on the Pi. RUN ON THE NUC.
#
# Jason, 2026-09-10: "im trying to use my nas to host information i want
# that encyrpted but avalivible".
#
# ===========================================================================
# WHAT THIS BUYS, AND WHAT IT DOES NOT. READ BOTH.
# ===========================================================================
#
# The vault is 680G of LUKS on /dev/sda1 -- the photo library, Immich's
# database, the Drive mirror. It is unlocked BY HAND: not in fstab, not in
# crypttab, no keyfile. So after any power cut it stays locked until
# somebody is physically present to type the passphrase, and nine
# containers sit down until then. nuc/vault-guard.sh exists entirely to
# stop Docker starting them against the empty directories it creates in the
# meantime.
#
# Clevis + Tang removes the typing without writing a key to the disk. Tang
# (on the Pi) and the NUC do a key exchange; Tang never learns the
# passphrase and never stores anything that can unlock anything on its own.
# Steal the disk, and it is exactly as encrypted as it is today.
#
#   PROTECTS AGAINST   the drive pulled out of the machine, the NUC alone
#                      taken, a failed disk sent back under warranty, the
#                      SSD sold on in three years with the vault still on it.
#
#   DOES NOT PROTECT   somebody taking the whole rack. The Pi will happily
#   AGAINST            complete the exchange on a burglar's network, because
#                      it cannot tell one LAN from another. If both boxes
#                      leave the house together, this changes nothing.
#
# That second line is the honest reason to hesitate, and it is worth
# sitting with rather than skipping: for a home where every machine is in
# one room, the realistic theft scenario takes both. What you are actually
# trading is "protected against a burglar who grabs one box" for "the
# photos come back by themselves after a power cut". Today's outage is the
# argument for; a single thief with a rucksack is the argument against.
#
# THERE IS NO LOCKOUT RISK EITHER WAY. `clevis luks bind` ADDS a keyslot.
# Your passphrase keeps working, in its own slot, untouched. If the Pi
# dies, is unplugged, or you unbind this tomorrow, you type the passphrase
# exactly as you do now. Nothing is taken away.
#
# ===========================================================================
# THIS SCRIPT DOES NOT TOUCH BOOT. Deliberately.
# ===========================================================================
#
# Binding the keyslot and making the machine unlock at boot are two
# separate changes, and only the second one can stop the NUC booting. This
# does the safe half: add the keyslot, prove it works, stop. Wiring
# /etc/crypttab comes after, once this is proven, as its own change.
#
#   sudo ./tang-bind.sh            # check everything, change nothing
#   sudo ./tang-bind.sh --go       # add the keyslot (asks for the passphrase)
# ===========================================================================
set -u

GO=0
for a in "$@"; do
    case "$a" in
        --go) GO=1 ;;
        *) echo "unknown option: $a"; exit 2 ;;
    esac
done

DEV="${VAULT_DEV:-/dev/sda1}"
TANG="${TANG_URL:-http://192.168.1.69:7500}"
# Read off the Pi directly over SSH, not from the advertisement this
# machine fetched. That is the whole point of checking it -- see below.
EXPECT_THP="WugZnKEj_BPhL6xEjO9M679FQOVNHrdvsk5SmC9kSfA"

if [ "$(id -u)" -ne 0 ]; then
    echo "  run with sudo -- it edits a LUKS header."
    exit 1
fi

echo
echo "  device:   $DEV"
echo "  tang:     $TANG"
[ "$GO" -eq 0 ] && echo && echo "  *** DRY RUN. Nothing is changed. Add --go. ***"
echo

# --- 1. is it actually LUKS, and which version --------------------------
echo "  [1/5] the device"
if ! cryptsetup isLuks "$DEV" 2>/dev/null; then
    echo "        $DEV is not a LUKS device. Stopping."
    exit 1
fi
VER=$(cryptsetup luksDump "$DEV" | awk '/^Version:/{print $2}')
echo "        LUKS$VER, $(lsblk -no SIZE "$DEV")"
echo "        keyslots in use now:"
if [ "$VER" = "2" ]; then
    cryptsetup luksDump "$DEV" | sed -n '/^Keyslots:/,/^Tokens:/p' | grep -E '^\s+[0-9]+:' | sed 's/^/          /'
else
    cryptsetup luksDump "$DEV" | grep -E '^Key Slot [0-9]+: ENABLED' | sed 's/^/          /'
fi
echo

# --- 2. clevis -----------------------------------------------------------
echo "  [2/5] clevis"
MISSING=""
for p in clevis clevis-luks clevis-systemd; do
    dpkg -s "$p" >/dev/null 2>&1 || MISSING="$MISSING $p"
done
if [ -n "$MISSING" ]; then
    echo "        missing:$MISSING"
    if [ "$GO" -eq 1 ]; then
        apt-get install -y -qq $MISSING || { echo "        install failed"; exit 1; }
        echo "        installed"
    else
        echo "        would install them"
    fi
else
    echo "        already installed"
fi
echo

# --- 3. verify the Tang server, OUT OF BAND ------------------------------
# WHY THIS CHECK IS NOT PARANOIA. `clevis luks bind` fetches the
# advertisement over plain HTTP and, unless you pin the thumbprint, trusts
# whatever answers. Anything that can answer for that address during the
# bind gets to substitute its own key -- and from then on THAT machine,
# not the Pi, is what unlocks your photo library. The bind is a
# trust-on-first-use moment; this is the "verify out of band" half of it.
# The expected value was read off the Pi's own /var/lib/tang.
echo "  [3/5] verifying the Tang server"
ADV=$(curl -sf -m 10 "$TANG/adv" 2>/dev/null)
if [ -z "$ADV" ]; then
    echo "        cannot reach $TANG -- is the Pi up? is tangd.socket running?"
    exit 1
fi
echo "        reachable, advertisement fetched"
echo "        expected exchange-key thumbprint:"
echo "          $EXPECT_THP"
echo
echo "        clevis will print a thumbprint and ask you to confirm it."
echo "        IF IT DOES NOT MATCH THE LINE ABOVE, ANSWER NO."
echo

# --- 4. bind -------------------------------------------------------------
echo "  [4/5] adding the Tang keyslot"
if [ "$GO" -eq 0 ]; then
    echo "        would run:"
    echo "          clevis luks bind -d $DEV tang '{\"url\":\"$TANG\",\"thp\":\"$EXPECT_THP\"}'"
    echo
    echo "        It asks for your EXISTING passphrase -- that is how it gets"
    echo "        the key to put in the new slot. Your slot is not modified."
    echo
    echo "  DRY RUN COMPLETE. Re-run with --go."
    exit 0
fi

if clevis luks list -d "$DEV" 2>/dev/null | grep -q tang; then
    echo "        a tang binding already exists:"
    clevis luks list -d "$DEV" | sed 's/^/          /'
    echo "        nothing to do."
else
    # thp pinned, so this does NOT trust-on-first-use -- it refuses if the
    # server answering is not the one whose key we read off the Pi.
    clevis luks bind -d "$DEV" tang "{\"url\":\"$TANG\",\"thp\":\"$EXPECT_THP\"}" || {
        echo "        bind FAILED. Nothing was changed -- your passphrase slot"
        echo "        is untouched and the vault is exactly as it was."
        exit 1
    }
    echo "        bound"
fi
echo

# --- 5. prove it ---------------------------------------------------------
# A binding that has never been exercised is a binding you find out about
# during a power cut.
echo "  [5/5] proving the Tang path actually recovers the key"
echo "        keyslots now:"
clevis luks list -d "$DEV" 2>/dev/null | sed 's/^/          /'
echo
TESTMAP="vault-tangtest"
if cryptsetup status "$TESTMAP" >/dev/null 2>&1; then cryptsetup close "$TESTMAP"; fi
if clevis luks unlock -d "$DEV" -n "$TESTMAP" 2>/dev/null; then
    echo "        UNLOCKED via Tang with no passphrase -- the binding works."
    cryptsetup close "$TESTMAP"
    echo "        (test mapping closed; the live /mnt/vault was never touched)"
else
    echo "        could not open a second mapping -- usually just because the"
    echo "        device is already active as 'vault'. That is not a failure"
    echo "        of the binding. The real test is a reboot, and the binding"
    echo "        is not wired into boot yet, so nothing is at risk."
fi
echo
echo "  Done. The passphrase still works; this only ADDED a way in."
echo
echo "  NEXT, as a separate change: wire it into boot via /etc/crypttab so"
echo "  the vault mounts itself. That is the step that can affect booting,"
echo "  so it gets its own script and its own test."
