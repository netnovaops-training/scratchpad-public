#!/bin/bash
# Tor on the Pi 3, as a CLIENT + LAN SOCKS proxy. Never a relay, never an exit.
#
# WHERE THINGS LIVE, and why:
#   DataDirectory  /var/lib/tor  (SD card)
#     Tor refuses to use a DataDirectory it does not own at mode 0700, and
#     exfat cannot represent POSIX ownership at all -- so this physically
#     cannot live on the stick. It is also tiny: the consensus and
#     descriptors are a few MB, refreshed hourly. That is not "heavy for
#     the memory card"; a torrent would be, and that goes on the stick.
#   downloads      /mnt/usb/pi3/downloads  (USB stick)
#     Anything bulk goes here, off the SD card.
#   logs           syslog (default)
#     NOT redirected to the stick on purpose: the stick is mounted
#     `nofail`, so if it is ever unplugged a file-logging Tor would fail
#     to start. A Tor daemon that dies because a USB stick moved is worse
#     than a few KB of syslog.
set -euo pipefail

echo "=== 0. host ==="
. /etc/os-release; echo "    $PRETTY_NAME  $(uname -m)  kernel $(uname -r)"

echo
echo "=== 1. install ==="
if command -v tor >/dev/null; then
  echo "    already installed: $(tor --version | head -1)"
else
  sudo apt-get update -qq
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq tor
  echo "    installed: $(tor --version | head -1)"
fi

echo
echo "=== 2. torrc ==="
# Keep the distro file; drop our settings in as a separate, readable unit.
sudo mkdir -p /etc/tor
TORRC=/etc/tor/torrc
[ -f "$TORRC.orig" ] || sudo cp "$TORRC" "$TORRC.orig"

sudo tee "$TORRC" >/dev/null <<'EOF'
# Managed for Jason's homelab. Original kept at /etc/tor/torrc.orig
#
# Role: a Tor CLIENT for the household, reachable as a SOCKS proxy on the
# LAN. Explicitly NOT a relay and NOT an exit -- running an exit node on a
# residential line invites abuse complaints and worse, and nothing here
# needs one.

SocksPort 0.0.0.0:9050

# Who may use the proxy. The LAN only; everything else refused. The Pi is
# not port-forwarded, so this is not reachable from the internet -- but
# state it anyway rather than rely on the router staying configured.
SocksPolicy accept 192.168.1.0/24
SocksPolicy accept 127.0.0.1/32
SocksPolicy reject *

# Never carry anyone else's traffic.
ExitRelay 0
PublishServerDescriptor 0
ClientOnly 1

# No control port: nothing here drives Tor programmatically, so do not
# offer an interface that could.

DataDirectory /var/lib/tor
Log notice syslog

# Bulk anything belongs on the USB stick, not the SD card.
# (downloads dir: /mnt/usb/pi3/downloads)
EOF
echo "    written"

echo
echo "=== 3. config check BEFORE restarting (a bad torrc leaves it down) ==="
sudo tor --verify-config -f "$TORRC" 2>&1 | tail -3 | sed 's/^/    /'

echo
echo "=== 4. enable + start ==="
sudo systemctl enable tor >/dev/null 2>&1 || true
sudo systemctl restart tor
sleep 8
echo "    tor:   $(systemctl is-active tor)"
echo "    tor@default: $(systemctl is-active tor@default 2>/dev/null || echo n/a)"

echo
echo "=== 5. is it listening ==="
ss -tlnp 2>/dev/null | grep -E '9050' | sed 's/^/    /' || echo "    NOT LISTENING"

echo
echo "=== 6. does it actually reach the Tor network ==="
# The real test is not "the process is up" but "a circuit completes".
if curl -s --max-time 60 --socks5-hostname 127.0.0.1:9050 \
     https://check.torproject.org/api/ip 2>/dev/null | tee /tmp/torcheck; then
  echo
else
  echo "    curl through the proxy failed (circuit not built yet?)"
fi
