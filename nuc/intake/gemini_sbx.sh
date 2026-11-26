#!/bin/sh
# gemini_sbx.sh - sandbox test of the share door's link handling: a dead Gemini share and a Google CAPTCHA page
# must be refused, a normal page must be printed to PDF by gotenberg-web and filed (first written 2026-11-18 for
# the Gemini patch; rewritten 2026-11-22, T0950).
#   sh ~/nuc-homelab/intake/gemini_sbx.sh [real-gemini-share-url ...]
# Builds an image from a COPY of the live intake folder and runs it as intake-sbx on scratch folders; the live
# intake container and ~/nuc-homelab/intake are not touched.
#
# T0950 (2026-11-22), what changed and why:
#   - NO live secrets: the old script copied the live intake's whole environment (drop key, service key, model key)
#     into /tmp with default permissions (readable by every local user) and then into the trash for 30 days. The
#     sandbox now gets only throw-away values: a random key made for this run, dead addresses for the model and
#     the auth service, and gotenberg-web (needed to print the normal page).
#   - Scratch folders are 700 (owned by server = the container's uid 1000), not chmod -R 777.
#   - A test identity (tester@example.com), not a real email address in the code.
#   - Requests go from this host to 127.0.0.1:18299 with the drop key and X-Auth-Level full, like the regression
#     harness; the 11-18 requests from a curl container would now be refused by the T0931 gatekeeper.
#   - The 11-18 patches (gemini_patch.py, captcha_patch.py in /tmp) are part of share.py now; no patch step.
# Variables: C (copy of the intake folder), S (scratch data), IMG (sandbox image), K (random drop key for this run),
# GW (webprint network gateway = the address host requests come from), B (sandbox URL), u (extra URLs from $@).
set -eu
umask 077
C=/home/user/build/intake-gemini
S=/home/user/build/intake-sbx-data
IMG=nuc-homelab-intake:candidate
mkdir -p /home/user/build
[ -d "$C" ] && trash-put "$C"
cp -a /home/user/nuc-homelab/intake "$C"
docker build -q -t "$IMG" "$C" >/dev/null
[ -d "$S" ] && trash-put "$S"
mkdir -p "$S/manga" "$S/books" "$S/consume" "$S/unsorted" "$S/media"
K=$(head -c 18 /dev/urandom | base64 | tr -dc 'A-Za-z0-9')
GW=$(docker network inspect webprint -f '{{(index .IPAM.Config 0).Gateway}}')
docker rm -f intake-sbx >/dev/null 2>&1 || true
# webprint: gotenberg-web lives only there (same as the live intake)
docker run -d --name intake-sbx --network webprint -p 127.0.0.1:18299:8100 \
  -e INTAKE_KEY="$K" -e INTAKE_ALLOWED_PEERS="127.0.0.1,$GW" -e LLM_BASE=http://10.255.255.1 -e INTAKE_LLM_TIMEOUT=2 \
  -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels -e GOTENBERG_URL=http://gotenberg-web:3000 \
  -v "$S/manga:/srv/manga" -v "$S/books:/srv/books" -v "$S/consume:/srv/consume" \
  -v "$S/unsorted:/srv/unsorted" -v "$S/media:/srv/media" "$IMG" >/dev/null
sleep 6
B=http://127.0.0.1:18299
run() { curl -s -H "X-Drop-Key: $K" -H "X-Auth-Email: tester@example.com" -H "X-Auth-Level: full" \
          -H "Accept: application/json" "$@" "$B/share"; echo; }
echo "== dead Gemini share (must be refused)"; run -F "url=https://g.co/gemini/share/0a1b2c3d4e5f"
echo "== Google AI Mode link (CAPTCHA, must be refused)"; run -F "url=https://www.google.com/search?udm=50&q=free+pdf+maker+from+gemini"
echo "== normal link (must still file)";      run -F "title=Example page" -F "url=https://example.com/"
for u in "$@"; do echo "== real Gemini share $u"; run -F "url=$u"; done
echo "== sandbox files"; find "$S" -type f | sed "s|$S/||"
docker logs intake-sbx 2>&1 | grep -iE "error|traceback" | tail -5
docker rm -f intake-sbx >/dev/null
