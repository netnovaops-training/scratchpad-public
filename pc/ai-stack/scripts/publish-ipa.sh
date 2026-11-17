#!/usr/bin/env bash
# Publish an iOS build from Codemagic, and read its version out of the file.
#
# WHY A SEPARATE SCRIPT FROM release-app.sh. The Android build happens on this
# machine, so that script can build and publish in one run. The iOS build
# cannot: there is no Xcode on Windows, so the IPA is produced on Codemagic's
# mac_mini_m2 and arrives here as a download. This script starts from that
# downloaded file.
#
# IT READS THE VERSION OUT OF THE IPA, never from an argument. An .ipa is a
# zip with Payload/<App>.app/Info.plist inside it, and that plist is what the
# phone itself reads. Taking the version from anywhere else means the download
# page can claim a version the artifact does not have.
#
# IT ALSO CHECKS FOR THE ICON. A build with no Assets.car is a build whose
# asset catalog was not wired in -- which has happened here once already, and
# presents as a green build that installs a white default icon.
set -euo pipefail

SRC="${1:-}"
STACK="${STACK:-/c/Jasons New Site/www/ai-stack}"
DEST_DIR="$STACK/web/public/apps"
IPA_NAME="cutthefluff.ipa"
BASE="https://example.org/apps"
PUBLIC_URL="$BASE/$IPA_NAME"

[ -n "$SRC" ] || { echo "usage: publish-ipa.sh <path-to-CutTheFluff-unsigned.ipa>"; exit 1; }
[ -f "$SRC" ] || { echo "No such file: $SRC"; exit 1; }

say() { printf '\n=== %s\n' "$*"; }

say "reading the build"
# THE reconfigure(newline=...) CALL BELOW IS LOAD-BEARING. Python's text mode
# on Windows turns every newline into CRLF. `read` splits on spaces and
# newlines but NOT on a carriage return, so the CR stayed glued to the LAST
# field and shipped a literal "15.0<CR>" into the published manifest -- and
# would have shipped it to AltStore, which compares these strings for
# equality. It is invisible in terminal output, which is how it reached a
# live file before being noticed.
#
# Piping through `tr` was tried first and was worse: the escape did not
# survive being written into this script, leaving `tr -d ''` quietly deleting
# nothing. Emitting the right bytes at the source cannot be undone by
# whatever edits this file later.
read -r VERSION BUILD HAS_ICON MIN_OS < <(python - "$SRC" <<'PY'
import plistlib, sys, zipfile
sys.stdout.reconfigure(newline="\n")
z = zipfile.ZipFile(sys.argv[1])
plist = next(n for n in z.namelist()
             if n.count("/") == 2 and n.endswith(".app/Info.plist"))
p = plistlib.loads(z.read(plist))
icon = any("Assets.car" in n or "AppIcon" in n for n in z.namelist())
print(p.get("CFBundleShortVersionString", "?"), p.get("CFBundleVersion", "?"),
      "yes" if icon else "NO", p.get("MinimumOSVersion", "?"))
PY
)
SIZE=$(stat -c %s "$SRC")
LOCAL_SUM=$(md5sum "$SRC" | cut -d' ' -f1)
printf '  version   : %s (build %s)\n' "$VERSION" "$BUILD"
printf '  min iOS   : %s\n' "$MIN_OS"
printf '  app icon  : %s\n' "$HAS_ICON"
printf '  size      : %s bytes\n' "$SIZE"

if [ "$HAS_ICON" = "NO" ]; then
    echo
    echo "WARNING: no asset catalog in this build -- it will install with the"
    echo "white default icon. Publishing anyway; rebuild once ios/project.yml"
    echo "reaches the CI runner."
fi

say "publishing"
cp "$SRC" "$DEST_DIR/$IPA_NAME"

say "writing the iOS manifest"
# GENERATED, NEVER HAND-EDITED -- same rule as version.json beside it. The
# download page and the app's own update check both read this, so a
# hand-edited value is a page that lies about the file next to it.
python - "$DEST_DIR/ios.json" "$VERSION" "$BUILD" "$PUBLIC_URL" "$LOCAL_SUM" "$SIZE" "$MIN_OS" "${NOTES:-}" <<'PY'
import json, sys
path, version, build, url, md5, size, min_os, notes = sys.argv[1:9]
json.dump({
    "versionName": version,
    "build": int(build),
    "url": url,
    "md5": md5,
    "bytes": int(size),
    "minimumOS": min_os,
    # Stated in the file the page reads, so the page never has to imply that
    # tapping the link installs anything. It does not: an UNSIGNED ipa cannot
    # be installed by iOS from a web link, at any price, without a signing
    # identity. Sideloading is the whole distribution story here.
    "signed": False,
    "notes": notes,
}, open(path, "w", encoding="utf-8"), indent=2)
PY

