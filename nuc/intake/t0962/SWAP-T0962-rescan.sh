#!/usr/bin/env bash
# SWAP-T0962-rescan.sh - put T0962 point 4 live: an hourly re-check of uploads held while the virus scanner was
# down (clean -> unsorted with a sidecar, infected -> stays held, still down -> next hour).
#   bash ~/sandbox/t0962/SWAP-T0962-rescan.sh
# Tested 2026-11-21: test_rescan.py 7/7 in a copy of the live image (scanner faked: clean, infected, down,
# infected-never-touched). Adds two files to intake (rescan_held.py, test_rescan.py), rebuilds intake once
# (a few seconds of downtime), and appends ONE line to the server user's crontab (old crontab saved first).
# Undo (printed at the end): restore the saved crontab, remove the two files, rebuild (or retag :before-rescan).
# Variables: D (date), A (archive), SB (sandbox), LIVE (live intake), COMPOSE, CRONLINE.
set -euo pipefail
D=$(date +%F); A=~/archive/$D/t0962-rescan; SB=~/sandbox/t0962
LIVE=~/nuc-homelab/intake; COMPOSE=~/nuc-homelab/docker-compose.yml
CRONLINE='23 * * * * docker exec intake python rescan_held.py >> $HOME/rescan-held.log 2>&1'

echo "1/4 backups -> $A (crontab), image tag :before-rescan"
mkdir -p "$A"; crontab -l > "$A/crontab.txt"
docker tag nuc-homelab-intake:latest nuc-homelab-intake:before-rescan

echo "2/4 install rescan_held.py + test_rescan.py, rebuild intake"
install -m 644 "$SB/rescan_held.py" "$SB/test_rescan.py" "$LIVE/"
docker compose -f "$COMPOSE" up -d --build intake
s=""
for i in $(seq 1 30); do
  s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true)
  [ "$s" = healthy ] && break; sleep 2
done
echo "    health: $s"

echo "3/4 one real run (nothing is held right now, so expect all zeros)"
docker exec intake python rescan_held.py

echo "4/4 crontab: add the hourly line (once)"
if crontab -l | grep -qF "rescan_held.py"; then echo "    already there"
else (crontab -l; echo "# T0962 point 4 (2026-11-21): re-check uploads held while the virus scanner was down"; echo "$CRONLINE") | crontab -
     echo "    added: $CRONLINE"; fi
echo
echo "Then commit: cd ~/nuc-homelab && git add intake && git commit -m 'T0962 point 4: hourly re-scan of held uploads'"
echo "UNDO: crontab $A/crontab.txt && trash-put $LIVE/rescan_held.py $LIVE/test_rescan.py && docker compose -f $COMPOSE up -d --build intake"
