#!/usr/bin/env bash
# harden_swap.sh - Novashare step 7 swap (2026-11-24, approved by Jason: "approve the fix batch", "burn the old pages").
#   bash ~/sandbox/novashare-harden/harden_swap.sh
#
# SWAP-PATCH.sh installs *.py only; this step also changes the Dockerfile (pinned base, --pull), removes burned files
# and adds a header backstop to the drop site in Caddy. So:
#   1 SWAP-PATCH.sh (tag e2e): patch a fresh copy, run test_harden.py in it (stops here, nothing changed, if it fails),
#     back up + install the changed *.py, retag the old image :before-e2e, rebuild, live regression
#   2 Dockerfile: back up, install the pinned one, rebuild with --pull, health
#   3 burn: the old Drop page copy and the one-time page patchers move out of the live folder into the archive
#   4 Caddy: back up Caddyfile.nuc, add the header backstop to the drop site (in place: it is a bind-mounted file),
#     caddy validate (restore and stop on failure), caddy reload
#   5 checks: every route's CSP from inside the container, CSP through Caddy, live regression again
# Undo: printed at the end. Variables: SB, LIVE, HL, C (Caddyfile), A (archive), TAG.
set -euo pipefail
SB=~/sandbox/novashare-harden; LIVE=~/nuc-homelab/intake; HL=~/nuc-homelab; C=$HL/web/Caddyfile.nuc
TAG=e2e; A=~/archive/$(date +%F)/$TAG
BURN="portal.py.bak-20261119 atomic_patch.py captcha_patch.py gemini_patch.py gotenberg_web_patch.py pwa_patch.py share_patch.py gemini_probe.py pwa_sbx.sh"

wait_healthy() {
  local s=""
  for _ in $(seq 1 60); do s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true); [ "$s" = healthy ] && break; sleep 2; done
  echo "    health: $s"; [ "$s" = healthy ]
}

echo "1/5 SWAP-PATCH.sh (code)"
bash ~/sandbox/horizon/SWAP-PATCH.sh $TAG $SB/novashare_harden_patch.py $SB/test_harden.py | sed 's/^/    /'
grep -q "site_assets" "$LIVE/app.py" || { echo "code not installed: stopped after step 1"; exit 1; }

echo "2/5 Dockerfile (pinned base) + rebuild --pull"
mkdir -p "$A"; chmod 700 "$A"
cp -p "$LIVE/Dockerfile" "$A/Dockerfile"
install -m 644 "$SB/Dockerfile.new" "$LIVE/Dockerfile"
/home/user/bin/dc build --pull intake >/dev/null 2>&1
/home/user/bin/dc up -d intake >/dev/null 2>&1
wait_healthy || { echo "NOT HEALTHY after the rebuild: see UNDO"; }

echo "3/5 burn"
mkdir -p "$A/burned"
for f in $BURN; do [ -e "$LIVE/$f" ] && mv "$LIVE/$f" "$A/burned/" && echo "    burned (archived): $f"; done

echo "4/5 Caddy header backstop on the drop site"
cp -p "$C" "$A/Caddyfile.nuc"
python3 - "$C" "$A/Caddyfile.new" <<'EOF'
import sys
s = open(sys.argv[1], encoding="utf-8").read()
anchor = 'drop.home.example.net:8543 {\n\theader X-Robots-Tag "noindex, nofollow"\n\theader -Server\n'
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; manifest-src 'self'; worker-src 'self'; frame-src 'self'; media-src 'self' blob:; "
       "form-action 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; require-trusted-types-for 'script'")
block = ('\t# Step 7 (2026-11-24, audit High 2a, Jason: "burn the old pages"): ONE strict header set for the whole drop site.\n'
         '\t# intake sets these on every answer itself (site_assets.SITE_CSP; the key pages set their own, stricter);\n'
         '\t# "?" only fills in what an answer lacks, e.g. Caddy\'s own 404 for /intake* or a sign-in redirect. /__sso*\n'
         '\t# (the sign-in service) is left alone. The Abyss keyring popup keeps COOP unsafe-none (it needs its opener).\n'
         '\t@not_sso not path /__sso*\n'
         '\theader @not_sso {\n'
         f'\t\t?Content-Security-Policy "{CSP}"\n'
         '\t\t?Cross-Origin-Opener-Policy "same-origin"\n'
         '\t\t?Cross-Origin-Resource-Policy "same-origin"\n'
         '\t\t?X-Content-Type-Options "nosniff"\n'
         '\t\t?X-Frame-Options "DENY"\n'
         '\t\t?Referrer-Policy "no-referrer"\n'
         '\t\tStrict-Transport-Security "max-age=31536000; includeSubDomains"\n'
         '\t}\n')
