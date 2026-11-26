#!/usr/bin/env bash
# SWAP-T0964b.sh - T0964 rest: Word/LibreOffice text, .csv, the document note, tabs in OCR text, MAX_GB, stale
# comments (classify.py portal.py scan.py share.py app.py test_t0958.py).
#   bash ~/sandbox/t0964b/SWAP-T0964b.sh
# Tested 2026-11-22: test_t0964b.py 15/15, test_t0958 + test_libraries ALL PASS, full regression ALL PASS on the
# patched copy. Patches a FRESH copy of live at swap time, runs the three tests, then installs.
# Undo (printed at the end): copy back from ~/archive/<date>/t0964b/ and rebuild, or retag :before-t0964b.
# Variables: D, A (archive), SB (sandbox), LIVE, COMPOSE, W (patched copy), IMG, M (test mounts), FILES.
set -euo pipefail
D=$(date +%F); A=~/archive/$D/t0964b; SB=~/sandbox/t0964b
LIVE=~/nuc-homelab/intake; COMPOSE=~/nuc-homelab/docker-compose.yml
W=$SB/swapcopy; IMG=intake-sandbox:t0964bswap
FILES="classify.py portal.py scan.py share.py app.py test_t0958.py"
M="-v $HOME/.config/drop/drop.toml:/config/drop.toml:ro -e PENDING_ROOT=/srv/unsorted/.pending -e LLM_BASE=http://x -e AUTH_LEVELS_URL=http://x -e GOTENBERG_URL=http://x"

echo "1/5 fresh copy of live, patched + tested"
[ -d "$W" ] && trash-put "$W"
cp -a "$LIVE" "$W"; cp "$SB/test_t0964b.py" "$W/"
python3 "$SB/patch_t0964b.py" "$W"
docker build -q -t $IMG "$W" >/dev/null
for t in test_t0964b.py test_t0958.py test_libraries.py; do
  docker run --rm $M --entrypoint python $IMG $t 2>/dev/null | tail -1 | grep -q "ALL PASS" || { echo "TEST FAILED ($t): nothing changed"; exit 1; }
  echo "    $t: ALL PASS"
done

echo "2/5 backups -> $A, image tag :before-t0964b"
mkdir -p "$A"; for f in $FILES; do cp -p "$LIVE/$f" "$A/"; done
docker tag nuc-homelab-intake:latest nuc-homelab-intake:before-t0964b

echo "3/5 install"
for f in $FILES test_t0964b.py; do install -m 644 "$W/$f" "$LIVE/$f"; done

echo "4/5 rebuild + recreate intake"
docker compose -f "$COMPOSE" up -d --build intake
s=""
for i in $(seq 1 30); do
  s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true)
  [ "$s" = healthy ] && break; sleep 2
done
echo "    health: $s"
echo "5/5 done"
echo "UNDO: cp $(for f in $FILES; do printf '%s ' "$A/$f"; done)$LIVE/ && docker compose -f $COMPOSE up -d --build intake"
