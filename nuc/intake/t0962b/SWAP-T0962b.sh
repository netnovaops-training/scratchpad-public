#!/usr/bin/env bash
# SWAP-T0962b.sh - T0962 point 5 (quarantine notes keep the person id, not the email; Jason 11-21: name only)
# + rescan follow-ups (one run at a time, failed folders counted and exit 1, held_count skips unreadable folders).
#   bash ~/sandbox/t0962b/SWAP-T0962b.sh
# Tested 2026-11-21: test_t0962b.py 9/9, test_t0962.py and test_rescan.py still ALL PASS on the patched copy.
# Patches a FRESH copy of the live code at swap time, runs the three tests, installs app.py, scan.py,
# rescan_held.py and the test. Undo (printed at the end): copy back from ~/archive/<date>/t0962b/ and rebuild,
# or retag nuc-homelab-intake:before-t0962b.
# Variables: D (date), A (archive), SB (sandbox), LIVE (live intake), COMPOSE, W (fresh patched copy), IMG, t.
set -euo pipefail
D=$(date +%F); A=~/archive/$D/t0962b; SB=~/sandbox/t0962b
LIVE=~/nuc-homelab/intake; COMPOSE=~/nuc-homelab/docker-compose.yml
W=$SB/swapcopy; IMG=intake-sandbox:t0962bswap

echo "1/5 fresh copy of the live code, patched + tested"
[ -d "$W" ] && trash-put "$W"
cp -a "$LIVE" "$W"; cp "$SB/test_t0962b.py" "$W/"
python3 "$SB/patch_t0962b.py" "$W"
docker build -q -t $IMG "$W" >/dev/null
for t in test_t0962b.py test_t0962.py test_rescan.py; do
  docker run --rm --entrypoint python $IMG $t | tail -1 | grep -q "ALL PASS" || { echo "TEST FAILED ($t): nothing changed"; exit 1; }
  echo "    $t: ALL PASS"
done

echo "2/5 backups -> $A, image tag :before-t0962b"
mkdir -p "$A"; cp -p "$LIVE/app.py" "$LIVE/scan.py" "$LIVE/rescan_held.py" "$A/"
docker tag nuc-homelab-intake:latest nuc-homelab-intake:before-t0962b

echo "3/5 install app.py scan.py rescan_held.py test_t0962b.py"
install -m 644 "$W/app.py" "$W/scan.py" "$W/rescan_held.py" "$W/test_t0962b.py" "$LIVE/"

echo "4/5 rebuild + recreate intake"
docker compose -f "$COMPOSE" up -d --build intake
s=""
for i in $(seq 1 30); do
  s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true)
  [ "$s" = healthy ] && break; sleep 2
done
echo "    health: $s"

echo "5/5 check: one real rescan run"
docker exec intake python rescan_held.py
echo "UNDO: cp $A/app.py $A/scan.py $A/rescan_held.py $LIVE/ && docker compose -f $COMPOSE up -d --build intake"
