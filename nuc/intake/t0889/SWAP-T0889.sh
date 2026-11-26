#!/usr/bin/env bash
# SWAP-T0889.sh - T0889 step 7 (2026-11-22): put the link router live on the Drop.
#   bash ~/sandbox/t0889/SWAP-T0889.sh
# What changes: the intake image is rebuilt from ~/nuc-homelab/intake (the 11 T0889 files are already there,
# md5-checked against the running container: nothing else differs), and compose gets ONE env line,
# GOTENBERG_HTML_URL=http://gotenberg:3000 (Paperless's file-only renderer; gotenberg-web 403s convert/html).
# METUBE_URL stays unset, so YouTube links keep becoming a page PDF (Ask on T0889 about where MeTube saves).
# Tested before: intake_regress_live.sh ALL PASS and t0889_routes_sbx.sh 7/7 routes + 6/6 offline tests.
# Live checks after the swap file NOTHING: health, env, a probe, and one convert/html call from inside intake.
# Outcome 2026-11-22: ran once, 0 failed, intake healthy; the two heredoc checks first ran WITHOUT -i (empty
# stdin = silent pass), fixed to docker exec -i and rerun by hand: probe application/pdf 13264, convert/html 200 %PDF.
# Undo (printed at the end): restore compose from the archive and retag :before-t0889 as latest, then up -d.
# Variables: D (date), A (archive folder), COMPOSE, BAD (failed live checks).
set -euo pipefail
D=$(date +%F); A=~/archive/$D/t0889; COMPOSE=~/nuc-homelab/docker-compose.yml; BAD=0

echo "1/5 backups -> $A, image tag :before-t0889"
mkdir -p "$A"; cp -p "$COMPOSE" "$A/docker-compose.yml.before-t0889"
docker tag nuc-homelab-intake:latest nuc-homelab-intake:before-t0889

echo "2/5 compose: add GOTENBERG_HTML_URL under intake's GOTENBERG_URL (exactly one match, else stop)"
python3 - "$COMPOSE" <<'PY'
import sys, pathlib
p = pathlib.Path(sys.argv[1]); s = p.read_text(encoding="utf-8")
old = "      - GOTENBERG_URL=${GOTENBERG_URL:-http://gotenberg-web:3000}\n"
new = old + ("      # T0889 step 7 (2026-11-22): article links print our own clean HTML via convert/html, which\n"
             "      # gotenberg-web refuses (403, URL allow-list); Paperless's gotenberg renders files only, JS off.\n"
             "      - GOTENBERG_HTML_URL=${GOTENBERG_HTML_URL:-http://gotenberg:3000}\n")
if "GOTENBERG_HTML_URL" in s:
    print("    already there"); sys.exit(0)
if s.count(old) != 1:
    sys.exit("    GOTENBERG_URL line found %d times: nothing changed" % s.count(old))
p.write_text(s.replace(old, new), encoding="utf-8"); print("    added")
PY
docker compose -f "$COMPOSE" config -q || { cp -p "$A/docker-compose.yml.before-t0889" "$COMPOSE"; echo "COMPOSE INVALID: restored"; exit 1; }

echo "3/5 rebuild + recreate intake"
docker compose -f "$COMPOSE" up -d --build intake
s=""
for i in $(seq 1 45); do
  s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true)
  [ "$s" = healthy ] && break; sleep 2
done
echo "    health: $s"; [ "$s" = healthy ] || BAD=$((BAD+1))

echo "4/5 live checks (nothing filed)"
docker exec intake printenv GOTENBERG_HTML_URL | grep -qx "http://gotenberg:3000" && echo "    PASS env" || { echo "    FAIL env"; BAD=$((BAD+1)); }
docker exec intake python -c "import link_probe, readable, link_route, share; print('    PASS modules load; metube on =', link_route.enabled())" || BAD=$((BAD+1))
docker exec -i intake python - <<'PY' || BAD=$((BAD+1))
import asyncio, link_probe
async def main():
    r = await link_probe.probe("https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf")
    ct = (r or {}).get("content_type", "") if isinstance(r, dict) else getattr(r, "content_type", "")
    print("    probe:", ct); assert "pdf" in ct
asyncio.run(main())
PY
docker exec -i intake python - <<'PY' || BAD=$((BAD+1))
import httpx
r = httpx.post("http://gotenberg:3000/forms/chromium/convert/html",
               files={"files": ("index.html", b"<h1>T0889 swap check</h1><p>ok</p>", "text/html")}, timeout=60)
print("    convert/html:", r.status_code, r.content[:4]); assert r.status_code == 200 and r.content[:4] == b"%PDF"
PY

echo "5/5 done ($BAD failed)"
echo "UNDO: cp -p $A/docker-compose.yml.before-t0889 $COMPOSE && docker tag nuc-homelab-intake:before-t0889 nuc-homelab-intake:latest && docker compose -f $COMPOSE up -d --no-build intake"
exit $(( BAD > 0 ))
