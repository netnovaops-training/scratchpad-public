#!/usr/bin/env bash
# SWAP-T0958.sh - put T0958 live: the drop reads the text in images (Tesseract) before the sorter decides.
#   bash ~/sandbox/t0958/SWAP-T0958.sh
# Tested 2026-11-21: test_t0958.py 10/10 (real Tesseract reads a drawn receipt; the text reaches the model
# labelled as OCR data with no --- fence; receipt photo -> document; photo without text -> unknown, model not
# asked; book/comic answers for an image -> unknown; model down -> unsorted; size cap), and the full intake
# regression ALL PASS on the patched copy. The image grows from about 255 MB to about 401 MB (Tesseract + libs).
# It patches a FRESH copy of the live code at swap time, tests it, then installs Dockerfile, classify.py and
# the test. Undo (printed at the end): copy both back from ~/archive/<date>/t0958/ and rebuild, or retag
# nuc-homelab-intake:before-t0958.
# Variables: D (date), A (archive), SB (sandbox), LIVE (live intake), COMPOSE, W (fresh patched copy), IMG.
set -euo pipefail
D=$(date +%F); A=~/archive/$D/t0958; SB=~/sandbox/t0958
LIVE=~/nuc-homelab/intake; COMPOSE=~/nuc-homelab/docker-compose.yml
W=$SB/swapcopy; IMG=intake-sandbox:t0958swap

echo "1/5 fresh copy of the live code, patched + tested"
[ -d "$W" ] && trash-put "$W"
cp -a "$LIVE" "$W"; cp "$SB/test_t0958.py" "$W/"
python3 "$SB/patch_t0958.py" "$W"
docker build -q -t $IMG "$W" >/dev/null
docker run --rm -v "$HOME/.config/drop/drop.toml:/config/drop.toml:ro" -e PENDING_ROOT=/srv/unsorted/.pending \
  --entrypoint python $IMG test_t0958.py | tail -1 | grep -q "ALL PASS" || { echo "TEST FAILED: nothing changed"; exit 1; }
echo "    test_t0958: ALL PASS"

echo "2/5 backups -> $A, image tag :before-t0958"
mkdir -p "$A"; cp -p "$LIVE/Dockerfile" "$LIVE/classify.py" "$A/"
docker tag nuc-homelab-intake:latest nuc-homelab-intake:before-t0958

echo "3/5 install Dockerfile classify.py test_t0958.py"
install -m 644 "$W/Dockerfile" "$W/classify.py" "$W/test_t0958.py" "$LIVE/"

echo "4/5 rebuild + recreate intake (the first build downloads Tesseract, a minute or two)"
docker compose -f "$COMPOSE" up -d --build intake
s=""
for i in $(seq 1 45); do
  s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true)
  [ "$s" = healthy ] && break; sleep 2
done
echo "    health: $s"

echo "5/5 check: tesseract is in the live image"
docker exec intake tesseract --version 2>&1 | head -1
echo
echo "Then: send a photo of a receipt from the phone (it should go to Paperless), and commit:"
echo "  cd ~/nuc-homelab && git add intake && git commit -m 'T0958: OCR for images before sorting'"
echo "UNDO: cp $A/Dockerfile $A/classify.py $LIVE/ && docker compose -f $COMPOSE up -d --build intake"
