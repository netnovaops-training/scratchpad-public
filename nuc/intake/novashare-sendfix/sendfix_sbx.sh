#!/usr/bin/env bash
# sendfix_sbx.sh - Novashare "cannot send" sandbox (2026-11-24). Runs the HARDENED (step 7) intake from a copy of
# ~/nuc-homelab/intake (or SRC=...) behind a front door that adds the sign-in headers AND the live step-7 header set.
#   bash ~/sandbox/novashare-sendfix/sendfix_sbx.sh up | down
# Ports: app 18392, Jason door 18393, Casey door 18394, stand-in Abyss 18396. Live intake is not touched.
set -euo pipefail
SB=~/sandbox/novashare-sendfix; RUN=$SB/run; W=$RUN/src; SRC=${SRC:-$HOME/nuc-homelab/intake}
IMG=intake-sandbox:sendfix; NAME=sbx-novashare-sendfix; PORT=18392
PROXY=sbx-novashare-sendfix-proxy; PJ=18393; PZ=18394; PA=18396
CSP="default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; manifest-src 'self'; worker-src 'self'; frame-src 'self'; media-src 'self' blob:; form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; require-trusted-types-for 'script'"
case "${1:-}" in
up)
  docker rm -f $PROXY $NAME >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  mkdir -p "$RUN/unsorted" "$RUN/abyss"; chmod 700 "$RUN"
  cp -a "$SRC" "$W"
  docker build -q -t $IMG "$W" >/dev/null
  umask 077; openssl rand -hex 24 > "$RUN/key"; umask 022
  printf '[brand]\nname = "Novashare"\nshort_name = "Novashare"\ntheme = "horizon"\n[[person]]\nid = "jason"\nname = "Jason"\nemails = ["jason@example.invalid"]\n[[person]]\nid = "casey"\nname = "Casey"\nemails = ["casey@example.invalid"]\n' > "$RUN/drop.toml"
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
      printf 'http://:%s {\n\tbind 127.0.0.1\n\theader {\n\t\t?Content-Security-Policy "%s"\n' "$1" "$CSP"
      printf '\t\t?Cross-Origin-Opener-Policy "same-origin"\n\t\t?Cross-Origin-Resource-Policy "same-origin"\n\t\t?X-Content-Type-Options "nosniff"\n\t\t?X-Frame-Options "DENY"\n\t\t?Referrer-Policy "no-referrer"\n\t}\n'
      printf '\treverse_proxy 127.0.0.1:%s {\n' "$PORT"
      printf '\t\theader_up X-Drop-Key {env.DROP_KEY}\n\t\theader_up X-Auth-Email %s@example.invalid\n' "$2"
      printf '\t\theader_up X-Auth-Level full\n\t\tflush_interval -1\n\t}\n}\n'
    done
    printf 'http://:%s {\n\tbind 127.0.0.1\n\troot * /abyss\n\tfile_server\n}\n' "$PA"
  } > "$RUN/Caddyfile"
  docker run -d --name $PROXY --network host -e DROP_KEY="$(cat "$RUN/key")" -v "$RUN/abyss:/abyss:ro" \
    -v "$RUN/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.4-alpine >/dev/null
  for _ in $(seq 1 20); do curl -s -o /dev/null http://127.0.0.1:$PJ/ && break; sleep 1; done
  echo "front door / : $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:$PJ/)  SENDFIX SANDBOX UP"
  ;;
grep) shift; grep -r -a -l -F -e "$1" "$RUN/unsorted" || exit 1 ;;
down)
  docker rm -f $PROXY $NAME >/dev/null 2>&1 || true
  docker rmi $IMG >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  echo "sendfix sandbox down" ;;
*) echo "usage: sendfix_sbx.sh up|grep NEEDLE|down"; exit 2 ;;
esac
