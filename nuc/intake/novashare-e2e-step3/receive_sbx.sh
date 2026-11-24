#!/usr/bin/env bash
# receive_sbx.sh - Novashare E2E Step 3 sandbox (2026-11-24): build + test a PATCHED COPY of live intake. Never swaps.
#
#   bash ~/sandbox/novashare-receive/receive_sbx.sh test        fresh copy of live + patch; test_receive.py, then Step 2's
#                                                               test_relay.py and Step 1's test_devices.py as regressions
#   bash ~/sandbox/novashare-receive/receive_sbx.sh up          a throw-away container on 127.0.0.1:18262 (fake people,
#                                                               example.invalid) behind a Caddy front door that signs
#                                                               in Jason on :18263 and Casey on :18264 (as forward_auth
#                                                               does live), for test_receive_browser.py on the PC
#   bash ~/sandbox/novashare-receive/receive_sbx.sh tamper TID N   flip one byte inside ciphertext piece N of TID
#   bash ~/sandbox/novashare-receive/receive_sbx.sh state TID   transfer state + its deliveries (ids cut short)
#   bash ~/sandbox/novashare-receive/receive_sbx.sh grep NEEDLE grep the relay store, DB and log (exit 1 = not found)
#   bash ~/sandbox/novashare-receive/receive_sbx.sh down        stop the container, remove the image, run folder to trash
#
# Live intake is not touched: nothing under ~/nuc-homelab changes and the live image is not retagged. The sandbox
# drop key is random per run, in $RUN/key (mode 600), never printed. The swap, when Jason says so, is
#   bash ~/sandbox/horizon/SWAP-PATCH.sh novashare-receive ~/sandbox/novashare-receive/novashare_receive_patch.py \
#        ~/sandbox/novashare-receive/test_receive.py
# Variables: SB (this folder), LIVE, RUN (state of the current run), W (patched copy), IMG, NAME (container), PORT.
set -euo pipefail
SB=~/sandbox/novashare-receive; LIVE=~/nuc-homelab/intake; RUN=$SB/run; W=$RUN/src
IMG=intake-sandbox:receive; NAME=sbx-novashare-receive; PORT=18262
PROXY=sbx-novashare-receive-proxy; PJ=18263; PZ=18264   # Caddy as the sign-in front door: Jason on PJ, Casey on PZ

