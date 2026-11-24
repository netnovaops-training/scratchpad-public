#!/usr/bin/env bash
# relay_sbx.sh - Novashare E2E Step 2 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.
#
#   bash ~/sandbox/novashare-relay/relay_sbx.sh test     fresh copy of live + patch; test_relay.py and Step 1's
#                                                        test_devices.py in the patched image; then the tester image
#                                                        (patched image + cryptography, test-only) runs
#                                                        test_relay_e2e.py: crypto round trip, tamper, crash + resume,
#                                                        2 GB with the plaintext grep
#   bash ~/sandbox/novashare-relay/relay_sbx.sh up       a throw-away container on 127.0.0.1:18261 (fake people,
#                                                        example.invalid) for test_relay_browser.py on the PC
#   bash ~/sandbox/novashare-relay/relay_sbx.sh twin ARGS   relay_twin.py in the tester image against that container
#   bash ~/sandbox/novashare-relay/relay_sbx.sh down     stop the container, remove the images, run folder to the trash
#
# Live intake is not touched: nothing under ~/nuc-homelab changes, the live image is not retagged, the live compose
# and Caddyfile are not read for writing. The sandbox drop key is random per run, in $RUN/key (mode 600), never
# printed. The swap, when Jason says so, is SWAP-PATCH.sh with novashare_relay_patch.py + test_relay.py.
# Variables: SB (this folder), LIVE, RUN (state of the current run), W (patched copy), IMG / TIMG (sandbox and tester
# images), NAME (container), PORT.
set -euo pipefail
SB=~/sandbox/novashare-relay; LIVE=~/nuc-homelab/intake; RUN=$SB/run; W=$RUN/src
IMG=intake-sandbox:relay; TIMG=intake-sandbox:relay-tester; NAME=sbx-novashare-relay; PORT=18261

build() {
  echo "1/3 fresh copy of live ($(git -C ~/nuc-homelab log -1 --format=%h -- intake)) + patch"
  docker rm -f $NAME >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  mkdir -p "$RUN"; chmod 700 "$RUN"
  cp -a "$LIVE" "$W"; cp "$SB/test_relay.py" "$W/"
  python3 "$SB/novashare_relay_patch.py" "$W"
  echo "    differs from live: $(cd "$W" && for f in *.py; do cmp -s "$f" "$LIVE/$f" 2>/dev/null || echo -n "$f "; done)"
  docker build -q -t $IMG "$W" >/dev/null
  mkdir -p "$RUN/tester"
  cp "$SB/relay_twin.py" "$SB/test_relay_e2e.py" "$RUN/tester/"
  printf 'FROM %s\nUSER root\nRUN pip install --no-cache-dir cryptography==50.0.2\nCOPY relay_twin.py test_relay_e2e.py /app/\nUSER 1000:1000\n' $IMG > "$RUN/tester/Dockerfile"
  docker build -q -t $TIMG "$RUN/tester" >/dev/null
}

unit() {   # $1 = test file in the image; prints PASS/FAIL lines, fails the script unless the last line is ALL PASS
  local out
  out=$(docker run --rm --entrypoint python "${2:-$IMG}" "$1" ${3:-} 2>&1 || true)
  echo "$out" | grep -E "^(PASS|FAIL)|Traceback|Error" | sed 's/^/    /'
  echo "$out" | tail -1 | grep -q "ALL PASS" || { echo "$1 FAILED"; exit 1; }
  echo "    $1: ALL PASS"
}

case "${1:-}" in
test)
  build
  echo "2/3 server tests in the patched image (test_relay.py; Step 1 test_devices.py as a regression)"
  unit test_relay.py; unit test_devices.py
  echo "3/3 end to end in the tester image (crypto, tamper, crash + resume, 2 GB + grep)"
  unit test_relay_e2e.py $TIMG "--big-mb ${BIG_MB:-2048}"
  echo "SANDBOX TESTS DONE. Swap NOT done."
  ;;
up)
  docker image inspect $IMG >/dev/null 2>&1 || build
  docker rm -f $NAME >/dev/null 2>&1 || true
  [ -d "$RUN/unsorted" ] && trash-put "$RUN/unsorted"
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
    -e INTAKE_MAX_MB=10240 -e LLM_BASE=http://10.255.255.1 -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels \
    -e GOTENBERG_URL=http://10.255.255.1:3000 -e TMPDIR=/srv/unsorted/.tmp \
    -v "$RUN/drop.toml:/config/drop.toml:ro" -v "$RUN/unsorted:/srv/unsorted" $IMG >/dev/null
  for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:$PORT/healthz && break; sleep 1; done
  K=$(cat "$RUN/key")
  c1=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Drop-Key: $K" -H "X-Auth-Email: jason@example.invalid" -H "X-Auth-Level: full" http://127.0.0.1:$PORT/relay/send)
  echo "    /relay/send $c1 (want 200)"
  [ "$c1" = 200 ] || { echo "SMOKE FAILED"; exit 1; }
  echo "SANDBOX UP on 127.0.0.1:$PORT (key in $RUN/key, relay store in $RUN/unsorted/.novashare-relay). Swap NOT done."
  ;;
twin)
  shift
  mkdir -p "$RUN/twin"
  docker run --rm --network host -e SBX_KEY_FILE=/run/key -v "$RUN/key:/run/key:ro" -v "$RUN/twin:/twin" \
    -v "$RUN/unsorted:/srv/unsorted:ro" --entrypoint python $TIMG relay_twin.py "$@"
  ;;
grep)   # grep -r -a -l -F NEEDLE over the sandbox's relay store, DB (+WAL), log and staging; exit 1 = not found
  shift
  grep -r -a -l -F -e "$1" "$RUN/unsorted/.novashare-relay" "$RUN/unsorted/.atomic-staging" "$RUN/unsorted/intake.log" || exit 1
  ;;
down)
  docker rm -f $NAME >/dev/null 2>&1 || true
  docker rmi $TIMG $IMG >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  echo "sandbox down (container and images removed, run folder in the trash)"
  ;;
*) echo "usage: relay_sbx.sh test|up|twin ARGS|grep NEEDLE|down"; exit 2 ;;
esac
