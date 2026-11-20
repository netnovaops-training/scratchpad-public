#!/usr/bin/env bash
# Set the desktop's MAC in whichever .env is actually in use.
#
#     sudo bash fix-mac.sh 00-00-5e-00-53-02
#
# Replaces the line if present, appends it if not. A plain `sed -i s|...|` only
# does the first, which is why setting it can silently do nothing on a .env that
# never had the key -- exactly what happened when the folder was recreated.
set -Eeuo pipefail
MAC="${1:-}"
[[ -n "$MAC" ]] || { echo "usage: sudo bash fix-mac.sh 00-00-5e-00-53-03"; exit 1; }
[[ "$MAC" =~ ^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$ ]] || {
  echo "That does not look like a MAC address: $MAC"; exit 1; }

for D in /var/www/Mom /var/www/mom; do
  F="$D/.env"
  [[ -f "$F" ]] || continue
  if grep -q '^MOM_DESKTOP_MAC=' "$F"; then
    sed -i "s|^MOM_DESKTOP_MAC=.*|MOM_DESKTOP_MAC=$MAC|" "$F"
  else
    printf 'MOM_DESKTOP_MAC=%s\n' "$MAC" >> "$F"
  fi
  # Same treatment for the two settings that travel with it.
  grep -q '^MOM_GPU_MAC=' "$F" && sed -i "s|^MOM_GPU_MAC=.*|MOM_GPU_MAC=$MAC|" "$F" || true
  echo "  set in $F ->  $(grep '^MOM_DESKTOP_MAC=' "$F")"
  chmod 600 "$F"
  ( cd "$D" && docker compose restart >/dev/null 2>&1 ) && echo "  restarted the container" || true
done

echo
echo "  Confirm the app can see it:"
docker exec momhelp python -c "
from app import core
print('  MAC the app will use :', core.DESKTOP_MAC or '(still empty)')
print('  broadcast            :', core.DESKTOP_BROADCAST)
print('  magic packet builds  :', len(core.build_magic_packet(core.DESKTOP_MAC)) == 102)
" 2>/dev/null || echo "  (container not running)"