if "Step 7 (2026-11-24, audit High 2a" in s:
    print("    Caddy: backstop already there"); open(sys.argv[2], "w", encoding="utf-8").write(s); sys.exit(0)
assert s.count(anchor) == 1, "drop site anchor not found once"
open(sys.argv[2], "w", encoding="utf-8").write(s.replace(anchor, anchor + block))
print("    Caddy: backstop added to the drop site")
EOF
cat "$A/Caddyfile.new" > "$C"
if docker exec caddy-nuc caddy validate --config /etc/caddy/Caddyfile >/dev/null 2>&1; then
  docker exec caddy-nuc caddy reload --config /etc/caddy/Caddyfile >/dev/null 2>&1 && echo "    caddy validate OK, reloaded"
else
  cat "$A/Caddyfile.nuc" > "$C"; echo "    caddy validate FAILED: Caddyfile restored, not reloaded"; exit 1
fi

echo "5/5 checks"
docker exec -i intake python - <<'EOF' | sed 's/^/    /'
# Every route of the live app, asked from inside the container (127.0.0.1 = the healthcheck peer; no key needed),
# as the admin. Prints only paths, status codes and which header is missing: no bodies, no values.
import re, urllib.request, urllib.error, app
from fastapi.routing import APIRoute
import portal
email = next(e for e, p in portal.WHO.items() if p == portal.ADMIN)   # never printed
H = {"X-Auth-Email": email, "X-Auth-Level": "full", "Sec-Fetch-Site": "same-origin", "X-Novashare": "1"}
FILL = {"name": "devices.js", "device_id": "A" * 22, "other": "nobody", "tid": "B" * 22, "n": "0", "uid": "C" * 22}
n, bad = 0, []
for r in app.app.routes:
    if not isinstance(r, APIRoute) or "GET" not in r.methods:
        continue
    path = re.sub(r"\{(\w+)(:[^}]*)?\}", lambda m: FILL.get(m.group(1), "x"), r.path)
    req = urllib.request.Request("http://127.0.0.1:8100" + path, headers=H)
    try:
        resp = urllib.request.urlopen(req, timeout=20); h, code = resp.headers, resp.status
    except urllib.error.HTTPError as e:
        h, code = e.headers, e.code
    c = h.get("Content-Security-Policy") or ""
    n += 1
    if not c or "script-src 'self'" not in c or "unsafe" in c or "frame-ancestors 'none'" not in c or h.get("X-Content-Type-Options") != "nosniff":
        bad.append(f"{path} {code}")
print(f"live GET routes with the strict CSP: {n - len(bad)} of {n}" + (f"  MISSING: {bad}" if bad else ""))
EOF
for p in / /manifest.webmanifest /intake /devices; do
  h=$(curl -s -o /dev/null -D - --resolve drop.home.example.net:8543:127.0.0.1 "https://drop.home.example.net:8543$p" || true)
  code=$(printf '%s' "$h" | head -1 | awk '{print $2}')
  printf '%s' "$h" | grep -qi "^content-security-policy: .*script-src 'self'" && echo "    via Caddy $p ($code, signed out): CSP present" \
    || echo "    via Caddy $p ($code, signed out): CSP MISSING"
done
bash ~/sandbox/intake_regress_live.sh 2>&1 | grep -E "^FAIL|REGRESSION" | sed 's/^/    /'
echo "DONE. Rollback image: nuc-homelab-intake:before-$TAG. Archive: $A"
echo "UNDO: cp $A/*.py $LIVE/ && cp $A/Dockerfile $LIVE/ && mv $A/burned/* $LIVE/ && cat $A/Caddyfile.nuc > $C && docker exec caddy-nuc caddy reload --config /etc/caddy/Caddyfile && ~/bin/dc up -d --build intake   (or: docker tag nuc-homelab-intake:before-$TAG nuc-homelab-intake:latest && ~/bin/dc up -d intake)"
