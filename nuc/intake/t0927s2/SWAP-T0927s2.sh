#!/usr/bin/env bash
# SWAP-T0927s2.sh - put T0927 slice 2 live: libraries as config (dropconfig.LIBRARIES, defaults = the old tables)
# and three required settings (LLM_BASE, AUTH_LEVELS_URL, GOTENBERG_URL; no built-in household addresses).
#   bash ~/sandbox/t0927s2/SWAP-T0927s2.sh
# Tested 2026-11-21 in the sandbox: test_libraries.py 31/31 (live drop.toml gives GRANT_FOR_KIND == the old table;
# book/comic/recipe/document/unknown placed exactly as before; 11 bad configs refused; broken config fails closed;
# app refuses to start without each setting), and all 7 intake suites + the virus checks ALL PASS on the patched copy.
# Live drop.toml needs NO change (no [library] section = the old behaviour).
# Undo (printed again at the end): copy the 5 files + compose back from ~/archive/<date>/t0927s2/ and rebuild,
#   or retag nuc-homelab-intake:before-t0927s2.
# Variables: D (date), A (archive folder), SB (sandbox), LIVE (live intake), COMPOSE (compose file), ENVLINE (added).
set -euo pipefail
D=$(date +%F)
A=~/archive/$D/t0927s2
SB=~/sandbox/t0927s2
LIVE=~/nuc-homelab/intake
COMPOSE=~/nuc-homelab/docker-compose.yml
ENVLINE='      - GOTENBERG_URL=${GOTENBERG_URL:-http://gotenberg-web:3000}'

echo "1/5 backups -> $A, image tag :before-t0927s2"
mkdir -p "$A"
cp -p "$LIVE/dropconfig.py" "$LIVE/classify.py" "$LIVE/app.py" "$LIVE/share.py" "$LIVE/test_t0931b.py" "$COMPOSE" "$A/"
docker tag nuc-homelab-intake:latest nuc-homelab-intake:before-t0927s2

echo "2/5 compose: GOTENBERG_URL for intake (once), next to LLM_BASE"
python3 - "$COMPOSE" "$ENVLINE" <<'EOF'
import sys
path, envline = sys.argv[1], sys.argv[2]
lines = open(path, encoding="utf-8").read().split("\n")
if envline in lines:
    print("    already there"); sys.exit(0)
start = lines.index("  intake:")
anchor = next(i for i in range(start, len(lines)) if lines[i].strip().startswith("- LLM_BASE="))
lines.insert(anchor + 1, envline)
open(path, "w", encoding="utf-8").write("\n".join(lines))
print("    added at line", anchor + 2)
EOF
docker compose -f "$COMPOSE" config -q && echo "    compose file valid"

echo "3/5 code: patch a FRESH copy of the live code (keeps any swap that went live first, e.g. T0962), test, install"
W=$SB/swapcopy; IMG=intake-sandbox:t0927s2swap
[ -d "$W" ] && trash-put "$W"
cp -a "$LIVE" "$W"; cp "$SB/test_libraries.py" "$W/"
python3 "$SB/patch_libraries.py" "$W"
docker build -q -t $IMG "$W" >/dev/null
docker run --rm -v "$HOME/.config/drop/drop.toml:/config/drop.toml:ro" -e PENDING_ROOT=/srv/unsorted/.pending \
  --entrypoint python $IMG test_libraries.py | tail -1 | grep -q "ALL PASS" || { echo "TEST FAILED: code not installed (compose already edited: restore it from $A)"; exit 1; }
echo "    test_libraries: ALL PASS"
install -m 644 "$W/dropconfig.py" "$W/classify.py" "$W/app.py" "$W/share.py" \
  "$W/test_t0931b.py" "$W/test_libraries.py" "$LIVE/"

echo "4/5 rebuild + recreate intake"
docker compose -f "$COMPOSE" up -d --build intake
s=""
for i in $(seq 1 30); do
  s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true)
  [ "$s" = healthy ] && break; sleep 2
done
echo "    health: $s"

echo "5/5 check: libraries + settings (no values printed)"
docker exec intake python -c "import dropconfig as d, classify, share, app; print('    error:', repr(d.ERROR), '| libraries', sorted(d.LIBRARIES), '| grants', classify.GRANT_FOR_KIND, '| gotenberg set', bool(share.GOTENBERG))"
echo "    expected: error '' | 5 libraries | grants recipe->recipe workout->gym document->paperless book->read comic->read | gotenberg set True"
echo
echo "Then: send one photo from the phone, and commit: cd ~/nuc-homelab && git add intake docker-compose.yml && git commit -m 'T0927 slice 2 live'"
echo "UNDO: cp $A/dropconfig.py $A/classify.py $A/app.py $A/share.py $A/test_t0931b.py $LIVE/ && cp $A/docker-compose.yml $COMPOSE && docker compose -f $COMPOSE up -d --build intake"
