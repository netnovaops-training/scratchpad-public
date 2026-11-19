#!/usr/bin/env python3
"""ci_scan_pin.py - pin the ci_scan network subnet and trust both host gateways in gitea-proxy (T0630, 2026-11-18).

Why: gitea-proxy sits on two networks (nuc-homelab_default + ci_scan). Docker then delivered Caddy's
127.0.0.1:3000 traffic via the ci_scan gateway (192.168.1.1, a random subnet), the sidecar did not trust
it, and Gitea fell back to its password page. Pinning ci_scan to 10.250.250.0/24 makes its gateway fixed.
Edits ~/nuc-homelab/docker-compose.yml and gitea-proxy.nginx.conf (writes the conf to /tmp/gp3.conf; the
caller copies it with cat > to keep the bind-mounted file's inode). Backups were taken before.
"""
from pathlib import Path

home = Path.home() / "nuc-homelab"
f = home / "docker-compose.yml"
t = f.read_text()
old = "  ci_scan:\n    name: ci_scan\n"
new = ("  ci_scan:\n    name: ci_scan\n"
       "    # pinned so the host gateway is always 10.250.250.1 (gitea-proxy.nginx.conf trusts it: with two\n"
       "    # networks Docker may hand the 127.0.0.1:3000 traffic from Caddy to the sidecar via either gateway)\n"
       "    ipam:\n      config:\n        - subnet: 10.250.250.0/24\n")
if "10.250.250.0/24" not in t:
    assert t.count(old) == 1
    f.write_text(t.replace(old, new))
print("compose ok")
g = home / "gitea-proxy.nginx.conf"
s = g.read_text()
old2 = "    172.18.0.1     $http_x_webauth_user;\n"
new2 = ("    # The sidecar is on two networks (nuc-homelab_default + ci_scan, T0630); Docker may deliver the\n"
        "    # published-port traffic through either gateway, and both gateways are the NUC host itself.\n"
        "    172.18.0.1     $http_x_webauth_user;\n    10.250.250.1   $http_x_webauth_user;\n")
if "10.250.250.1" not in s:
    assert s.count(old2) == 1
    s = s.replace(old2, new2)
# Fixed work path on a machine Jason controls; the script's own scratch file, overridable where it reads an env var.
Path("/tmp/gp3.conf").write_text(s)  # nosec B108
print("conf ok")
