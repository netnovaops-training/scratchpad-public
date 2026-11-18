#!/bin/sh
# push-feed-changed.sh: copy only the CHANGED files of the PC's feed/ to the NUC backlog page, over SSH.
#
# Made 2026-11-17 by the to-do autopilot (T0970 step 2), beside the old push-feed.sh (kept, not edited:
# copy-test-swap). Why a new script:
#   - push-feed.sh sent the whole feed (17,134 files, ~683 MB) every run and used the LAN address
#     192.168.1.86 (T0998: Tailscale only). Nothing scheduled it; sync.log shows the last run 2026-11-15.
#   - This one sends only files newer than the stamp file .last-feed-push, over Tailscale (100.64.0.10),
#     and skips sync.lock and Synology Drive "Name clash" copies (the NUC copy made 11-17 skips them too).
# Runs every 30 min from the Windows task "Backlog feed push" (Git Bash). One-way PC -> NUC. No new port.
# Prints PASS/FAIL only; writes one line to push-feed.log. Never deletes anything on the NUC.
cd "$(dirname "$0")" || exit 1
NUC=server@100.64.0.10
STAMP=.last-feed-push
LOG=push-feed.log
NEW=.last-feed-push.new
touch "$NEW"   # take the new stamp BEFORE listing, so a file written during the push is sent next run
LIST=$(mktemp)
if [ -f "$STAMP" ]; then
  (cd feed && find . -type f -newer "../$STAMP" ! -name sync.lock ! -name '*Name clash*') > "$LIST"
else
  (cd feed && find . -type f ! -name sync.lock ! -name '*Name clash*') > "$LIST"
fi
COUNT=$(wc -l < "$LIST" | tr -d ' ')
if [ "$COUNT" = "0" ]; then
  echo "$(date '+%F %T') PASS nothing changed" >> "$LOG"; echo "PASS nothing changed"
  mv -f "$NEW" "$STAMP"; rm -f "$LIST"; exit 0
fi
# tar -T reads the file list; names with spaces are fine (one per line).
if tar -C feed -cf - -T "$LIST" \
   | ssh -o BatchMode=yes -o ConnectTimeout=20 "$NUC" 'tar -C ~/backlog-page/feed -xf -'; then
  mv -f "$NEW" "$STAMP"
  echo "$(date '+%F %T') PASS $COUNT files pushed" >> "$LOG"; echo "PASS $COUNT files pushed"
  rm -f "$LIST"; exit 0
else
  rm -f "$NEW"   # keep the old stamp so the same files are retried next run
  echo "$(date '+%F %T') FAIL push of $COUNT files" >> "$LOG"; echo "FAIL feed push"
  rm -f "$LIST"; exit 1
fi
