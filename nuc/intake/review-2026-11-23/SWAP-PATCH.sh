#!/usr/bin/env bash
# SWAP-PATCH.sh - generic intake swap (2026-11-23): patch a fresh copy of live, test it, back up, install, rebuild, regress.
#   bash ~/sandbox/horizon/SWAP-PATCH.sh TAG PATCHER.py TEST.py
# TAG names the rollback image (nuc-homelab-intake:before-TAG) and the backup folder ~/archive/<date>/TAG/.
# Installs every intake/*.py the patcher changed (compared with live by content) plus TEST.py.
# Stops before touching live if the patch or its test fails. Undo line printed at the end.
# Variables: TAG, P (patcher), T (test), LIVE, W (patched copy), A (archive), CHANGED (files that differ).
set -euo pipefail
TAG=$1; P=$2; T=$3; EXTRA=${4:-}   # EXTRA: more docker run args for the test, e.g. a drop.toml mount
SB=~/sandbox/horizon; LIVE=~/nuc-homelab/intake; COMPOSE=~/nuc-homelab/docker-compose.yml
W=$SB/swap-$TAG; A=~/archive/$(date +%F)/$TAG
echo "1/4 fresh copy + patch"
[ -d "$W" ] && trash-put "$W"
cp -a "$LIVE" "$W"; cp "$T" "$W/"
python3 "$P" "$W"
echo "2/4 test in the patched image"
docker build -q -t intake-sandbox:$TAG "$W" >/dev/null
out=$(docker run --rm $EXTRA --entrypoint python intake-sandbox:$TAG "$(basename "$T")" 2>&1 || true)
echo "$out" | grep -E "^(PASS|FAIL)" | sed 's/^/    /'
echo "$out" | tail -1 | grep -q "ALL PASS" || { echo "TEST FAILED: nothing changed"; exit 1; }
CHANGED=$(cd "$W" && for f in *.py; do cmp -s "$f" "$LIVE/$f" 2>/dev/null || echo "$f"; done)
echo "3/4 backup -> $A, tag :before-$TAG, install: $(echo $CHANGED)"
mkdir -p "$A"; chmod 700 "$A"
for f in $CHANGED; do [ -f "$LIVE/$f" ] && cp -p "$LIVE/$f" "$A/"; done
docker tag nuc-homelab-intake:latest nuc-homelab-intake:before-$TAG
for f in $CHANGED; do install -m 644 "$W/$f" "$LIVE/$f"; done
echo "4/4 rebuild + health + live regression"
docker compose -f "$COMPOSE" up -d --build intake >/dev/null 2>&1
s=""
for i in $(seq 1 30); do
  s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true)
  [ "$s" = healthy ] && break; sleep 2
done
echo "    health: $s"
bash ~/sandbox/intake_regress_live.sh 2>&1 | grep -E "^FAIL|REGRESSION" | sed 's/^/    /'
echo "UNDO: cp $A/*.py $LIVE/ && docker compose -f $COMPOSE up -d --build intake   (or retag :before-$TAG)"
