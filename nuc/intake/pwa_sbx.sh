#!/bin/sh
# pwa_sbx.sh - copy-test of the PNG icon patch (2026-11-18). Builds a candidate from a COPY of intake, runs it as
# intake-sbx (scratch folders), checks manifest + PNGs, removes the sandbox. Live intake is not touched;
# the live container is only used to render the icons (read-only, writes /tmp inside it).
set -eu
C=/home/user/build/intake-pwa
S=/home/user/build/intake-sbx-data
mkdir -p /home/user/build
[ -d "$C" ] && trash-put "$C"
cp -a /home/user/nuc-homelab/intake "$C"
cp /tmp/icons_png.py "$C/icons_png.py"   # drawn on the PC by gen_icons.py (gotenberg screenshot = 403)
cd "$C" && python3 /tmp/pwa_patch.py && python3 -m py_compile share.py icons_png.py
docker build -q -t nuc-homelab-intake:candidate "$C" >/dev/null
docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' intake | grep -v '^PATH=' > /tmp/intake-test.env
[ -d "$S" ] && trash-put "$S"
mkdir -p "$S/manga" "$S/books" "$S/consume" "$S/unsorted" "$S/media"; chmod -R 777 "$S"
docker rm -f intake-sbx >/dev/null 2>&1 || true
docker run -d --name intake-sbx --network nuc-homelab_default --env-file /tmp/intake-test.env \
  -v "$S/manga:/srv/manga" -v "$S/books:/srv/books" -v "$S/consume:/srv/consume" \
  -v "$S/unsorted:/srv/unsorted" -v "$S/media:/srv/media" nuc-homelab-intake:candidate >/dev/null
trash-put /tmp/intake-test.env
docker network connect webprint intake-sbx
sleep 6
c() { docker run --rm --network nuc-homelab_default curlimages/curl:8.11.1 -s "$@"; }
echo "== manifest icons"; c http://intake-sbx:8100/manifest.webmanifest | grep -o '"icons": \[[^]]*\]'
for i in icon-192.png icon-512.png icon.svg; do echo "== $i: $(c -o /dev/null -w '%{http_code} %{content_type} %{size_download}' http://intake-sbx:8100/$i)"; done
echo "== dead Gemini still refused"; c -H "X-Auth-Email: user1@example.com" -H "Accept: application/json" -F "url=https://g.co/gemini/share/0a1b2c3d4e5f" http://intake-sbx:8100/share; echo
docker logs intake-sbx 2>&1 | grep -iE "error|traceback" | tail -5
docker rm -f intake-sbx >/dev/null
