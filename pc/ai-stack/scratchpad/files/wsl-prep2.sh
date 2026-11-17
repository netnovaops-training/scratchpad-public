#!/bin/bash
# Test-only preparation of the WSL "junstest" distro to act like Robin's laptop.
set -e
cat > /etc/wsl.conf <<'EOF'
[boot]
systemd=true
[interop]
appendWindowsPath=false
[user]
default=robin
EOF
id robin >/dev/null 2>&1 || useradd -m -s /bin/bash -G sudo robin
echo 'robin ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-junstest
chmod 440 /etc/sudoers.d/90-junstest
systemctl enable ssh >/dev/null 2>&1 || true
install -d -m 700 -o robin -g robin /home/robin/.ssh
cp /mnt/c/Users/USER/.ssh/id_ed25519.pub /home/robin/.ssh/authorized_keys
chown robin:robin /home/robin/.ssh/authorized_keys
chmod 600 /home/robin/.ssh/authorized_keys
apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unzip >/dev/null; echo prepared
