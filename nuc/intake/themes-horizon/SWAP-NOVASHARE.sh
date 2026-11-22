#!/usr/bin/env bash
# SWAP-NOVASHARE.sh - Jason 2026-11-22: rename Atomic Share to "Novashare" with the collapsing-star animation
# (theme "horizon": a star collapses into an event horizon that pulls everything in). Same drop otherwise:
# same icon, links, people, libraries.
#   bash ~/sandbox/horizon/SWAP-NOVASHARE.sh
# Steps: (1) fresh copy of live -> the T0942 theme system (patch_theme.py with the garden + horizon parts) and
# "horizon" allowed in dropconfig; (2) PROOF the theme system alone changes nothing: with theme atomic the page and
# manifest are byte-for-byte today's (test_themes.py); (3) the horizon page shows Novashare with every engine
# hook; (4) back up + install; (5) drop.toml [brand] -> Novashare / horizon (backup first, only brand lines change,
# printed without any other line); (6) rebuild + check the live page and manifest.
# Undo (printed at the end): restore the files + drop.toml from ~/archive/<date>/novashare/ and rebuild, or
# retag nuc-homelab-intake:before-novashare.
# Variables: D, A (archive), SB (sandbox), T42 (T0942 sandbox), LIVE, COMPOSE, CONF (live drop.toml), W (patched
# copy), OLD (unpatched copy), FILES (what is installed).
set -euo pipefail
D=$(date +%F); A=~/archive/$D/novashare; SB=~/sandbox/horizon; T42=~/sandbox/t0942
LIVE=~/nuc-homelab/intake; COMPOSE=~/nuc-homelab/docker-compose.yml; CONF=~/.config/drop/drop.toml
W=$SB/swapcopy; OLD=$SB/oldcopy
FILES="atomic_ui.py portal.py share.py themes.py dropconfig.py icons_png_garden.py test_themes.py"
SET="-e LLM_BASE=http://x -e AUTH_LEVELS_URL=http://x -e GOTENBERG_URL=http://x"

echo "1/6 fresh copies of live: one as-is (for the byte check), one with the theme system"
for d in "$W" "$OLD"; do [ -d "$d" ] && trash-put "$d"; done
cp -a "$LIVE" "$OLD"; cp -a "$LIVE" "$W"
cp "$T42/test_themes.py" "$OLD/"; cp "$T42/test_themes.py" "$T42/icons_png_garden.py" "$W/"
python3 - "$T42/themes_garden.py.part" "$SB/themes_horizon.py.part" "$SB/themes_combined.py.part" <<'EOF'
import sys
g = open(sys.argv[1], encoding="utf-8").read(); h = open(sys.argv[2], encoding="utf-8").read()
line = 'THEMES = {"atomic": ATOMIC, "garden": GARDEN}\n'
assert g.count(line) == 1
open(sys.argv[3], "w", encoding="utf-8").write(g.replace(line, h + '\nTHEMES = {"atomic": ATOMIC, "garden": GARDEN, "horizon": HORIZON}\n'))
EOF
python3 "$T42/patch_theme.py" "$W" "$SB/themes_combined.py.part"
python3 - "$W/dropconfig.py" <<'EOF'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read(); old = 'THEMES = {"atomic", "garden"}\n'
assert s.count(old) == 1
open(p, "w", encoding="utf-8").write(s.replace(old, 'THEMES = {"atomic", "garden", "horizon"}\n'))
EOF
docker build -q -t intake-sandbox:novaold "$OLD" >/dev/null
docker build -q -t intake-sandbox:novanew "$W" >/dev/null

echo "2/6 proof: with today's drop.toml (atomic) the page + manifest are byte-for-byte today's"
docker run --rm -v "$CONF:/config/drop.toml:ro" $SET --entrypoint python intake-sandbox:novaold test_themes.py dump > "$SB/old.json"
docker run --rm -v "$CONF:/config/drop.toml:ro" -v "$SB/old.json:/old.json:ro" $SET --entrypoint python intake-sandbox:novanew \
  test_themes.py check /old.json | tail -4
docker run --rm -v "$CONF:/config/drop.toml:ro" -v "$SB/old.json:/old.json:ro" $SET --entrypoint python intake-sandbox:novanew \
  test_themes.py check /old.json | tail -1 | grep -q "ALL PASS" || { echo "BYTE CHECK FAILED: nothing changed"; exit 1; }

echo "3/6 the horizon page with the name Novashare"
printf '[brand]\nname = "Novashare"\nshort_name = "Novashare"\ntheme = "horizon"\n' > "$SB/nova.toml"
docker run --rm -v "$SB/nova.toml:/config/drop.toml:ro" $SET --entrypoint python intake-sandbox:novanew -c "
import re, atomic_ui, share, themes
p = atomic_ui.PAGE
ok = (share.MANIFEST['name'] == 'Novashare' and 'Novashare' in p and 'Atomic Share' not in p and '@@' not in p
      and all(f'class=\"{c}\"' in p or f'class={c}' in p for c in ('atom', 'orbits', 'nucleus', 'glow'))
      and 'class=\"hole\"' in p and not re.search(r'=[^\"\'\s>]+/>', themes.HORIZON['svg']))
print('    horizon page:', 'ok' if ok else 'PROBLEM'); raise SystemExit(0 if ok else 1)" || { echo "HORIZON CHECK FAILED: nothing changed"; exit 1; }

echo "4/6 backups -> $A, image tag :before-novashare; install"
mkdir -p "$A"; chmod 700 "$A"
for f in atomic_ui.py portal.py share.py dropconfig.py; do cp -p "$LIVE/$f" "$A/"; done
install -m 600 "$CONF" "$A/drop.toml"
docker tag nuc-homelab-intake:latest nuc-homelab-intake:before-novashare
for f in $FILES; do install -m 644 "$W/$f" "$LIVE/$f"; done

echo "5/6 drop.toml [brand] -> Novashare / horizon (only those lines change)"
python3 - "$CONF" <<'EOF'
import re, sys, tomllib
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
brand = '[brand]\nname = "Novashare"\nshort_name = "Novashare"\ntheme = "horizon"\n'
m = re.search(r'^\[brand\]\n(?:(?!\[).*\n)*', s, re.M)
s = s[:m.start()] + brand + "\n" + s[m.end():].lstrip("\n") if m else brand + "\n" + s
tomllib.loads(s)                      # still valid TOML, or nothing is written
open(p, "w", encoding="utf-8").write(s)
print("    brand now:", tomllib.loads(s)["brand"])
EOF

echo "6/6 rebuild + live check"
docker compose -f "$COMPOSE" up -d --build intake
s=""
for i in $(seq 1 30); do
  s=$(docker inspect -f '{{.State.Health.Status}}' intake 2>/dev/null || true)
  [ "$s" = healthy ] && break; sleep 2
done
echo "    health: $s"
docker exec intake python -c "import share, dropconfig; print('    live name:', share.MANIFEST['name'], '| theme:', dropconfig.BRAND['theme'], '| config error:', repr(dropconfig.ERROR))"
echo "UNDO: cp $A/atomic_ui.py $A/portal.py $A/share.py $A/dropconfig.py $LIVE/ && install -m 600 $A/drop.toml $CONF && docker compose -f $COMPOSE up -d --build intake"
