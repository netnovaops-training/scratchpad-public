#!/usr/bin/env bash
# push_sbx.sh - Novashare E2E Step 4 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.
#
#   bash ~/sandbox/novashare-push/push_sbx.sh test   fresh copy of live + patch; test_push.py, then Step 3's test_receive.py,
#                                                    Step 2's test_relay.py and Step 1's test_devices.py as regressions;
#                                                    pip-audit of the patched requirements
#   bash ~/sandbox/novashare-push/push_sbx.sh e2e    real uvicorn (lifespan -> push worker) + a mock push service, each in
#                                                    its own container on a private docker network (no host ports);
#                                                    test_push_e2e.py runs inside the intake container; then the log is
#                                                    checked for names, emails, endpoint paths and the key
#   bash ~/sandbox/novashare-push/push_sbx.sh down   stop the containers, remove the images and the network, run folder to trash
#
# Live intake is not touched: nothing under ~/nuc-homelab changes and the live image is not retagged. The sandbox VAPID
# key is a THROWAWAY made per run in $RUN/vapid (mode 600), never printed (the real one is in Proton Pass).
# Variables: SB (this folder), LIVE, RUN (state of the current run), W (patched copy), IMG, NAME, MOCK, NET.
set -euo pipefail
SB=~/sandbox/novashare-push; LIVE=~/nuc-homelab/intake; RUN=$SB/run; W=$RUN/src
IMG=intake-sandbox:push; NAME=sbx-novashare-push; MOCK=sbx-novashare-push-mock; NET=sbx-novashare-push-net

build() {
  echo "1/2 fresh copy of live ($(git -C ~/nuc-homelab log -1 --format=%h -- intake)) + patch"
  docker rm -f $NAME $MOCK >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  mkdir -p "$RUN"; chmod 700 "$RUN"
  cp -a "$LIVE" "$W"; cp "$SB/test_push.py" "$SB/test_push_e2e.py" "$SB/mock_push.py" "$W/"
  python3 "$SB/novashare_push_patch.py" "$W"
  echo "    differs from live: $(cd "$W" && for f in *.py requirements.txt; do cmp -s "$f" "$LIVE/$f" 2>/dev/null || echo -n "$f "; done)"
  docker build -q -t $IMG "$W" >/dev/null
}

unit() {   # $1 = test file in the image; fails the script unless its last line is ALL PASS
  local out
  out=$(docker run --rm --entrypoint python $IMG "$1" 2>&1 || true)
  echo "$out" | grep -E "^(FAIL)|Traceback|Error" | sed 's/^/    /' || true
  echo "$out" | tail -1 | grep -q "ALL PASS" || { echo "$out" | tail -25 | sed 's/^/    /'; echo "$1 FAILED"; exit 1; }
  echo "    $1: ALL PASS ($(echo "$out" | grep -c '^PASS') checks)"
}

case "${1:-}" in
test)
  build
  echo "2/2 server tests in the patched image"
  unit test_push.py; unit test_receive.py; unit test_relay.py; unit test_devices.py
  echo "    pip-audit of the patched requirements.txt:"
  docker run --rm --user 0 -v "$W/requirements.txt:/r.txt:ro" --entrypoint sh $IMG -c \
    'pip install -q --no-cache-dir --root-user-action=ignore pip-audit >/dev/null 2>&1; pip-audit -r /r.txt --progress-spinner off 2>&1 | tail -3' | sed 's/^/    /'
  echo "SANDBOX TESTS DONE. Swap NOT done."
  ;;
e2e)
  docker image inspect $IMG >/dev/null 2>&1 || build
  docker rm -f $NAME $MOCK >/dev/null 2>&1 || true
  docker network inspect $NET >/dev/null 2>&1 || docker network create --internal $NET >/dev/null
  [ -d "$RUN/unsorted" ] && trash-put "$RUN/unsorted"
  mkdir -p "$RUN/unsorted"
  umask 077
  openssl rand -hex 24 > "$RUN/key"
  docker run --rm --entrypoint python $IMG -c 'import base64
from cryptography.hazmat.primitives.asymmetric import ec
k = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value.to_bytes(32, "big")
print(base64.urlsafe_b64encode(k).rstrip(b"=").decode())' | tr -cd 'A-Za-z0-9_-' > "$RUN/vapid"
  umask 022
  echo "    throwaway VAPID key: length $(wc -c < "$RUN/vapid") (want 43), not shown"
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
  docker run -d --name $MOCK --network $NET --entrypoint python $IMG mock_push.py 8099 >/dev/null
  docker run -d --name $NAME --network $NET \
    -e INTAKE_KEY="$(cat "$RUN/key")" -e INTAKE_TOKEN=sbx-token -e INTAKE_ALLOWED_PEERS=127.0.0.1 \
    -e INTAKE_MAX_MB=64 -e LLM_BASE=http://10.255.255.1 -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels \
    -e GOTENBERG_URL=http://10.255.255.1:3000 -e TMPDIR=/srv/unsorted/.tmp \
    -e VAPID_PRIVATE_KEY="$(cat "$RUN/vapid")" -e INTAKE_PUSH_TEST_ORIGINS=http://$MOCK:8099 \
    -v "$RUN/drop.toml:/config/drop.toml:ro" -v "$RUN/unsorted:/srv/unsorted" $IMG >/dev/null
  for i in $(seq 1 30); do docker exec $NAME python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8100/healthz', timeout=3)" 2>/dev/null && break; sleep 1; done
  docker logs $NAME 2>&1 | grep -q "push on" && echo "    server start: push worker on" || { echo "    push worker NOT on:"; docker logs $NAME 2>&1 | grep -i push | tail -3; exit 1; }
  out=$(docker exec $NAME python test_push_e2e.py http://$MOCK:8099 2>&1 || true)
  echo "$out" | sed 's/^/    /'
  echo "$out" | tail -1 | grep -q "ALL PASS" || { echo "E2E FAILED"; exit 1; }
  log=$(docker logs $NAME 2>&1)
  n=$(printf '%s' "$log" | grep -c -F -e "example.invalid" -e "Kitchen" -e "/p/" -e "Jason" -e "Casey" || true)
  k=$(printf '%s' "$log" | grep -c -F -f "$RUN/vapid" || true)
  echo "    server log: $(printf '%s\n' "$log" | grep -c '^.*push ') push lines; lines with names/emails/labels/endpoint paths: $n; with the key: $k"
  [ "$n" = 0 ] && [ "$k" = 0 ] || { echo "LOG LEAK"; exit 1; }
  printf '%s\n' "$log" | grep -E "push (sent|gone|retry|subscribed)" | head -6 | sed 's/^/    /'
  echo "SANDBOX E2E DONE. Swap NOT done."
  ;;
down)
  docker rm -f $NAME $MOCK >/dev/null 2>&1 || true
  docker network rm $NET >/dev/null 2>&1 || true
  docker rmi $IMG >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  echo "sandbox down (containers, network and image removed, run folder in the trash)"
  ;;
*) echo "usage: push_sbx.sh test|e2e|down"; exit 2 ;;
esac
