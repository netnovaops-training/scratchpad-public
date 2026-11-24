#!/usr/bin/env bash
# sweep_sbx.sh - Novashare E2E Step 6 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.
#
#   bash ~/sandbox/novashare-sweep/sweep_sbx.sh test        fresh copy of live + patch; test_sweep.py, then the Step 1-5
#                                                           tests and the theme/UI tests as regressions; node --check
#   bash ~/sandbox/novashare-sweep/sweep_sbx.sh up          a throw-away container on 127.0.0.1:18292 (fake people,
#                                                           example.invalid; per-person relay cap 40 MB, first sweep 5 s)
#                                                           behind a Caddy front door that signs in Jason on :18293 and
#                                                           Casey on :18294, for test_sweep_browser.py on the PC
#   bash ~/sandbox/novashare-sweep/sweep_sbx.sh relay       the transfer ids (folders) in the relay store
#   bash ~/sandbox/novashare-sweep/sweep_sbx.sh log WORDS   how many server log lines contain WORDS
#   bash ~/sandbox/novashare-sweep/sweep_sbx.sh grep NEEDLE grep the sandbox data, relay store, DB and log (exit 1 = none)
#   bash ~/sandbox/novashare-sweep/sweep_sbx.sh down        stop the containers, remove the image, run folder to trash
#
# Live intake is not touched: nothing under ~/nuc-homelab changes and the live image is not retagged. The sandbox
# drop key is random per run, in $RUN/key (mode 600), never printed. The swap, when Jason says so, is
#   bash ~/sandbox/horizon/SWAP-PATCH.sh novashare-sweep ~/sandbox/novashare-sweep/novashare_sweep_patch.py \
#        ~/sandbox/novashare-sweep/test_sweep.py
# Variables: SB (this folder), LIVE, RUN (state of the current run), W (patched copy), IMG, NAME (container), PORT.
set -euo pipefail
SB=~/sandbox/novashare-sweep; LIVE=~/nuc-homelab/intake; RUN=$SB/run; W=$RUN/src
IMG=intake-sandbox:sweep; NAME=sbx-novashare-sweep; PORT=18292
PROXY=sbx-novashare-sweep-proxy; PJ=18293; PZ=18294   # Caddy as the sign-in front door: Jason on PJ, Casey on PZ

build() {
  echo "1/2 fresh copy of live ($(git -C ~/nuc-homelab log -1 --format=%h -- intake)) + patch"
  docker rm -f $PROXY $NAME >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  mkdir -p "$RUN"; chmod 700 "$RUN"
  cp -a "$LIVE" "$W"; cp "$SB/test_sweep.py" "$W/"
  python3 "$SB/novashare_sweep_patch.py" "$W"
  echo "    differs from live: $(cd "$W" && for f in *.py; do cmp -s "$f" "$LIVE/$f" 2>/dev/null || echo -n "$f "; done)"
  docker build -q -t $IMG "$W" >/dev/null
}

unit() {   # $1 = test file in the image; fails the script unless its last line is ALL PASS
  local out
  out=$(docker run --rm --entrypoint python $IMG "$1" 2>&1 || true)
  echo "$out" | grep -E "^(FAIL)|Traceback" | sed 's/^/    /' || true
  echo "$out" | tail -1 | grep -q "ALL PASS" || { echo "$out" | tail -25 | sed 's/^/    /'; echo "$1 FAILED"; exit 1; }
  echo "    $1: ALL PASS ($(echo "$out" | grep -c '^PASS') checks)"
}

case "${1:-}" in
test)
  build
  echo "2/2 server tests in the patched image"
  unit test_sweep.py; unit test_pick.py; unit test_relay.py; unit test_receive.py; unit test_devices.py; unit test_push.py
  unit test_abyss_handoff.py
  mkdir -p "$RUN/js"; printf '[brand]\nname = "Novashare"\ntheme = "horizon"\n' > "$RUN/js/h.toml"
  # the library pages must be byte-for-byte live's: /sending page + manifest hashes, atomic and horizon themes
  for th in default horizon; do
    a=(); [ $th = horizon ] && a=(-v "$RUN/js/h.toml:/config/drop.toml:ro")
    old=$(docker run --rm "${a[@]}" --entrypoint python nuc-homelab-intake:latest test_themes.py dump 2>/dev/null | tail -1)
    new=$(docker run --rm "${a[@]}" --entrypoint python $IMG test_themes.py dump 2>/dev/null | tail -1)
    [ -n "$old" ] && [ "$old" = "$new" ] && echo "    /sending page + manifest ($th theme): identical to live" || { echo "    /sending or manifest CHANGED ($th)"; exit 1; }
  done
  out=$(docker run --rm -v "$RUN/js/h.toml:/config/drop.toml:ro" --entrypoint python $IMG test_ui.py 2>&1 | tail -1)
  echo "$out" | grep -q "ALL PASS" && echo "    test_ui.py (horizon): ALL PASS" || { echo "    test_ui.py: $out"; exit 1; }
  # JS syntax of the page scripts and the horizon service worker, as the patched image serves them
  docker run --rm -v "$RUN/js:/w" -e DROP_CONFIG=/w/h.toml --entrypoint python $IMG -c \
    "import receive_js,relay_js;open('/w/inbox.mjs','w').write(receive_js.JS);open('/w/rsw.js','w').write(receive_js.SW);open('/w/relay.mjs','w').write(relay_js.JS)"
  docker run --rm -v "$RUN/js:/w:ro" node:22-alpine sh -c 'for f in /w/inbox.mjs /w/relay.mjs /w/rsw.js; do node --check "$f" || exit 1; done' \
    && echo "    node --check inbox.js, relay.js, /relay/sw.js: OK"
  echo "SANDBOX TESTS DONE. Swap NOT done."
  ;;
