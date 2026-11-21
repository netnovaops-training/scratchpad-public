#!/usr/bin/env bash
# SWAP-T0962.sh - put T0962 points 1, 2 + 3 live: /intake (Telegram + mail) gets the ClamAV check route_file
# already has; /healthz answers 503 while ClamAV is down (uploads would be held) and reports clamav + held;
# quarantine folders are always new (no overwrite when the same file is held twice in a second).
#   bash ~/sandbox/t0962/SWAP-T0962.sh
# Tested 2026-11-21: test_t0962.py 13/13 (infected 422 + held, scanner down 503 + held, clean filed, scanning off
# = old, two holds -> two folders, healthz ok/down/off; scan.ping True against the live clamd, False for no host), all 7 intake suites + live-style virus checks ALL PASS on the patched copy.
# SAFE ORDER WITH OTHER SWAPS: it patches a FRESH copy of the live code at swap time (so it keeps any slice that
# went live before it), tests that copy, then installs only app.py, scan.py and the test.
# Undo (printed at the end): copy app.py + scan.py back from ~/archive/<date>/t0962/ and rebuild,
#   or retag nuc-homelab-intake:before-t0962.
# Variables: D (date), A (archive), SB (sandbox), LIVE (live intake), COMPOSE, W (fresh patched copy), IMG.
set -euo pipefail
D=$(date +%F); A=~/archive/$D/t0962; SB=~/sandbox/t0962
LIVE=~/nuc-homelab/intake; COMPOSE=~/nuc-homelab/docker-compose.yml
W=$SB/swapcopy; IMG=intake-sandbox:t0962swap

echo "1/5 fresh copy of the live code, patched + tested"
[ -d "$W" ] && trash-put "$W"
cp -a "$LIVE" "$W"; cp "$SB/test_t0962.py" "$W/"
python3 "$SB/patch_t0962.py" "$W"
docker build -q -t $IMG "$W" >/dev/null
docker run --rm --entrypoint python $IMG test_t0962.py | tail -1 | grep -q "ALL PASS" || { echo "TEST FAILED: nothing changed"; exit 1; }
echo "    test_t0962: ALL PASS"

echo "2/5 backups -> $A, image tag :before-t0962"
mkdir -p "$A"; cp -p "$LIVE/app.py" "$LIVE/scan.py" "$A/"
docker tag nuc-homelab-intake:latest nuc-homelab-intake:before-t0962

echo "3/5 install app.py scan.py test_t0962.py"
install -m 644 "$W/app.py" "$W/scan.py" "$W/test_t0962.py" "$LIVE/"

echo "4/5 rebuild + recreate intake"
docker compose -f "$COMPOSE" up -d --build intake
s=""
for i in $(seq 1 30); do
  s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true)
  [ "$s" = healthy ] && break; sleep 2
done
echo "    health: $s"

echo "5/5 check: the /intake door has the virus check"
docker exec intake grep -c "T0962" app.py scan.py
echo
echo "Then commit: cd ~/nuc-homelab && git add intake && git commit -m 'T0962: virus check on /intake, unique quarantine folders'"
echo "UNDO: cp $A/app.py $A/scan.py $LIVE/ && docker compose -f $COMPOSE up -d --build intake"
