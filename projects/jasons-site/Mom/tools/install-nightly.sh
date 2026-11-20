#!/usr/bin/env bash
# =============================================================================
#  Install the nightly growth job as a systemd timer on the Pi.
#
#      sudo bash install-nightly.sh
#      sudo bash install-nightly.sh --remove
#
#  WHY A TIMER ON THE HOST AND NOT CRON IN THE CONTAINER
#  -----------------------------------------------------
#  A cron inside the container would be invisible: no journal entry, no
#  systemctl status, and it disappears silently every time the image is
#  rebuilt. A timer on the host shows up in `systemctl list-timers`, logs to the
#  journal like everything else, and survives rebuilds because it only ever
#  calls `docker exec`.
#
#  WHAT IT DOES AT 03:20
#  ---------------------
#  Wakes the desktop, reads the day's closed tickets, writes what it learned,
#  indexes the resolutions for retrieval, mirrors anything queued to Peppermint,
#  and emails you a summary. Then leaves the desktop alone -- it does not put it
#  back to sleep, because you may have been using it.
#
#  RandomizedDelaySec spreads the start over ten minutes. Nothing else runs at
#  03:20, but a fixed time is how two unrelated jobs eventually collide on the
#  same weekly maintenance window.
# =============================================================================
set -Eeuo pipefail

CONTAINER="${CONTAINER:-momhelp}"
WHEN="${WHEN:-03:20}"
UNIT=/etc/systemd/system/momhelp-nightly.service
TIMER=/etc/systemd/system/momhelp-nightly.timer

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; D=$'\033[2m'; O=$'\033[0m'
ok(){   printf '%s  ok  %s%s\n' "$G" "$*" "$O"; }
note(){ printf '%s      %s%s\n' "$D" "$*" "$O"; }
step(){ printf '\n%s==> %s%s\n' "$C" "$*" "$O"; }
die(){  printf '%s  !!  %s%s\n' "$R" "$*" "$O" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo: sudo bash install-nightly.sh"

if [[ "${1:-}" == "--remove" ]]; then
  systemctl disable --now momhelp-nightly.timer 2>/dev/null || true
  rm -f "$UNIT" "$TIMER"
  systemctl daemon-reload
  ok "Removed. The job will no longer run."
  exit 0
fi

step "Checking the container"
docker inspect "$CONTAINER" >/dev/null 2>&1 \
  || die "No container named '$CONTAINER'. Start it first: cd /var/www/Mom && sudo docker compose up -d"
ok "$CONTAINER exists"

step "Writing the unit"
cat > "$UNIT" <<EOF
[Unit]
Description=Tech Help nightly growth job
Documentation=file:///var/www/Mom/app/distill.py
After=docker.service network-online.target
Requires=docker.service

[Service]
Type=oneshot
# The job wakes the desktop and waits for it, which can legitimately take four
# minutes on a cold boot, then runs a model call per closed ticket.
TimeoutStartSec=3600
ExecStart=/usr/bin/docker exec ${CONTAINER} python manage.py distill
# A failed night is not an emergency: nothing is lost, and the next run picks up
# from the same point because the cutoff comes from the last SUCCESSFUL run.
SuccessExitStatus=0
StandardOutput=journal
StandardError=journal
EOF
ok "$UNIT"

cat > "$TIMER" <<EOF
[Unit]
Description=Run the Tech Help nightly growth job

[Timer]
OnCalendar=*-*-* ${WHEN}:00
RandomizedDelaySec=600
# If the Pi was off at 03:20, run once it is back rather than skipping the day.
Persistent=true
Unit=momhelp-nightly.service

[Install]
WantedBy=timers.target
EOF
ok "$TIMER"

step "Enabling"
systemctl daemon-reload
systemctl enable --now momhelp-nightly.timer
ok "Enabled"

printf '\n'
systemctl list-timers momhelp-nightly.timer --no-pager || true

cat <<EOF

$(ok "Done")

  ${D}Run it right now${O}      sudo systemctl start momhelp-nightly.service
  ${D}Watch it${O}              journalctl -u momhelp-nightly -f
  ${D}When is it next${O}       systemctl list-timers momhelp-nightly.timer
  ${D}Turn it off${O}           sudo bash install-nightly.sh --remove

  You will get an email each morning listing what it learned. Everything it
  writes is editable and deletable at /Mom/admin/learned, and

      sudo docker exec ${CONTAINER} python manage.py distill-reset

  erases everything it has ever taught itself without touching your own rules,
  the tickets, or the answers.

EOF