build() {
  echo "1/2 fresh copy of live ($(git -C ~/nuc-homelab log -1 --format=%h -- intake)) + patch"
  docker rm -f $NAME >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  mkdir -p "$RUN"; chmod 700 "$RUN"
  cp -a "$LIVE" "$W"; cp "$SB/test_receive.py" "$W/"
  python3 "$SB/novashare_receive_patch.py" "$W"
  echo "    differs from live: $(cd "$W" && for f in *.py; do cmp -s "$f" "$LIVE/$f" 2>/dev/null || echo -n "$f "; done)"
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
  unit test_receive.py; unit test_relay.py; unit test_devices.py
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
    -e INTAKE_MAX_MB=10240 -e LLM_BASE=http://10.255.255.1 -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels \
    -e GOTENBERG_URL=http://10.255.255.1:3000 -e TMPDIR=/srv/unsorted/.tmp \
    -v "$RUN/drop.toml:/config/drop.toml:ro" -v "$RUN/unsorted:/srv/unsorted" $IMG >/dev/null
  for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:$PORT/healthz && break; sleep 1; done
  K=$(cat "$RUN/key")
  c1=$(curl -s -o /dev/null -w "%{http_code}" -H "X-Drop-Key: $K" -H "X-Auth-Email: casey@example.invalid" -H "X-Auth-Level: full" http://127.0.0.1:$PORT/relay/inbox)
  echo "    /relay/inbox $c1 (want 200)"
  [ "$c1" = 200 ] || { echo "SMOKE FAILED"; exit 1; }
  # the front door, as live Caddy does it: forward_auth's X-Auth-* headers + the drop key, set by the proxy for every
  # request (pages, fetches AND the service worker's own script fetch), never by the browser. One port per person.
  {
    printf '{\n\tadmin off\n\tauto_https off\n}\n'
    for pp in "$PJ jason" "$PZ casey"; do
      set -- $pp
      printf 'http://:%s {\n\tbind 127.0.0.1\n\treverse_proxy 127.0.0.1:%s {\n' "$1" "$PORT"
      printf '\t\theader_up X-Drop-Key {env.DROP_KEY}\n\t\theader_up X-Auth-Email %s@example.invalid\n' "$2"
      printf '\t\theader_up X-Auth-Level full\n\t\tflush_interval -1\n\t}\n}\n'
    done
  } > "$RUN/Caddyfile"
  docker rm -f $PROXY >/dev/null 2>&1 || true
  docker run -d --name $PROXY --network host -e DROP_KEY="$(cat "$RUN/key")" \
    -v "$RUN/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.11.4-alpine >/dev/null
  for i in $(seq 1 20); do curl -s -o /dev/null http://127.0.0.1:$PZ/relay/inbox && break; sleep 1; done
  c2=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$PZ/relay/sw.js)
  echo "    front door: /relay/sw.js via Casey's port $c2 (want 200)"
  [ "$c2" = 200 ] || { echo "SMOKE FAILED"; exit 1; }
  echo "SANDBOX UP on 127.0.0.1:$PORT, front doors $PJ (Jason) and $PZ (Casey). Swap NOT done."
  ;;
tamper)   # flip one byte 100 bytes into ciphertext piece $3 of transfer $2 (ids are checked: 22 base64url chars)
  [[ "${2:-}" =~ ^[A-Za-z0-9_-]{22}$ && "${3:-}" =~ ^[0-9]+$ ]] || { echo "usage: tamper TID N"; exit 2; }
  python3 - "$RUN/unsorted/.novashare-relay/$2/data" "$3" <<'EOF'
import sys
p, n = sys.argv[1], int(sys.argv[2])
off = n * (4 * 1048576 + 16) + 100
with open(p, "r+b") as f:
    f.seek(off); b = f.read(1); f.seek(off); f.write(bytes([b[0] ^ 0x01]))
print("tampered one byte at", off)
EOF
  ;;
state)
  [[ "${2:-}" =~ ^[A-Za-z0-9_-]{22}$ ]] || { echo "usage: state TID"; exit 2; }
  python3 - "$RUN/unsorted" "$2" <<'EOF'
import json, pathlib, sqlite3, sys
root, tid = pathlib.Path(sys.argv[1]), sys.argv[2]
dbs = list(root.rglob("devices.db"))
c = sqlite3.connect(f"file:{dbs[0]}?mode=ro", uri=True)
t = c.execute("SELECT state FROM transfer WHERE id=?", (tid,)).fetchone()
d = c.execute("SELECT substr(device_id,1,6), state FROM delivery WHERE transfer_id=?", (tid,)).fetchall()
w = c.execute("SELECT count(*) FROM wrap WHERE transfer_id=?", (tid,)).fetchone()[0]
print(json.dumps({"transfer": t and t[0], "deliveries": dict(d), "wraps": w,
                  "file": (root / ".novashare-relay" / tid / "data").exists()}))
EOF
  ;;
grep)
  shift
  grep -r -a -l -F -e "$1" "$RUN/unsorted" || exit 1
  ;;
down)
  docker rm -f $PROXY $NAME >/dev/null 2>&1 || true
  docker rmi $IMG >/dev/null 2>&1 || true
  [ -d "$RUN" ] && trash-put "$RUN"
  echo "sandbox down (container and image removed, run folder in the trash)"
  ;;
*) echo "usage: receive_sbx.sh test|up|tamper TID N|state TID|grep NEEDLE|down"; exit 2 ;;
esac
