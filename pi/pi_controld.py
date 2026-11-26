#!/usr/bin/env python3
"""pi_controld.py - Control D as the DNS of the Pi's VPN stack (2026-11-23). Run on the Pi in ~/stack.

    python3 pi_controld.py IMAGE_WITH_DIGEST

Jason gave the Control D endpoint "pi5" (resolver w60thuvt9e). gluetun cannot take a custom DNS-over-TLS host
(its DoT list is fixed: cloudflare, google, quad9, ...), so Control D's own proxy ctrld runs in gluetun's network
namespace on 127.0.0.1:5354 and gluetun forwards its DNS there (DNS_UPSTREAM_RESOLVER_TYPE=plain,
DNS_UPSTREAM_PLAIN_ADDRESSES=127.0.0.1:5354; plain only on loopback). ctrld asks Control D over DoH inside the
Proton tunnel. ctrld depends on gluetun STARTED, not healthy: gluetun's health check itself needs DNS.
Edits compose-download.yml (the caller made a backup). Refuses if already patched.
"""
import pathlib
import re
import sys

digest = sys.argv[1]
p = pathlib.Path.home() / "stack" / "compose-download.yml"
s = p.read_text()
if "\n  ctrld:\n" in s:
    raise SystemExit("already patched")
s, n1 = re.subn(r"^(\s*- DNS_UPSTREAM_RESOLVER_TYPE=)\S+.*$", r"\1plain   # 2026-11-23: Control D through ctrld", s, count=1, flags=re.M)
s, n2 = re.subn(r"^(\s*- DNS_UPSTREAM_PLAIN_ADDRESSES=)\S*.*$", r"\g<1>127.0.0.1:5354", s, count=1, flags=re.M)
m = re.search(r"^(\s*)- VPN_SERVICE_PROVIDER=.*$", s, re.M)
if not m:
    raise SystemExit("VPN_SERVICE_PROVIDER line not found")
ind = m.group(1)
extra = ""
if n1 == 0:
    extra += f"\n{ind}- DNS_UPSTREAM_RESOLVER_TYPE=plain   # 2026-11-23: Control D through ctrld"
if n2 == 0:
    extra += f"\n{ind}- DNS_UPSTREAM_PLAIN_ADDRESSES=127.0.0.1:5354"
s = s[:m.end()] + extra + s[m.end():]
svc = (
    "\n  ctrld:\n"
    "    # 2026-11-23: Control D (resolver w60thuvt9e, endpoint pi5) = DNS of everything in the VPN. gluetun forwards\n"
    "    # its DNS to 127.0.0.1:5354 (this container, same network namespace); ctrld asks Control D over DoH inside\n"
    "    # the Proton tunnel. Starts when gluetun STARTS, not when healthy: the gluetun health check needs DNS.\n"
    f"    image: {digest}\n"
    "    container_name: ctrld\n"
    "    restart: unless-stopped\n"
    "    network_mode: \"service:gluetun\"\n"
    "    command: [\"--listen\", \"127.0.0.1:5354\", \"--primary_upstream\", \"https://dns.controld.com/w60thuvt9e\"]\n"
    "    depends_on:\n"
    "      gluetun:\n"
    "        condition: service_started\n")
anchor = "\nvolumes:\n"
if s.count(anchor) != 1:
    raise SystemExit("volumes anchor not found")
s = s.replace(anchor, svc + anchor, 1)
p.write_text(s)
print("compose-download.yml updated (ctrld + gluetun DNS -> 127.0.0.1:5354)")
