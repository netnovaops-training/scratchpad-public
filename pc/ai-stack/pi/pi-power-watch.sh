#!/usr/bin/env bash
# Report this board's power health to Kuma. Run by a systemd timer.
#
# Jason, 2026-09-13: "they are going to warn of brown outs now".
#
# WHAT IT WATCHES. `vcgencmd get_throttled` returns a bitmask the SoC
# maintains itself. It is the only authoritative source for this -- a
# browning-out Pi still answers pings, still accepts SSH, and still
# reports a healthy uptime, right up until it stops answering anything
# at all. That is exactly how the Pi 3 was lost: 0x50005 the whole time,
# and nothing looking at it.
#
#   bit 0   0x1       under-voltage RIGHT NOW
#   bit 1   0x2       ARM frequency capped now
#   bit 2   0x4       currently throttled
#   bit 3   0x8       soft temperature limit active now
#   bit 16  0x10000   under-voltage has occurred since boot
#   bit 17  0x20000   frequency capping has occurred since boot
#   bit 18  0x40000   throttling has occurred since boot
#   bit 19  0x80000   soft temperature limit has occurred since boot
#
# THE LIVE BITS AND THE SINCE-BOOT BITS MEAN DIFFERENT THINGS, and this
# is the distinction the script exists to make. A since-boot bit is
# history: it may be from a five-second dip during a reboot three weeks
# ago, and it never clears until the board is power-cycled. Alerting on
# it means an alert that can never be resolved. A live bit is happening
# now and deserves a phone notification.
#
# So: live bits -> DOWN. Since-boot only -> UP, with the history in the
# message where it is visible without being noisy.
#
# Config lives in /etc/default/pi-power-watch:
#   KUMA_PUSH_URL=http://192.168.1.69:3002/api/push
#   KUMA_PUSH_TOKEN=...
set -u

CONF=/etc/default/pi-power-watch
[ -r "$CONF" ] && . "$CONF"

KUMA_PUSH_URL="${KUMA_PUSH_URL:-http://192.168.1.69:3002/api/push}"
KUMA_PUSH_TOKEN="${KUMA_PUSH_TOKEN:-}"

if [ -z "$KUMA_PUSH_TOKEN" ]; then
    echo "KUMA_PUSH_TOKEN not set in $CONF" >&2
    exit 1
fi

raw=$(vcgencmd get_throttled 2>/dev/null || echo "throttled=0x0")
hex=${raw#*=}
val=$((hex))

live=""
hist=""
(( val & 0x1 ))     && live="$live under-voltage"
(( val & 0x2 ))     && live="$live freq-capped"
(( val & 0x4 ))     && live="$live throttled"
(( val & 0x8 ))     && live="$live temp-limited"
(( val & 0x10000 )) && hist="$hist under-voltage"
(( val & 0x20000 )) && hist="$hist freq-capped"
(( val & 0x40000 )) && hist="$hist throttled"
(( val & 0x80000 )) && hist="$hist temp-limited"

# Temperature too, since it is free and explains a soft temp limit.
temp=$(vcgencmd measure_temp 2>/dev/null | sed 's/temp=//;s/'"'"'C//')
volts=$(vcgencmd measure_volts core 2>/dev/null | sed 's/volt=//;s/V//')

if [ -n "$live" ]; then
    status=down
    msg="NOW:${live} ($hex) core=${volts:-?}V temp=${temp:-?}C"
elif [ -n "$hist" ]; then
    status=up
    msg="ok now; since boot:${hist} ($hex) core=${volts:-?}V temp=${temp:-?}C"
else
    status=up
    msg="clean ($hex) core=${volts:-?}V temp=${temp:-?}C"
fi

# --max-time so a wedged Kuma cannot leave this hanging until the next
# timer tick stacks another copy on top of it.
curl -fsS --max-time 15 -o /dev/null \
    --data-urlencode "status=$status" \
    --data-urlencode "msg=$msg" \
    --data-urlencode "ping=" \
    -G "$KUMA_PUSH_URL/$KUMA_PUSH_TOKEN" 2>/dev/null

rc=$?
# Log locally too. If Kuma is the thing that is down, this is the only
# record that the board noticed -- and on these machines /var/log is a
# tmpfs, so it costs no SD-card writes.
logger -t pi-power-watch "$status: $msg (push rc=$rc)"
exit 0