say "writing the AltStore source"
# THE ONLY REAL SELF-UPDATE PATH THIS APP HAS ON iOS.
#
# The app itself cannot install an app -- iOS has no such API, and
# itms-services:// refuses anything not signed with a distribution
# certificate, which an unsigned build by definition is not. AltStore and
# SideStore can: they re-sign with the person's own Apple ID, so once they
# have added this source they get the update offered and installed there.
#
# APPENDS, NEVER OVERWRITES, which is why this is thirty lines rather than
# one. AltStore decides there is an update by comparing versions[0] against
# what is installed, and shows a version history from the rest. Regenerating
# the file with only the current build would throw that history away on every
# release. The existing file is read back, this build goes on the front, and
# what was already there is kept.
python - "$DEST_DIR/altstore.json" "$VERSION" "$BUILD" "$PUBLIC_URL" "$SIZE" "$MIN_OS" "${NOTES:-}" <<'PY'
import datetime, json, sys

path, version, build, url, size, min_os, notes = sys.argv[1:8]

entry = {
    "version": version,          # must equal CFBundleShortVersionString
    "buildVersion": build,       # must equal CFBundleVersion
    "date": datetime.date.today().isoformat(),
    "localizedDescription": notes or "A new build of Cut The Fluff.",
    "downloadURL": url,
    "size": int(size),
    "minOSVersion": min_os,
}

ICON = "https://example.org/apps/icon.png"

try:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)
    app = doc["apps"][0]
except (OSError, ValueError, KeyError, IndexError):
    doc = {
        "name": "Jason's household",
        "identifier": "org.example.source",
        "iconURL": ICON,
        "tintColor": "5AA2E8",
        "apps": [{
            "name": "Cut The Fluff",
            "bundleIdentifier": "org.example.cutthefluff",
            "developerName": "Site Owner",
            "subtitle": "The household assistant.",
            "localizedDescription": (
                "Chat with the household assistant, look things up on the "
                "shelf, read recipes and send it photos. Sign in with your "
                "household account."
            ),
            "iconURL": ICON,
            "tintColor": "5AA2E8",
            "category": "utilities",
            "versions": [],
        }],
    }
    app = doc["apps"][0]

# Newest first is what AltStore expects, and rebuilding the same build number
# replaces that entry rather than adding a duplicate it would never offer.
app["versions"] = [entry] + [
    v for v in app.get("versions", []) if v.get("buildVersion") != build
]

with open(path, "w", encoding="utf-8") as f:
    json.dump(doc, f, indent=2)
print("  %d version(s) listed, newest %s (build %s)"
      % (len(app["versions"]), version, build))
PY

say "verifying what the site actually serves"
REMOTE_SUM=$(curl -sfL --max-time 300 "$PUBLIC_URL" | md5sum | cut -d' ' -f1)
printf '  built  : %s\n' "$LOCAL_SUM"
printf '  served : %s\n' "$REMOTE_SUM"
[ "$LOCAL_SUM" = "$REMOTE_SUM" ] || { echo "MISMATCH: the site is not serving what was just published."; exit 1; }

say "checking the manifests for stray whitespace"
# Specifically because a CR once survived all the way into a published
# manifest. A string that merely LOOKS right in a terminal is not evidence.
python - "$DEST_DIR/ios.json" "$DEST_DIR/altstore.json" <<'PY'
import json, sys

bad = 0
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as f:
        doc = json.load(f)

    def walk(node, where=""):
        global bad
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, where + "/" + k)
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, where + "[%d]" % i)
        elif isinstance(node, str) and node != node.strip():
            bad += 1
            print("  DIRTY %s %s %r" % (path.rsplit("/", 1)[-1], where, node))

    walk(doc)

print("  clean" if not bad else "  %d field(s) need fixing" % bad)
sys.exit(1 if bad else 0)
PY

say "done"
printf '  download page  : %s/\n' "$BASE"
printf '  direct         : %s\n' "$PUBLIC_URL"
printf '  altstore source: %s/altstore.json\n' "$BASE"
