#!/bin/bash
# Install stack-ensure as a boot-and-every-10-minutes systemd timer.
#
# A TIMER, NOT ONLY A BOOT UNIT. The boot run covers the ordinary case (the
# NAS is slower to come up than the Pi). The repeating run covers the ones
# that are not about boot at all: the NAS rebooting later, a share being
# unlocked by hand, a transient NFS stall. A container that died at 3am
# should not still be dead at breakfast.
#
# OnBootSec=180 rather than 0: the failure being fixed is starting too
# early, so starting this early too would reproduce it.
set -euo pipefail

install -m 0755 "$(dirname "$0")/stack-ensure.sh" /usr/local/bin/stack-ensure 2>/dev/null \
  || sudo -n install -m 0755 "$(dirname "$0")/stack-ensure.sh" /usr/local/bin/stack-ensure

sudo -n tee /etc/systemd/system/stack-ensure.service >/dev/null <<'UNIT'
[Unit]
Description=Restart any ai-stack container that is not running
# Docker must be up, and remote filesystems mounted, before this is useful.
After=docker.service remote-fs.target
Wants=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/stack-ensure
# The script waits for the NFS mount itself, so give it room to.
TimeoutStartSec=600
UNIT

sudo -n tee /etc/systemd/system/stack-ensure.timer >/dev/null <<'UNIT'
[Unit]
Description=Check every 10 minutes that the stack is up

[Timer]
OnBootSec=180
OnUnitActiveSec=600
# A missed run (machine off) fires on the next boot rather than vanishing.
Persistent=true

[Install]
WantedBy=timers.target
UNIT

sudo -n systemctl daemon-reload
sudo -n systemctl enable --now stack-ensure.timer

echo "  installed. next run:"
systemctl list-timers stack-ensure.timer --no-pager | sed -n '2p' | sed 's/^/    /'
echo
echo "  running it once now:"
sudo -n systemctl start stack-ensure.service
sleep 3
sudo -n journalctl -u stack-ensure -n 5 --no-pager | sed 's/^/    /'
