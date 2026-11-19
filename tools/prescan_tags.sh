#!/bin/sh
# prescan_tags.sh - tag every not-yet-worked-on project for a Gitea pre-scan (T0630, Jason 2026-11-18:
# "it can run the gitea scans for later projects too ... save time later by having code already in a better
# state"). One scan/<project>-<date> tag per group; the runner opens one "Pre-scan" issue each (1 job at a time).
# Run from D:/scratchpad. Tags only: no code changes. Re-run on a later date to get fresh scans.
set -eu
D=$(date +%Y%m%d)
t() { name="$1"; shift; msg="Pre-scan $name ($D)"; for p in "$@"; do msg="$msg
path: $p"; done; git tag -a "scan/$name-$D" -m "$msg"; echo "tagged scan/$name-$D"; }
t ai-stack-rust pc/ai-stack/rust/
t ai-stack-scratchpad pc/ai-stack/scratchpad/
t ai-stack-pi pc/ai-stack/pi/ pc/ai-stack/pi-digits/
t ai-stack-scripts pc/ai-stack/scripts/ pc/ai-stack/shared/ ':(glob)pc/ai-stack/*.ps1' ':(glob)pc/ai-stack/*.cmd'
t ai-stack-voice-chat pc/ai-stack/hermes/ pc/ai-stack/telephony/ pc/ai-stack/voice/ pc/ai-stack/stt/ pc/ai-stack/telegram/
t ai-stack-small-apps pc/ai-stack/peewee-tools/ pc/ai-stack/homepage/ pc/ai-stack/colibri/ pc/ai-stack/files/ pc/ai-stack/collage/ pc/ai-stack/tables/ pc/ai-stack/metrics/ pc/ai-stack/dj/ pc/ai-stack/web/ pc/ai-stack/watchdog/ pc/ai-stack/printq/ pc/ai-stack/litellm/ pc/ai-stack/config/
t phone-apps pc/apps/
t pc-tools pc/pc-cleanup/ pc/doc-gather/ pc/secrets-tools/ pc/www-root/ pc/desktop/ pc/archive-tools/ pc/checks/
t nuc-services nuc/weather-validation/ nuc/intake/ nuc/backlog/
