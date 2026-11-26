#!/usr/bin/env bash
# devices_sbx.sh - Novashare E2E Step 1 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.
#
#   bash ~/sandbox/novashare-devices/devices_sbx.sh up     fresh copy of live + patch, build, test_devices.py in the
#                                                          image, then a throw-away container on 127.0.0.1:18260
#                                                          with fake people (example.invalid) for the browser test
#   bash ~/sandbox/novashare-devices/devices_sbx.sh down   stop and remove that container (run folder to the trash)
#
# The browser test (test_devices_browser.py) runs on the PC through `ssh -L 18260:127.0.0.1:18260`. The sandbox
# drop key is random per run, kept in $RUN/key (mode 600) and never printed. Live intake is not touched: no file
# under ~/nuc-homelab changes and the live image is not retagged. The swap, when Jason says so, is SWAP-PATCH.sh.
# Variables: SB (this folder), LIVE, RUN (state of the current run, in $SB/run), W (patched copy), IMG, NAME, PORT.
set -euo pipefail
SB=~/sandbox/novashare-devices; LIVE=~/nuc-homelab/intake; RUN=$SB/run; W=$RUN/src
IMG=intake-sandbox:novashare-devices; NAME=sbx-novashare-devices; PORT=18260

if [ "${1:-}" = down ]; then
  docker rm -f $NAME >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  echo "sandbox down (run folder in the trash)"; exit 0
fi

echo "1/4 fresh copy of live ($(git -C ~/nuc-homelab log -1 --format=%h -- intake)) + patch"
docker rm -f $NAME >/dev/null 2>&1 || true
[ -d "$RUN" ] && trash-put "$RUN"
mkdir -p "$RUN"; chmod 700 "$RUN"
cp -a "$LIVE" "$W"; cp "$SB/test_devices.py" "$W/"
python3 "$SB/novashare_devices_patch.py" "$W"
CHANGED=$(cd "$W" && for f in *.py; do cmp -s "$f" "$LIVE/$f" 2>/dev/null || echo "$f"; done)
echo "    differs from live: $(echo $CHANGED)"

echo "2/4 build + test_devices.py in the patched image"
docker build -q -t $IMG "$W" >/dev/null
out=$(docker run --rm --entrypoint python $IMG test_devices.py 2>&1 || true)
echo "$out" | grep -E "^(PASS|FAIL)|Traceback|Error" | sed 's/^/    /'
echo "$out" | tail -1 | grep -q "ALL PASS" || { echo "UNIT TESTS FAILED"; exit 1; }
echo "    UNIT: ALL PASS"

echo "3/4 throw-away container on 127.0.0.1:$PORT (fake people, temp folders)"
mkdir -p "$RUN/unsorted"
umask 077; openssl rand -hex 24 > "$RUN/key"; umask 022
cat > "$RUN/drop.toml" <<'EOF'
[brand]
name = "Novashare"
short_name = "Novashare"
theme = "horizon"
[[person]]
id = "jason"
name = "Jason"
emails = ["jason@example.invalid"]
[[person]]
id = "casey"
name = "Casey"
emails = ["casey@example.invalid"]
EOF
docker run -d --name $NAME -p 127.0.0.1:$PORT:8100 \
  -e INTAKE_KEY="$(cat "$RUN/key")" -e INTAKE_TOKEN=sbx-token -e INTAKE_ALLOWED_PEERS=127.0.0.1,172.17.0.1 \
  -e LLM_BASE=http://10.255.255.1 -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels -e GOTENBERG_URL=http://10.255.255.1:3000 \
  -e TMPDIR=/srv/unsorted/.tmp -v "$RUN/drop.toml:/config/drop.toml:ro" -v "$RUN/unsorted:/srv/unsorted" $IMG >/dev/null
for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:$PORT/healthz && break; sleep 1; done

echo "4/4 smoke: page and guard"
K=$(cat "$RUN/key")
c1=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Drop-Key: $K" -H "X-Auth-Email: jason@example.invalid" -H "X-Auth-Level: full" http://127.0.0.1:$PORT/devices)
c2=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Drop-Key: $K" -H "X-Auth-Email: jason@example.invalid" -H "X-Auth-Level: full" http://127.0.0.1:$PORT/devices/api/mine)
echo "    /devices $c1 (want 200), /devices/api/mine without Sec-Fetch-Site $c2 (want 403)"
[ "$c1" = 200 ] && [ "$c2" = 403 ] || { echo "SMOKE FAILED"; exit 1; }
echo "SANDBOX UP on 127.0.0.1:$PORT (key in $RUN/key). Swap NOT done."
