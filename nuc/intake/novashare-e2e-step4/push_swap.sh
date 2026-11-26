#!/usr/bin/env bash
# push_swap.sh - Novashare E2E Step 4 swap (2026-11-24). Run ONLY when Jason says so:
#   bash ~/sandbox/novashare-push/push_swap.sh
#
# SWAP-PATCH.sh installs *.py only, and Step 4 also needs the web push packages (requirements.txt) and the key in the
# container's environment (docker-compose.yml). So:
#   0 preflight: secrets.refs has the VAPID_PRIVATE_KEY line and it resolves from Pass to 43 characters (length only)
#   1 SWAP-PATCH.sh: patch a fresh copy, run test_push.py in it (stops here, nothing changed, if it fails), back up,
#     install the changed *.py, rebuild, live regression. Push is OFF after this step (no packages yet; nothing breaks)
#   2 back up requirements.txt + docker-compose.yml to the same archive folder; install the patched requirements.txt;
#     add "- VAPID_PRIVATE_KEY=${VAPID_PRIVATE_KEY:-}" to the intake service (once); dc config -q
#   3 rebuild through ~/bin/dc (secrets from Pass, in memory only), health, "push on" in the log, live regression
# Undo: printed at the end (copy back the archived files and rebuild, or retag :before-novashare-push).
set -euo pipefail
SB=~/sandbox/novashare-push; LIVE=~/nuc-homelab/intake; HL=~/nuc-homelab; COMPOSE=$HL/docker-compose.yml
TAG=novashare-push; W=~/sandbox/horizon/swap-$TAG; A=~/archive/$(date +%F)/$TAG
REF="VAPID_PRIVATE_KEY=pass://NUC services/NUC VAPID_PRIVATE_KEY novashare push (2026-11-24)/password"

echo "0/3 preflight"
grep -qxF "$REF" $HL/secrets.refs || { echo "    secrets.refs has no VAPID line. Add this line, then run again:"; echo "    $REF"; exit 1; }
tmp=$(mktemp); printf '%s\n' "$REF" > "$tmp"
n=$(/home/user/bin/pass run --env-file "$tmp" -- sh -c 'printf %s "${#VAPID_PRIVATE_KEY}"' 2>/dev/null || true); rm -f "$tmp"
[ "$n" = 43 ] || { echo "    the Pass item did not resolve to 43 characters (got '${n}'): stopped, nothing changed"; exit 1; }
echo "    VAPID key resolves from Pass (length 43)"

echo "1/3 SWAP-PATCH.sh (code)"
bash ~/sandbox/horizon/SWAP-PATCH.sh $TAG $SB/novashare_push_patch.py $SB/test_push.py | sed 's/^/    /'
[ -f "$W/requirements.txt" ] && grep -q "pywebpush==" "$W/requirements.txt" || { echo "patched copy missing: stopped after step 1"; exit 1; }

echo "2/3 requirements + compose (backups in $A)"
mkdir -p "$A"; chmod 700 "$A"
cp -p "$LIVE/requirements.txt" "$A/requirements.txt"; cp -p "$COMPOSE" "$A/docker-compose.yml"
install -m 644 "$W/requirements.txt" "$LIVE/requirements.txt"
python3 - "$COMPOSE" <<'EOF'
import pathlib, sys
p = pathlib.Path(sys.argv[1]); s = p.read_text(encoding="utf-8")
anchor = "      - INTAKE_TOKEN=${INTAKE_TOKEN:-}\n"
if "VAPID_PRIVATE_KEY" not in s:
    assert s.count(anchor) == 1, "anchor line not unique"
    s = s.replace(anchor, anchor + "      # Novashare E2E step 4 (2026-11-24): web push key (push.py), from Pass via ~/bin/dc + secrets.refs. Unset = push off.\n"
                  "      - VAPID_PRIVATE_KEY=${VAPID_PRIVATE_KEY:-}\n")
    p.write_text(s, encoding="utf-8")
    print("    compose: VAPID_PRIVATE_KEY added to intake")
else:
    print("    compose: already has VAPID_PRIVATE_KEY")
EOF
/home/user/bin/dc config -q && echo "    dc config: OK"

echo "3/3 rebuild with the push packages + key"
/home/user/bin/dc up -d --build intake >/dev/null 2>&1
s=""
for i in $(seq 1 45); do s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true); [ "$s" = healthy ] && break; sleep 2; done
echo "    health: $s"
docker logs --since 3m intake 2>&1 | grep -E "push (on|off)" | tail -1 | sed 's/^/    /'
docker exec intake python -c "import pywebpush, py_vapid; print('    packages: pywebpush', pywebpush.__version__ if hasattr(pywebpush,'__version__') else 'ok')" 2>&1 | tail -1
bash ~/sandbox/intake_regress_live.sh 2>&1 | grep -E "^FAIL|REGRESSION" | sed 's/^/    /' || true
echo "DONE. Phone check: open Novashare (iPhone: from the Home Screen icon), Inbox or Devices, 'Notify me on this device', 'Send a test'."
echo "UNDO: cp $A/*.py $LIVE/ && cp $A/requirements.txt $LIVE/ && cp $A/docker-compose.yml $COMPOSE && ~/bin/dc up -d --build intake   (or retag :before-$TAG)"
