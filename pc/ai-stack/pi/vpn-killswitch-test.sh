#!/bin/bash
# PROVE the kill switch. Not "the firewall rules look right" -- actually
# drop the tunnel and confirm the lane goes dark instead of falling back
# to the household connection.
#
# The previous attempt at this test was invalid: it brought down `wg0`,
# which does not exist on this box. The interface is tun0. A test that
# targets the wrong interface passes for the wrong reason.
set -uo pipefail

HOUSEHOLD=$(wget -qO- -T 10 https://api.ipify.org 2>/dev/null)
echo "household address: $HOUSEHOLD"
echo

echo "=== 1. baseline: tunnel UP ==="
printf '    qbittorrent sees itself as: '
docker exec qbittorrent sh -c 'wget -qO- -T 20 https://api.ipify.org 2>/dev/null || echo FAILED'
echo

echo "=== 2. drop the tunnel ==="
docker exec gluetun ip link set tun0 down
echo "    tun0 down"
sleep 3

echo
echo "=== 3. THE TEST: what does the torrent client reach now? ==="
RESULT=$(docker exec qbittorrent sh -c \
  'wget -qO- -T 20 https://api.ipify.org 2>/dev/null || echo BLOCKED')
echo "    result: $RESULT"
echo

if [ "$RESULT" = "BLOCKED" ] || [ -z "$RESULT" ]; then
  echo "    PASS -- traffic STOPPED. That is a kill switch."
elif [ "$RESULT" = "$HOUSEHOLD" ]; then
  echo "    FAIL -- it fell back to the HOUSEHOLD ADDRESS. Still leaking."
else
  echo "    UNCLEAR -- got $RESULT, neither blocked nor the household IP."
fi

echo
echo "=== 4. restore ==="
docker exec gluetun ip link set tun0 up 2>/dev/null
sleep 5
# gluetun's own health check also restarts the tunnel; give it a moment
# and confirm the lane is back rather than assuming it.
for i in $(seq 1 12); do
  R=$(docker exec qbittorrent sh -c \
      'wget -qO- -T 10 https://api.ipify.org 2>/dev/null || echo -')
  if [ "$R" != "-" ] && [ -n "$R" ] && [ "$R" != "$HOUSEHOLD" ]; then
    echo "    restored, presenting: $R"
    exit 0
  fi
  sleep 10
done
echo "    did not come back on its own -- restarting gluetun"
cd ~/stack && docker compose -f compose.yml -f compose-download.yml \
  up -d --force-recreate gluetun qbittorrent metube firefox 2>&1 | tail -4
