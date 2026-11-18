#!/usr/bin/env bash
# ngrams_fetch.sh - T1018 (Jason 11-18: "do the recommended"): rebuild ~/languagetool-ngrams, lost in the 11-15 wipe.
# Downloads LanguageTool's English n-gram set (ngrams-en-20150817.zip, 8,961,853,713 bytes, languagetool.org),
# checks the size and the zip, unpacks into ~/languagetool-ngrams (the container's /ngrams; it expects /ngrams/en),
# then restarts the languagetool container. Resumable (wget -c). Log: ~/ngrams_fetch.log.
# OUTCOME: see the T1018 Review line written when it finishes.
set -euo pipefail
URL=https://languagetool.org/download/ngram-data/ngrams-en-20150817.zip
SIZE=8961853713
DL=/home/user/ngrams-download
DEST=/home/user/languagetool-ngrams
LOG=/home/user/ngrams_fetch.log
exec >>"$LOG" 2>&1
echo "[$(date '+%F %T')] start"
mkdir -p "$DL" "$DEST"
wget -q -c -O "$DL/ngrams-en.zip" "$URL"
got=$(stat -c %s "$DL/ngrams-en.zip")
[ "$got" = "$SIZE" ] || { echo "size $got != $SIZE, stop"; exit 1; }
# no unzip on the NUC (no sudo): Python's zipfile tests CRCs and unpacks
python3 - "$DL/ngrams-en.zip" "$DEST" <<'PY'
import sys, zipfile
z = zipfile.ZipFile(sys.argv[1])
bad = z.testzip()
if bad:
    sys.exit("zip test failed at " + bad)
print("zip test ok")
z.extractall(sys.argv[2])
PY
echo "unpacked: $(du -sh "$DEST" | cut -f1); top: $(ls "$DEST" | tr '\n' ' ')"
docker restart languagetool >/dev/null && echo "languagetool restarted"
sleep 60
docker logs --since 2m languagetool 2>&1 | grep -i -E "ngram|language ?model|error" | tail -5 || true
echo "[$(date '+%F %T')] done (zip kept in $DL until checked; move it to trash after)"
