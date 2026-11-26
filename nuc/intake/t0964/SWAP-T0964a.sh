#!/usr/bin/env bash
# SWAP-T0964a.sh - T0964 tidy-ups outside classify.py: the video card shows the real upload limit
# (INTAKE_MAX_MB) and /healthz "held" counts only folders that still hold a file.
#   bash ~/sandbox/t0964/SWAP-T0964a.sh
# Tested 2026-11-21: card text 10240 MB -> "Up to 10 GB", 2048 -> "Up to 2 GB"; held_count with one held + one
# released folder = 1. Patches a FRESH copy of the live code at swap time, checks it, installs portal.py + scan.py.
# Undo (printed at the end): copy both back from ~/archive/<date>/t0964a/ and rebuild, or retag :before-t0964a.
# Variables: D (date), A (archive), SB (sandbox), LIVE (live intake), COMPOSE, W (fresh patched copy), IMG.
set -euo pipefail
D=$(date +%F); A=~/archive/$D/t0964a; SB=~/sandbox/t0964
LIVE=~/nuc-homelab/intake; COMPOSE=~/nuc-homelab/docker-compose.yml
W=$SB/swapcopy; IMG=intake-sandbox:t0964swap

echo "1/5 fresh copy of the live code, patched + checked"
[ -d "$W" ] && trash-put "$W"
cp -a "$LIVE" "$W"
python3 "$SB/patch_t0964a.py" "$W"
docker build -q -t $IMG "$W" >/dev/null
docker run --rm -v "$HOME/.config/drop/drop.toml:/config/drop.toml:ro" -e INTAKE_MAX_MB=10240 --entrypoint python $IMG \
  -c "import portal; assert 'Up to 10 GB' in portal.page('x@example.com', None)" || { echo "CHECK FAILED: nothing changed"; exit 1; }
docker run --rm -e INTAKE_QUARANTINE=/tmp/q --entrypoint python $IMG -c "
import pathlib, scan
q = pathlib.Path('/tmp/q'); (q / 'a').mkdir(parents=True); (q / 'a' / 'f.held').write_text('x'); (q / 'b').mkdir()
assert scan.held_count() == 1" || { echo "CHECK FAILED: nothing changed"; exit 1; }
echo "    checks: ok"

echo "2/5 backups -> $A, image tag :before-t0964a"
mkdir -p "$A"; cp -p "$LIVE/portal.py" "$LIVE/scan.py" "$A/"
docker tag nuc-homelab-intake:latest nuc-homelab-intake:before-t0964a

echo "3/5 install portal.py scan.py"
install -m 644 "$W/portal.py" "$W/scan.py" "$LIVE/"

echo "4/5 rebuild + recreate intake"
docker compose -f "$COMPOSE" up -d --build intake
s=""
for i in $(seq 1 30); do
  s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true)
  [ "$s" = healthy ] && break; sleep 2
done
echo "    health: $s"

echo "5/5 done"
echo "Then commit: cd ~/nuc-homelab && git add intake && git commit -m 'T0964a: real upload limit on the video card, held_count'"
echo "UNDO: cp $A/portal.py $A/scan.py $LIVE/ && docker compose -f $COMPOSE up -d --build intake"
