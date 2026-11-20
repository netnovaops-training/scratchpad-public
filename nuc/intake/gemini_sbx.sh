#!/bin/sh
# gemini_sbx.sh - copy-test of the Gemini patch (2026-11-18). Builds a candidate image from a COPY of the intake
# folder, runs it as intake-sbx on scratch folders (never the real libraries), runs the tests, removes the sandbox.
# The live intake container and ~/nuc-homelab/intake are not touched.
set -eu
C=/home/user/build/intake-gemini
S=/home/user/build/intake-sbx-data
mkdir -p /home/user/build
[ -d "$C" ] && trash-put "$C"
cp -a /home/user/nuc-homelab/intake "$C"
cd "$C" && { grep -q _is_gemini share.py || python3 /tmp/gemini_patch.py; } && python3 /tmp/captcha_patch.py && python3 -m py_compile share.py
docker build -q -t nuc-homelab-intake:candidate "$C" >/dev/null
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' intake | grep -v '^PATH=' > /tmp/intake-test.env
[ -d "$S" ] && trash-put "$S"
mkdir -p "$S/manga" "$S/books" "$S/consume" "$S/unsorted" "$S/media"; chmod -R 777 "$S"
docker rm -f intake-sbx >/dev/null 2>&1 || true
docker run -d --name intake-sbx --network nuc-homelab_default --env-file /tmp/intake-test.env \
  -v "$S/manga:/srv/manga" -v "$S/books:/srv/books" -v "$S/consume:/srv/consume" \
  -v "$S/unsorted:/srv/unsorted" -v "$S/media:/srv/media" nuc-homelab-intake:candidate >/dev/null
trash-put /tmp/intake-test.env
docker network connect webprint intake-sbx   # gotenberg-web lives only there (same as live intake)
sleep 6
B=http://intake-sbx:8100
H="X-Auth-Email: user1@example.com"
run() { docker run --rm --network nuc-homelab_default curlimages/curl:8.11.1 -s -H "$H" -H "Accept: application/json" "$@" "$B/share"; echo; }
echo "== dead Gemini share (must be refused)"; run -F "url=https://g.co/gemini/share/0a1b2c3d4e5f"
echo "== Google AI Mode link (CAPTCHA, must be refused)"; run -F "url=https://www.google.com/search?udm=50&q=free+pdf+maker+from+gemini"
echo "== normal link (must still file)";      run -F "title=Example page" -F "url=https://example.com/"
for u in "$@"; do echo "== real Gemini share $u"; run -F "url=$u"; done
echo "== sandbox files"; find "$S" -type f | sed "s|$S/||"
docker logs intake-sbx 2>&1 | grep -iE "error|traceback" | tail -5
docker rm -f intake-sbx >/dev/null
