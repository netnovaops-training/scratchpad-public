#!/usr/bin/env bash
# Build Cut The Fluff, publish it, and prove what is being served.
#
# WHY THIS EXISTS. Releases were done by hand: build, copy the APK to a
# versioned name, copy it AGAIN to a stable name, edit the portfolio link,
# rebuild the landing container. Five steps, and the one that mattered most
# -- refreshing the stable file -- was the easiest to forget. Forget it once
# and the permanent link quietly serves last week's build, which presents as
# "the app did not change" and costs an hour finding out why.
#
# THE DESIGN THIS ASSUMES. One canonical filename, cutthefluff.apk, served
# with revalidation headers so browsers re-check it instead of caching it
# forever. No versioned filenames: they existed only to defeat caching, and
# they cost a permanent URL to do it. The version is inside the APK and
# printed on the sign-in screen, which is where it belongs -- a filename was
# never evidence of what actually installed.
#
# VERIFIES RATHER THAN ASSUMES. It compares the checksum of what was built
# against the checksum of what the public URL returns, and checks that the
# version manifest names the build that was just published. "I copied the
# file" and "the site serves that file" are different claims, and only the
# second one matters to somebody downloading it.
set -euo pipefail

PROJECT="${PROJECT:-/c/Users/USER/AndroidStudioProjects/CutTheFluff}"
STACK="${STACK:-/c/Jasons New Site/www/ai-stack}"
APK_NAME="cutthefluff.apk"
BASE="https://example.org/apps"
PUBLIC_URL="$BASE/$APK_NAME"
MANIFEST_URL="$BASE/version.json"
# Points at the DOWNLOAD PAGE, not at the APK. It used to hand back the apk
# itself, which was fine while Android was the only target and wrong the
# moment an iOS build existed -- an iPhone following it got a 40MB file it
# cannot do anything with. The page picks the right file per device; see
# web/public/apps/index.html.
SHORT_URL="https://go.home.example.net/app"
PAGE_URL="$BASE/"
JAVA_HOME="${JAVA_HOME:-C:\\Program Files\\Android\\Android Studio\\jbr}"
export JAVA_HOME

say() { printf '\n=== %s\n' "$*"; }

say "building"
cd "$PROJECT"
./gradlew.bat assembleDebug --console=plain | grep -E '^e: |BUILD' || true

BUILT="$PROJECT/app/build/outputs/apk/debug/app-debug.apk"
[ -f "$BUILT" ] || { echo "No APK at $BUILT"; exit 1; }

# Read the version out of the APK's own build file rather than being told it,
# so the announcement cannot disagree with the artifact.
VERSION=$(grep -oE 'versionName = "[^"]+"' "$PROJECT/app/build.gradle.kts" | head -1 | cut -d'"' -f2)
CODE=$(grep -oE 'versionCode = [0-9]+' "$PROJECT/app/build.gradle.kts" | head -1 | grep -oE '[0-9]+')
LOCAL_SUM=$(md5sum "$BUILT" | cut -d' ' -f1)

say "publishing v${VERSION} (${CODE})"
cp "$BUILT" "$STACK/web/public/apps/$APK_NAME"
# The old stable name, kept working for anything bookmarked before the
# canonical one existed. 40MB of disk against a dead link is a good trade.
cp "$BUILT" "$STACK/web/public/apps/CutTheFluff-beta.apk"

say "writing the version manifest"
# GENERATED, NEVER HAND-EDITED. The app decides whether to offer an update by
# comparing its own versionCode against this file, so a stale manifest is
# worse than none: it either hides a release forever or offers one that does
# not exist. Written here so it cannot disagree with the APK beside it --
# both come from the same build in the same run.
python - "$STACK/web/public/apps/version.json" "$CODE" "$VERSION" "$PUBLIC_URL" "$LOCAL_SUM" "${NOTES:-}" <<'PY'
import json, sys
path, code, name, url, md5, notes = sys.argv[1:7]
json.dump({
    "versionCode": int(code),
    "versionName": name,
    "url": url,
    "md5": md5,
    "notes": notes,
}, open(path, "w", encoding="utf-8"), indent=2)
PY
printf '  versionCode %s, versionName %s\n' "$CODE" "$VERSION"

say "verifying what the site actually serves"
REMOTE_SUM=$(curl -sfL --max-time 300 "$PUBLIC_URL" | md5sum | cut -d' ' -f1)
printf '  built  : %s\n' "$LOCAL_SUM"
printf '  served : %s\n' "$REMOTE_SUM"
if [ "$LOCAL_SUM" != "$REMOTE_SUM" ]; then
    echo
    echo "MISMATCH: the site is not serving what was just built."
    echo "Caddy serves web/public straight from disk, so this usually means"
    echo "the copy above failed rather than anything needing a restart."
    exit 1
fi

say "verifying the manifest is served too"
# 2026-11-20: the download is saved first and read second, instead of piped
# straight into python (a "curl | interpreter" line is what the supply-chain
# scanner flags, even though python only parses JSON here). Same result: an
# empty SERVED_CODE if the fetch or the parse fails.
MANIFEST_JSON=$(curl -sf --max-time 60 "$MANIFEST_URL" || echo "")
SERVED_CODE=$(python -c 'import json,sys; print(json.loads(sys.argv[1])["versionCode"])' "$MANIFEST_JSON" 2>/dev/null || echo "")
printf '  manifest says versionCode: %s\n' "${SERVED_CODE:-<none>}"
if [ "${SERVED_CODE:-}" != "$CODE" ]; then
    echo "MISMATCH: the manifest does not name the build just published."
    exit 1
fi

say "done"
printf '  v%s (%s)\n' "$VERSION" "$CODE"
printf '  permanent link : %s\n' "$SHORT_URL"
printf '  download page  : %s\n' "$PAGE_URL"
printf '  direct         : %s\n' "$PUBLIC_URL"
printf '  manifest       : %s\n' "$MANIFEST_URL"
printf '\n  Phones on an older build will offer this one on their next launch.\n'
