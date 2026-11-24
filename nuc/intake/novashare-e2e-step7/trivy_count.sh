#!/usr/bin/env bash
# trivy_count.sh IMAGE OUT.json - Trivy 0.58.1 (as the 2026-11-24 audit) HIGH/CRITICAL scan of a local image.
# Prints: critical, high, high-with-a-fix. The cache is a temp folder, moved to the trash afterwards.
set -euo pipefail
IMG=$1; OUT=$2; C=$(mktemp -d ~/sandbox/trivy-cache-XXXXXX)
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock -v "$C:/root/.cache" -v "$(dirname "$OUT"):/out" \
  aquasec/trivy:0.58.1 image --quiet --severity HIGH,CRITICAL --format json -o "/out/$(basename "$OUT")" "$IMG" >/dev/null
trash-put "$C"
python3 - "$OUT" <<'EOF'
import json, sys
d = json.load(open(sys.argv[1]))
v = [x for r in d.get("Results", []) for x in (r.get("Vulnerabilities") or [])]
crit = sum(x["Severity"] == "CRITICAL" for x in v)
high = [x for x in v if x["Severity"] == "HIGH"]
print(f"critical={crit} high={len(high)} high_with_fix={sum(bool(x.get('FixedVersion')) for x in high)}"
      f" os={d.get('Metadata', {}).get('OS', {}).get('Name', '?')}")
EOF
