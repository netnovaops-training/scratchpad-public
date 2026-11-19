#!/bin/sh
# done_scan.sh - scan ONLY the files of a finished project (T0630, Jason 2026-11-18).
#
# Jason: "only for task and code we have moved on ... flagging only the scope with a tag ... so we dont
# duplicate work as we work on it". So nothing scans on every push. When a project is DONE:
#
#     git tag -a done/<project> -m "path: pc/ai-stack/web/
#     path: tools/stirling_31_patch.py"
#     git push nuc done/<project>          (the post-commit hook does not push tags)
#
# Each "path:" line is a git pathspec (folder, file or glob). The Gitea runner (.gitea/workflows/
# done-scan.yml) runs this script in the scan-tools image, copies just those files into a scratch folder,
# runs every free scanner on them, and opens a Gitea issue "Revisit: <project>" with the counts and the
# worst findings: the come-back-later list. A tag is scanned once; work in progress is never scanned.
#
# Usage (inside the job): done_scan.sh <tag> <repo-dir> <out-dir>
# Exit 0 even when there are findings (they go to the issue); non-zero only if the scan itself broke.
# OUTCOME: (2026-11-18) first version; see T0630 Review lines for the first real run.
set -eu
TAG="$1"; SRC="$2"; OUT="$3"
mkdir -p "$OUT"
SCOPE="$OUT/scope"; mkdir -p "$SCOPE"

# 1) scope = the "path:" lines of the annotated tag message
git -C "$SRC" tag -l --format='%(contents)' "$TAG" | sed -n 's/^path:[[:space:]]*//p' | tr -d '\r' > "$OUT/paths.txt"
if [ ! -s "$OUT/paths.txt" ]; then
  echo "tag $TAG has no 'path:' lines; nothing to scan (see tools/done_scan.sh header)" | tee "$OUT/summary.md"
  exit 0
fi
# shellcheck disable=SC2046
git -C "$SRC" ls-files -- $(cat "$OUT/paths.txt") > "$OUT/files.txt"
NFILES=$(wc -l < "$OUT/files.txt")
while IFS= read -r f; do
  mkdir -p "$SCOPE/$(dirname "$f")"; cp "$SRC/$f" "$SCOPE/$f"
done < "$OUT/files.txt"

# 2) scanners (each one's failure is recorded, never fatal)
cd "$SCOPE"
# "not run" (not 0) when a tool wrote nothing: a scanner that failed must not look like a clean result
count() { [ -s "$1" ] && jq "$2" "$1" 2>/dev/null || echo "not run"; }
gitleaks dir . --no-banner --redact --report-format json --report-path "$OUT/gitleaks.json" >/dev/null 2>&1 || true
semgrep scan --config p/default --metrics=off --quiet --json -o "$OUT/semgrep.json" . >/dev/null 2>&1 || true
TRIVY_CACHE_DIR=/tmp/trivy trivy fs --quiet --scanners vuln,misconfig,secret --format json -o "$OUT/trivy.json" . >/dev/null 2>&1 || true
osv-scanner --recursive --format json . > "$OUT/osv.json" 2>/dev/null || true
PY=$(grep -E '\.py$' "$OUT/files.txt" || true); SH=$(grep -E '\.sh$' "$OUT/files.txt" || true)
DF=$(grep -E '(^|/)Dockerfile[^/]*$' "$OUT/files.txt" || true)
# shellcheck disable=SC2086
[ -n "$PY" ] && { ruff check --output-format json $PY > "$OUT/ruff.json" 2>/dev/null || true; }
# shellcheck disable=SC2086
[ -n "$SH" ] && { shellcheck -f json $SH > "$OUT/shellcheck.json" 2>/dev/null || true; }
# shellcheck disable=SC2086
[ -n "$DF" ] && { hadolint -f json $DF > "$OUT/hadolint.json" 2>/dev/null || true; }

# 3) summary (counts + the first findings of each tool; secrets are redacted by gitleaks)
G=$(count "$OUT/gitleaks.json" 'length'); S=$(count "$OUT/semgrep.json" '.results|length')
T=$(count "$OUT/trivy.json" '[.Results[]?|(.Vulnerabilities//[])+(.Misconfigurations//[])+(.Secrets//[])|length]|add // 0')
O=$(count "$OUT/osv.json" '[.results[]?.packages[]?.vulnerabilities[]?]|length')
R=$(count "$OUT/ruff.json" 'length'); C=$(count "$OUT/shellcheck.json" 'length'); H=$(count "$OUT/hadolint.json" 'length')
{
  echo "Scan of tag \`$TAG\`: $NFILES files in scope."
  echo
  echo "| tool | findings |"; echo "|---|---|"
  echo "| gitleaks (secrets) | $G |"; echo "| semgrep (code bugs) | $S |"; echo "| trivy (vuln/misconfig/secret) | $T |"
  echo "| osv-scanner (dependencies) | $O |"; echo "| ruff (python) | $R |"; echo "| shellcheck | $C |"; echo "| hadolint (Dockerfile) | $H |"
  echo
  echo "### First findings"
  [ -s "$OUT/semgrep.json" ] && jq -r '.results[:10][]|"- semgrep `\(.path):\(.start.line)` \(.check_id|split(".")|last): \(.extra.message|.[0:140])"' "$OUT/semgrep.json" 2>/dev/null || true
  [ -s "$OUT/gitleaks.json" ] && jq -r '.[:10][]|"- gitleaks `\(.File):\(.StartLine)` \(.RuleID) (value redacted)"' "$OUT/gitleaks.json" 2>/dev/null || true
  [ -s "$OUT/ruff.json" ] && jq -r '.[:10][]|"- ruff `\(.filename|sub(".*/work/";"")):\(.location.row)` \(.code) \(.message)"' "$OUT/ruff.json" 2>/dev/null || true
  [ -s "$OUT/shellcheck.json" ] && jq -r '.[:10][]|"- shellcheck `\(.file):\(.line)` SC\(.code) \(.message|.[0:120])"' "$OUT/shellcheck.json" 2>/dev/null || true
  [ -s "$OUT/hadolint.json" ] && jq -r '.[:10][]|"- hadolint `\(.file):\(.line)` \(.code) \(.message|.[0:120])"' "$OUT/hadolint.json" 2>/dev/null || true
  echo
  echo "Files in scope:"; sed 's/^/- /' "$OUT/files.txt" | head -50
} > "$OUT/summary.md"
cat "$OUT/summary.md"
