#!/usr/bin/env bash
# Install the power watcher and its timer. Run ON A PI.
#
#     KUMA_PUSH_TOKEN=xxxx bash pi-power-watch-install.sh
#
# Needs the push token from kuma-add-power.py, which creates the matching
# monitor in Kuma.
set -euo pipefail

TOKEN="${KUMA_PUSH_TOKEN:-}"
URL="${KUMA_PUSH_URL:-http://192.168.1.69:3002/api/push}"
[ -n "$TOKEN" ] || { echo "  KUMA_PUSH_TOKEN not set"; exit 1; }

command -v vcgencmd >/dev/null || { echo "  vcgencmd missing -- not a Pi?"; exit 1; }

echo "  1. script -> /usr/local/bin/pi-power-watch"
sudo install -m 0755 "$(dirname "$0")/pi-power-watch.sh" /usr/local/bin/pi-power-watch

echo "  2. config -> /etc/default/pi-power-watch"
# 0600 and root-owned: the token is a write credential for a Kuma
# monitor. Not catastrophic if leaked, but there is no reason for every
# user on the box to read it.
printf 'KUMA_PUSH_URL=%s\nKUMA_PUSH_TOKEN=%s\n' "$URL" "$TOKEN" \
    | sudo tee /etc/default/pi-power-watch >/dev/null
sudo chmod 0600 /etc/default/pi-power-watch

echo "  3. systemd units"
sudo tee /etc/systemd/system/pi-power-watch.service >/dev/null <<'UNIT'
[Unit]
Description=Report under-voltage and throttling to Kuma
# Do not fail the boot if the network is slow; the timer will retry.
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/pi-power-watch
# The script exits 0 even when the push fails, so a Kuma outage does not
# mark this unit failed and bury the real signal in unit noise.
UNIT

sudo tee /etc/systemd/system/pi-power-watch.timer >/dev/null <<'UNIT'
[Unit]
Description=Report under-voltage to Kuma every 2 minutes

[Timer]
# 30s after boot: a brownout during boot is exactly the case worth
# catching, and waiting two minutes for the first sample would miss it.
OnBootSec=30s
OnUnitActiveSec=120s
# Without this every Pi pushes on the same second forever.
RandomizedDelaySec=10s
AccuracySec=5s

[Install]
WantedBy=timers.target
UNIT

sudo systemctl daemon-reload
sudo systemctl enable --now pi-power-watch.timer >/dev/null

echo "  4. first run"
sudo systemctl start pi-power-watch.service
sleep 3
journalctl -t pi-power-watch -n 3 --no-pager 2>/dev/null | sed 's/^/     /'
echo
systemctl list-timers pi-power-watch.timer --no-pager 2>/dev/null | sed -n '2p' | sed 's/^/     /'
