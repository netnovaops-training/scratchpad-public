#!/usr/bin/env bash
# build_horizon.sh - sandbox build of the "horizon" theme (2026-11-22): a fresh copy of the live intake, the
# T0942 theme patch with the garden AND horizon parts, "horizon" allowed in dropconfig.THEMES, then a sandbox
# container on 127.0.0.1:18243 showing it. Never touches the live intake.
#   bash ~/sandbox/horizon/build_horizon.sh
# Variables: SB (this sandbox), T42 (the T0942 sandbox with patch_theme.py + garden part), PART (combined part).
set -euo pipefail
SB=~/sandbox/horizon; T42=~/sandbox/t0942
cd "$SB"
[ -d intake ] && trash-put intake
cp -a ~/nuc-homelab/intake intake
cp "$T42/test_themes.py" "$T42/icons_png_garden.py" intake/
# combined part: garden part, then HORIZON inserted before the THEMES line, and THEMES gains horizon
python3 - "$T42/themes_garden.py.part" "$SB/themes_horizon.py.part" "$SB/themes_combined.py.part" <<'EOF'
import sys
garden, horizon, out = (open(p, encoding="utf-8").read() for p in sys.argv[1:3]), None, sys.argv[3]
g, h = garden
line = 'THEMES = {"atomic": ATOMIC, "garden": GARDEN}\n'
assert g.count(line) == 1
g = g.replace(line, h + '\n' + 'THEMES = {"atomic": ATOMIC, "garden": GARDEN, "horizon": HORIZON}\n')
open(out, "w", encoding="utf-8").write(g)
EOF
python3 "$T42/patch_theme.py" "$SB/intake" "$SB/themes_combined.py.part"
python3 - "$SB/intake/dropconfig.py" <<'EOF'
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
old = 'THEMES = {"atomic", "garden"}\n'
assert s.count(old) == 1
open(p, "w", encoding="utf-8").write(s.replace(old, 'THEMES = {"atomic", "garden", "horizon"}\n'))
print("dropconfig: horizon allowed")
EOF
docker build -q -t intake-sandbox:horizon intake >/dev/null
docker rm -f sbx-th-horizon >/dev/null 2>&1 || true
mkdir -p d3
docker run -d --name sbx-th-horizon -p 127.0.0.1:18243:8100 -e INTAKE_KEY=sbx-th -e INTAKE_ALLOWED_PEERS=127.0.0.1,172.17.0.1 \
  -e LLM_BASE=http://10.255.255.1 -e AUTH_LEVELS_URL=http://10.255.255.1/a -e GOTENBERG_URL=http://10.255.255.1:3000 \
  -v "$SB/horizon.toml:/config/drop.toml:ro" -v "$SB/d3:/srv/unsorted" intake-sandbox:horizon >/dev/null
sleep 4
docker run --rm -v "$SB/horizon.toml:/config/drop.toml:ro" -e LLM_BASE=http://x -e AUTH_LEVELS_URL=http://x -e GOTENBERG_URL=http://x \
  --entrypoint python intake-sandbox:horizon -c "
import re, atomic_ui, share, themes
p = atomic_ui.PAGE
print('name in page:', share.MANIFEST['name'], '| hooks:', all(f'class=\"{c}\"' in p or f'class={c}' in p for c in ('atom','orbits','nucleus','glow')),
      '| placeholders left:', '@@' in p, '| unquoted before />:', bool(re.search(r'=[^\"\'\s>]+/>', themes.HORIZON['svg'])))"
echo "sandbox: http://127.0.0.1:18243/sending (tunnel 18243)"
