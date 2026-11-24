#!/bin/bash
# nuc_hwmon_root_setup.sh - the ONE sudo step on the NUC for hardware-health monitoring (2026-11-24).
#
#   sudo bash ~/hwmon/nuc_hwmon_root_setup.sh
#
# Does (nothing else):
#   1. Copies nuc_smart_prom.py to /usr/local/sbin (root-owned, so a user-level edit can never run as root).
#   2. Adds /etc/cron.d/hwmon-smart: that script every 5 minutes as root (SMART reads need root).
#      It writes /home/user/hwmon/textfile/nuc_smart.prom, which node-exporter already reads.
#   3. Runs it once and prints the NUC drives' temperature, wear, media errors and SMART verdict.
# smartmontools 7.5 is already installed (apt). Reads only: no self-test, no setting changed.
# Undo: sudo rm /etc/cron.d/hwmon-smart /usr/local/sbin/nuc_smart_prom.py
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
install -o root -g root -m 0755 /home/user/hwmon/nuc_smart_prom.py /usr/local/sbin/nuc_smart_prom.py
cat > /etc/cron.d/hwmon-smart <<'EOF'
# hardware health (2026-11-24): NUC drive SMART -> /home/user/hwmon/textfile/nuc_smart.prom (node-exporter)
PATH=/usr/sbin:/usr/bin:/sbin:/bin
*/5 * * * * root /usr/bin/python3 /usr/local/sbin/nuc_smart_prom.py >> /var/log/hwmon-smart.log 2>&1
EOF
chmod 0644 /etc/cron.d/hwmon-smart
echo "1-2. installed /usr/local/sbin/nuc_smart_prom.py and /etc/cron.d/hwmon-smart (every 5 min)"
/usr/bin/python3 /usr/local/sbin/nuc_smart_prom.py --print | grep -E 'read_ok|field="(passed|temp_c|percentage_used|media_errors|available_spare)"'
cat /var/lib/hwmon/*.type 2>/dev/null | sed 's/^/   smartctl -d type that worked: /'