up)
  docker image inspect $IMG >/dev/null 2>&1 || build
  docker rm -f $PROXY $NAME >/dev/null 2>&1 || true
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
    -e INTAKE_MAX_MB=10240 -e INTAKE_RELAY_OPEN_MB=40 -e INTAKE_RELAY_SWEEP_FIRST_S=5 -e LLM_BASE=http://10.255.255.1 -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels \
    -e GOTENBERG_URL=http://10.255.255.1:3000 -e TMPDIR=/srv/unsorted/.tmp \
    -v "$RUN/drop.toml:/config/drop.toml:ro" -v "$RUN/unsorted:/srv/unsorted" $IMG >/dev/null
  for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:$PORT/healthz && break; sleep 1; done
  K=$(cat "$RUN/key")
  c1=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Drop-Key: $K" -H "X-Auth-Email: casey@example.invalid" -H "X-Auth-Level: full" http://127.0.0.1:$PORT/relay/send)
  echo "    /relay/send $c1 (want 200)"
  [ "$c1" = 200 ] || { echo "SMOKE FAILED"; exit 1; }
  {
    printf '{\n\tadmin off\n\tauto_https off\n}\n'
    for pp in "$PJ jason" "$PZ casey"; do
      set -- $pp
      printf 'http://:%s {\n\tbind 127.0.0.1\n\treverse_proxy 127.0.0.1:%s {\n' "$1" "$PORT"
      printf '\t\theader_up X-Drop-Key {env.DROP_KEY}\n\t\theader_up X-Auth-Email %s@example.invalid\n' "$2"
      printf '\t\theader_up X-Auth-Level full\n\t\tflush_interval -1\n\t}\n}\n'
    done
  } > "$RUN/Caddyfile"
  docker run -d --name $PROXY --network host -e DROP_KEY="$(cat "$RUN/key")" \
    -v "$RUN/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.4-alpine >/dev/null
  for i in $(seq 1 20); do curl -s -o /dev/null http://127.0.0.1:$PJ/relay/send && break; sleep 1; done
  c2=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$PJ/relay/sw.js)
  echo "    front door: /relay/sw.js via Jason's port $c2 (want 200)"
  [ "$c2" = 200 ] || { echo "SMOKE FAILED"; exit 1; }
  echo "SANDBOX UP on 127.0.0.1:$PORT, front doors $PJ (Jason) and $PZ (Casey). Swap NOT done."
  ;;
find)   # NAME = the file name the library gave it (exact); prints "sha256 size path-under-unsorted"
  [[ "${2:-}" =~ ^[A-Za-z0-9._-]{1,120}$ ]] || { echo "usage: find NAME"; exit 2; }
  hit=$(find "$RUN/unsorted" -path "$RUN/unsorted/.novashare-relay" -prune -o -type f -name "$2" -print | head -1)
  [ -n "$hit" ] || exit 1
  echo "$(sha256sum < "$hit" | cut -c1-64) $(stat -c %s "$hit") ${hit#$RUN/unsorted/}"
  ;;
relay)  # the ids (folder names) in the relay store, one per line
  ls -1 "$RUN/unsorted/.novashare-relay" 2>/dev/null || true
  ;;
log)    # count of log lines matching a fixed string (the log itself never leaves the NUC)
  [[ "${2:-}" =~ ^[A-Za-z0-9\ ._=-]{1,80}$ ]] || { echo "usage: log WORDS"; exit 2; }
  docker logs $NAME 2>&1 | grep -c -F -e "$2" || true
  ;;
grep)
  shift
  grep -r -a -l -F -e "$1" "$RUN/unsorted" || exit 1
  ;;
down)
  docker rm -f $PROXY $NAME >/dev/null 2>&1 || true
  docker rmi $IMG >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  echo "sandbox down (containers and image removed, run folder in the trash)"
  ;;
*) echo "usage: sweep_sbx.sh test|up|relay|log WORDS|grep NEEDLE|down"; exit 2 ;;
esac
