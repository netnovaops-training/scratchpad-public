#!/usr/bin/env bash
# harden_sbx.sh - Novashare step 7 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.
#
#   bash ~/sandbox/novashare-harden/harden_sbx.sh test     fresh copy of live + patch (new pinned base, burned files
#                                                          gone); every self-contained test_*.py in the patched image
#                                                          AND in the live image (a failure is a regression only if
#                                                          live passes it); node --check of every served script
#   bash ~/sandbox/novashare-harden/harden_sbx.sh regress  intake_regress_live.sh on the patched copy (all library
#                                                          suites, virus check, sandbox clamav)
#   bash ~/sandbox/novashare-harden/harden_sbx.sh up       throw-away container on 127.0.0.1:18292 behind a Caddy front
#                                                          door: Jason on :18293, Casey on :18294, and a stand-in "Abyss"
#                                                          origin on :18296 (keyring opener page) for the browser test
#   bash ~/sandbox/novashare-harden/harden_sbx.sh down     stop the containers, remove the image, run folder to trash
#
# Live intake is not touched. The sandbox drop key is random per run, in $RUN/key (mode 600), never printed.
# Variables: SB (this folder), LIVE, RUN (state of this run), W (patched copy), IMG, NAME, PORT, PJ/PZ/PA (doors).
set -euo pipefail
SB=~/sandbox/novashare-harden; LIVE=~/nuc-homelab/intake; RUN=$SB/run; W=$RUN/src
IMG=intake-sandbox:harden; NAME=sbx-novashare-harden; PORT=18292
PROXY=sbx-novashare-harden-proxy; PJ=18293; PZ=18294; PA=18296
SUITES="test_harden.py test_devices.py test_relay.py test_receive.py test_push.py test_pick.py test_sweep.py
 test_keyring.py test_abyss_handoff.py test_gateway.py test_locks.py test_races.py test_srvA.py test_srvB.py test_srvC.py
 test_purple.py test_hzicon.py test_onion.py test_torrent.py test_torlink.py test_untorlink.py test_t0927s3.py
 test_t0946_0.py test_t0958.py test_t0962.py test_t0962b.py test_t0964b.py test_rescan.py test_libraries.py
 test_link_probe.py test_link_route.py test_readable.py"

build() {
  echo "1/3 fresh copy of live ($(git -C ~/nuc-homelab log -1 --format=%h -- intake)) + patch"
  docker rm -f $PROXY $NAME >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  mkdir -p "$RUN"; chmod 700 "$RUN"
  cp -a "$LIVE" "$W"
  python3 "$SB/novashare_harden_patch.py" "$W"
  echo "    differs from live: $(cd "$W" && for f in *.py Dockerfile; do cmp -s "$f" "$LIVE/$f" 2>/dev/null || echo -n "$f "; done)"
  docker build --pull -q -t $IMG "$W" >/dev/null
}

one() {   # $1 image, $2 test -> PASS / FAIL (last line ALL PASS) ; extra args for the horizon theme tests
  local out
  out=$(timeout 600 docker run --rm "${@:3}" --entrypoint python "$1" "$2" 2>&1 || true)
  if echo "$out" | tail -1 | grep -q "ALL PASS"; then echo "PASS $(echo "$out" | grep -c '^PASS')"; else echo "FAIL $(echo "$out" | grep -E '^FAIL|Error' | head -3 | tr '\n' '|')"; fi
}

case "${1:-}" in
test)
  build
  echo "2/3 every self-contained suite, patched vs live image"
  mkdir -p "$RUN/js"; printf '[brand]\nname = "Novashare"\ntheme = "horizon"\n' > "$RUN/js/h.toml"
  REG=0
  for t in $SUITES test_ui.py test_themes.py; do
    a=(); case $t in test_ui.py|test_themes.py) a=(-v "$RUN/js/h.toml:/config/drop.toml:ro");; esac
    [ -f "$W/$t" ] || { echo "    $t: not in this copy"; continue; }
    new=$(one $IMG $t "${a[@]}")
    if [ -f "$LIVE/$t" ]; then old=$(one nuc-homelab-intake:latest $t "${a[@]}"); else old="(new)"; fi
    printf '    %-22s patched: %-40.40s live: %.40s\n' "$t" "$new" "$old"
    case "$new" in PASS*) ;; *) case "$old" in PASS*|"(new)") REG=$((REG + 1));; esac;; esac
  done
  echo "3/3 node --check of every script the patched image serves"
  docker run --rm -v "$RUN/js:/w" -e DROP_CONFIG=/w/h.toml -e INTAKE_KEY=x -e INTAKE_TOKEN=y -e LLM_BASE=http://x \
    -e AUTH_LEVELS_URL=http://x -e GOTENBERG_URL=http://x --entrypoint python $IMG -c "
