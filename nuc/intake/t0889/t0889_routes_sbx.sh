#!/usr/bin/env bash
# t0889_routes_sbx.sh - T0889 step 7 (2026-11-22): every shared-link route against REAL sites and the REAL
# gotenberg renderers, in a throw-away intake sandbox built from a fresh copy of the live intake code.
# Never touches the live intake: own container (sbx-t0889), own data folder, port 127.0.0.1:18240 only.
#   bash ~/sandbox/t0889/t0889_routes_sbx.sh
# Routes checked (Do line + Steps 2-6): direct PDF -> filed as a file; direct image -> filed as a file;
# article -> readable-text PDF via gotenberg convert/html (GOTENBERG_HTML_URL); tiny page -> Chromium print;
# dead Gemini share -> refused; private address -> refused; YouTube with METUBE_URL unset -> PDF (no MeTube);
# the 4 offline T0889 test files + the Drop page link card.
# Variables: SB (this item's sandbox folder), RUN (fresh copy + data), IMG, K (throw-away key), GW (gateway of
# nuc-homelab_default = where host curl arrives from), CONF (live drop.toml, read-only), BAD (failed checks).
set -u
SB=~/sandbox/t0889; mkdir -p "$SB"
RUN=$(mktemp -d "$SB/run-XXXXXX"); cp -a ~/nuc-homelab/intake "$RUN/src"; B=$RUN/data
mkdir -p $B/books $B/manga $B/media $B/unsorted $B/consume
IMG=intake-sandbox:t0889; K=sbx-t0889-key; CONF=$HOME/.config/drop/drop.toml; BAD=0
docker build -q -t $IMG "$RUN/src" >/dev/null || { echo "BUILD FAILED"; exit 1; }
GW=$(docker network inspect nuc-homelab_default -f '{{(index .IPAM.Config 0).Gateway}}')
docker rm -f sbx-t0889 >/dev/null 2>&1
docker create --name sbx-t0889 -p 127.0.0.1:18240:8100 --network nuc-homelab_default \
  -e INTAKE_KEY=$K -e INTAKE_ALLOWED_PEERS=127.0.0.1,$GW -e LLM_BASE=http://10.255.255.1 -e INTAKE_LLM_TIMEOUT=2 \
  -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels -e PENDING_ROOT=/srv/unsorted/.pending \
  -e GOTENBERG_URL=http://gotenberg-web:3000 -e GOTENBERG_HTML_URL=http://gotenberg:3000 -e CLAMD_HOST=nuc-homelab-clamav-1 \
  -v $CONF:/config/drop.toml:ro -v $B/books:/srv/books -v $B/manga:/srv/manga -v $B/media:/srv/media \
  -v $B/unsorted:/srv/unsorted -v $B/consume:/srv/consume $IMG >/dev/null
docker network connect webprint sbx-t0889
docker start sbx-t0889 >/dev/null
for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:18240/healthz -H "X-Drop-Key: $K" && break; sleep 1; done

echo "=== offline tests in the image"
for t in test_link_probe.py test_link_route.py test_readable.py test_t0889_share.py test_t0889_step4.py test_t0889_step6.py; do
  # Outcome 2026-11-22: test_t0889_share/step4 import app.py, which refuses to start without INTAKE_KEY,
  # LLM_BASE, AUTH_LEVELS_URL, GOTENBERG_URL and drop.toml; with these dummies all 6 files print ALL PASS.
  out=$(docker run --rm --network none -e INTAKE_KEY=x -e LLM_BASE=http://x -e AUTH_LEVELS_URL=http://x \
        -e GOTENBERG_URL=http://x -e PENDING_ROOT=/srv/unsorted/.pending -v $CONF:/config/drop.toml:ro \
        --entrypoint python $IMG $t 2>&1 | tail -1)
  case "$out" in *"ALL PASS"*) echo "PASS $t";; *) echo "FAIL $t: $out"; BAD=$((BAD+1));; esac
done

# share LINK EXPECT_STATUS EXPECT_TEXT: POST one link as a phone share, JSON answer, check status + a word.
share() {
  local link=$1 want=$2 word=$3 out
  out=$(curl -s -m 120 -H "X-Drop-Key: $K" -H "X-Auth-Email: tester@example.com" -H "X-Auth-Level: full" \
        -H "Accept: application/json" --form-string "url=$link" http://127.0.0.1:18240/share)
  # want "" = filed somewhere: any status except error/refused
  if { if [ -z "$want" ]; then ! printf '%s' "$out" | grep -qE '"status": *"(error|refused)"' && printf '%s' "$out" | grep -q '"status"';
       else printf '%s' "$out" | grep -q "\"status\": *\"$want\""; fi; } && printf '%s' "$out" | grep -qi -- "$word"; then
    echo "PASS $link -> $want ($word)"
  else
    echo "FAIL $link -> wanted $want/$word, got: $(printf '%s' "$out" | head -c 400)"; BAD=$((BAD+1))
  fi
}
echo "=== live routes"
share "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf" "" "dummy.pdf"
share "https://www.w3.org/Icons/w3c_home.png" "" ".png"
share "https://en.wikipedia.org/wiki/Atom" "" ".pdf"
share "https://example.com/" "" ".pdf"
share "https://g.co/gemini/share/000000000000" "refused" "Gemini"
share "http://192.168.1.68/" "refused" "public"
share "https://youtu.be/dQw4w9WgXcQ" "" ".pdf"
echo "=== what got filed (names only)"
find $B -type f ! -name '*.json' | sed "s|$B/||" | sort | head -40
echo "=== container errors"
docker logs sbx-t0889 2>&1 | grep -E "Traceback|ERROR" | head -10
docker rm -f sbx-t0889 >/dev/null
echo "sandbox removed; run folder kept: $RUN"
[ "$BAD" = 0 ] && echo "ROUTES: ALL PASS" || echo "ROUTES: $BAD check(s) did not pass"
exit $(( BAD > 0 ))
