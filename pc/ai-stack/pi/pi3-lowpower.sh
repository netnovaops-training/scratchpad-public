#!/bin/bash
# Cut the Pi 3's current draw so a marginal supply stops browning out.
#
# Jason cannot replace the adapter yet and wants to keep running Tor in the
# meantime. That is reasonable -- Tor is close to the ideal workload for a
# weak supply (a few MB/hour, almost no CPU) -- but the board is currently
# at throttled=0x50005: under-voltage AND throttling RIGHT NOW, clock pinned
# at 600 MHz. So instead of asking it to draw less by luck, remove the loads
# it does not need.
#
# WHAT ACTUALLY RISKS DAMAGE. Not the Pi -- under-voltage throttling is the
# firmware protecting itself, working as designed. The real hazard is SD
# CARD CORRUPTION when the voltage dips during a write. That is what
# destroyed the wipe mid-command last time. So this also makes the card
# written-to less often.
#
# EVERY CHANGE IS IN config.txt AND IS REVERSIBLE: the original is backed up
# beside it, and the card can be edited in any PC if the Pi will not boot.
set -euo pipefail

CFG=/boot/firmware/config.txt
BAK="$CFG.bak-lowpower-$(date +%Y%m%d-%H%M%S)"

echo "=== before ==="
printf '  throttled: %s\n' "$(vcgencmd get_throttled)"
printf '  arm clock: %s MHz\n' "$(( $(vcgencmd measure_clock arm | cut -d= -f2) / 1000000 ))"
printf '  core volt: %s\n' "$(vcgencmd measure_volts core | cut -d= -f2)"

sudo cp "$CFG" "$BAK"
echo "  backup: $BAK"

echo
echo "=== changes ==="

set_kv () {  # key value  -- replace in place or append under [all]
  local k="$1" v="$2"
  if grep -qE "^\s*${k}=" "$CFG"; then
    sudo sed -i "s|^\s*${k}=.*|${k}=${v}|" "$CFG"
    echo "    ~ ${k}=${v}"
  else
    echo "${k}=${v}" | sudo tee -a "$CFG" >/dev/null
    echo "    + ${k}=${v}"
  fi
}

add_line () {  # append once
  grep -qxF "$1" "$CFG" || { echo "$1" | sudo tee -a "$CFG" >/dev/null; echo "    + $1"; }
}

# 1. THE BIG ONE. arm_boost raises the Pi 3B+ to 1.4 GHz. Higher clock =
#    higher peak current, which is exactly what a marginal supply cannot
#    deliver. Tor does not care about clock speed even slightly.
set_kv arm_boost 0

# 2. Headless box: the display stack, the 3D driver and the framebuffers
#    all keep the VideoCore and the HDMI PHY powered for nobody.
set_kv display_auto_detect 0
set_kv camera_auto_detect 0
sudo sed -i 's|^\s*dtoverlay=vc4-kms-v3d|#dtoverlay=vc4-kms-v3d  # headless: GPU/HDMI off to save current|' "$CFG"
sudo sed -i 's|^\s*max_framebuffers=2|#max_framebuffers=2|' "$CFG"
echo "    ~ vc4-kms-v3d and framebuffers disabled (headless)"

# 3. Audio circuit: nothing plays anything on this board.
sudo sed -i 's|^\s*dtparam=audio=on|dtparam=audio=off|' "$CFG"
echo "    ~ dtparam=audio=off"

# 4. Bluetooth off. Wired on eth0, nothing pairs with this Pi.
#    WiFi is deliberately LEFT ENABLED: wlan0 is down but the radio stays
#    available as a way back in if ethernet ever fails. Trading a recovery
#    path for ~40 mA is a bad deal on a box in another room.
add_line "dtoverlay=disable-bt"

echo
echo "=== SD card: fewer writes, so a voltage dip is less likely to land on one ==="
# Journal commit every 60s instead of every 5s. Fewer, larger writes.
if ! grep -q 'commit=60' /etc/fstab; then
  sudo cp /etc/fstab "/etc/fstab.bak-lowpower-$(date +%H%M%S)"
  sudo sed -i 's|\(\s/\s\+ext4\s\+\)defaults|\1defaults,commit=60|' /etc/fstab
  grep -E '\s/\s' /etc/fstab | sed 's/^/    /'
else
  echo "    already set"
fi

echo
echo "=== written. Reboot required for config.txt to take effect. ==="
echo "    To revert:  sudo cp $BAK $CFG && sudo reboot"
echo "    If it will not boot: put the SD in any PC, the boot partition is"
echo "    readable as a normal FAT drive, and restore the same file."