import importlib, site_assets, atomic_ui, portal
for m, n in [('devices_js','JS'),('relay_js','JS'),('receive_js','JS'),('receive_js','SW'),('pick_js','JS'),('push_js','JS'),
             ('abyss_keyring_js','JS'),('atomic_ui','SW'),('atomic_ui','ATOMIC_JS')]:
    open('/w/%s.%s.js' % (m, n), 'w').write(getattr(importlib.import_module(m), n))
for n in ('sending.js', 'portal.js'):
    open('/w/site.' + n, 'w').write(site_assets.body(n).decode())
" 2>/dev/null
  docker run --rm -v "$RUN/js:/w:ro" node:22-alpine sh -c 'for f in /w/*.js; do node --check "$f" || exit 1; done' \
    && echo "    node --check: $(ls "$RUN/js"/*.js | wc -l) scripts OK" || { echo "    node --check FAILED"; REG=$((REG + 1)); }
  [ "$REG" = 0 ] && echo "SANDBOX TESTS: ALL PASS (no suite that passes on live fails here). Swap NOT done." \
                 || { echo "SANDBOX TESTS: $REG regression(s). Swap NOT done."; exit 1; }
  ;;
regress)
  [ -d "$W" ] || build
  bash ~/sandbox/intake_regress_live.sh "$W" 2>&1 | grep -E "^(===|PASS|FAIL|!!!|REGRESSION)" | sed 's/^/    /'
  ;;
up)
  docker image inspect $IMG >/dev/null 2>&1 || build
  docker rm -f $PROXY $NAME >/dev/null 2>&1 || true
  [ -d "$RUN/unsorted" ] && trash-put "$RUN/unsorted"
  mkdir -p "$RUN/unsorted" "$RUN/abyss"
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
  cp "$SB/fake_abyss.html" "$RUN/abyss/index.html"
  docker run -d --name $NAME -p 127.0.0.1:$PORT:8100 \
    -e INTAKE_KEY="$(cat "$RUN/key")" -e INTAKE_TOKEN=sbx-token -e INTAKE_ALLOWED_PEERS=127.0.0.1,172.17.0.1 \
    -e INTAKE_EDGE_PEERS=127.0.0.1,172.17.0.1 -e INTAKE_ABYSS_ORIGIN=http://127.0.0.1:$PA \
    -e INTAKE_MAX_MB=10240 -e LLM_BASE=http://10.255.255.1 -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels \
    -e GOTENBERG_URL=http://10.255.255.1:3000 -e TMPDIR=/srv/unsorted/.tmp \
    -v "$RUN/drop.toml:/config/drop.toml:ro" -v "$RUN/unsorted:/srv/unsorted" $IMG >/dev/null
  for _ in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:$PORT/healthz && break; sleep 1; done
  {
    printf '{\n\tadmin off\n\tauto_https off\n}\n'
    for pp in "$PJ jason" "$PZ casey"; do
      set -- $pp
      printf 'http://:%s {\n\tbind 127.0.0.1\n\treverse_proxy 127.0.0.1:%s {\n' "$1" "$PORT"
      printf '\t\theader_up X-Drop-Key {env.DROP_KEY}\n\t\theader_up X-Auth-Email %s@example.invalid\n' "$2"
      printf '\t\theader_up X-Auth-Level full\n\t\tflush_interval -1\n\t}\n}\n'
    done
    printf 'http://:%s {\n\tbind 127.0.0.1\n\troot * /abyss\n\tfile_server\n}\n' "$PA"
  } > "$RUN/Caddyfile"
  docker run -d --name $PROXY --network host -e DROP_KEY="$(cat "$RUN/key")" -v "$RUN/abyss:/abyss:ro" \
    -v "$RUN/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.4-alpine >/dev/null
  for _ in $(seq 1 20); do curl -s -o /dev/null http://127.0.0.1:$PJ/devices && break; sleep 1; done
  c=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$PJ/devices)
  echo "    front door: /devices via Jason's port $c (want 200)"
  [ "$c" = 200 ] || { echo "SMOKE FAILED"; exit 1; }
  echo "SANDBOX UP on 127.0.0.1:$PORT, doors $PJ (Jason) $PZ (Casey), stand-in Abyss $PA. Swap NOT done."
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
*) echo "usage: harden_sbx.sh test|regress|up|grep NEEDLE|down"; exit 2 ;;
esac
