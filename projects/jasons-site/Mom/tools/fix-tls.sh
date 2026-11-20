#!/usr/bin/env bash
# =============================================================================
#  ERR_SSL_PROTOCOL_ERROR on example.org -- find out why.
#
#      sudo bash fix-tls.sh            # diagnose only
#      sudo bash fix-tls.sh --fix      # also try to repair
#
#  That error means one specific thing: whatever answered on port 443 did not
#  speak TLS. Not a bad certificate, not an expired one -- the browser never
#  got a TLS handshake at all. There are only a few ways that happens:
#
#    1. Caddy is not running, and something else answered.
#    2. Caddy is running but another process holds 443.
#    3. Caddy is running and holds 443, but its config has no site block for
#       this hostname, so it has no certificate to present.
#    4. Apache is bound to 443 directly, serving plain HTTP on the TLS port.
#
#  This checks all four, in that order.
# =============================================================================
set -Eeuo pipefail

FIX=false
[[ "${1:-}" == "--fix" ]] && FIX=true
HOSTN="${HOSTN:-example.org}"
CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; D=$'\033[2m'; O=$'\033[0m'
step(){ printf '\n%s==> %s%s\n' "$C" "$*" "$O"; }
ok(){   printf '%s  ok  %s%s\n' "$G" "$*" "$O"; }
note(){ printf '%s      %s%s\n' "$D" "$*" "$O"; }
warn(){ printf '%s  !   %s%s\n' "$Y" "$*" "$O"; }
bad(){  printf '%s  XX  %s%s\n' "$R" "$*" "$O"; }

[[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }

# --- 1. who holds 80 and 443 --------------------------------------------------
step "Who is listening on 80 and 443"
LISTEN="$(ss -ltnp 2>/dev/null | grep -E ':(80|443)\b' || true)"
if [[ -z "$LISTEN" ]]; then
  bad "NOTHING is listening on 80 or 443."
  note "That alone explains the error: the browser connected to your router,"
  note "the router forwarded to this Pi, and nothing answered."
else
  printf '%s\n' "$LISTEN" | while IFS= read -r l; do note "$l"; done
fi

grep -q 'caddy' <<<"$LISTEN" && ok "Caddy holds the ports" || warn "Caddy does NOT hold the ports"
if grep -qE 'apache|httpd' <<<"$LISTEN"; then
  bad "Apache is bound to 80/443 directly."
  note "Apache should be on 8080 only. Bound to 443 it serves plain HTTP on the"
  note "TLS port, which is exactly ERR_SSL_PROTOCOL_ERROR."
  note "Fix: edit /etc/apache2/ports.conf to Listen 8080, then:"
  note "  sudo systemctl restart apache2"
fi

# --- 2. the caddy service -----------------------------------------------------
step "The Caddy service"
if systemctl is-active --quiet caddy; then
  ok "caddy.service is active"
else
  bad "caddy.service is NOT running"
  note "Last 20 lines of its log:"
  journalctl -u caddy -n 20 --no-pager 2>/dev/null | while IFS= read -r l; do note "$l"; done
fi

# --- 3. a competing caddy container ------------------------------------------
step "Docker containers on 80/443"
# The caddy-proxy container is retired but set to restart=always in the past;
# a Docker daemon restart can bring back something you thought was gone, and
# then two Caddies fight over the same port.
CONT="$(docker ps --format '{{.Names}}|{{.Ports}}' 2>/dev/null | grep -E ':(80|443)->' || true)"
if [[ -n "$CONT" ]]; then
  warn "A container is publishing 80 or 443:"
  printf '%s\n' "$CONT" | while IFS= read -r l; do note "$l"; done
  note "If this is caddy-proxy, it is the retired one and it is fighting the"
  note "systemd service. Stop it for good:"
  note "  sudo docker update --restart=no caddy-proxy"
  note "  sudo docker stop caddy-proxy"
else
  ok "No container is publishing 80 or 443"
fi

# --- 4. the config ------------------------------------------------------------
step "The configuration"
if [[ ! -f "$CADDYFILE" ]]; then
  bad "$CADDYFILE does not exist"
else
  if caddy validate --config "$CADDYFILE" --adapter caddyfile >/tmp/cv.$$ 2>&1; then
    ok "Config is valid"
  else
    bad "Config is INVALID -- Caddy will refuse to start or reload"
    head -20 /tmp/cv.$$ | while IFS= read -r l; do note "$l"; done
  fi
  rm -f /tmp/cv.$$

  if grep -q "^$HOSTN" "$CADDYFILE" || grep -q "^[[:space:]]*$HOSTN" "$CADDYFILE"; then
    ok "There is a site block for $HOSTN"
  else
    bad "NO site block for $HOSTN"
    note "Without one Caddy has no certificate for this name, so it cannot"
    note "complete a TLS handshake. This is the most common cause after a"
    note "config edit that dropped or renamed the block."
    note "Site names present:"
    grep -oE '^[a-z0-9.*-]+\.[a-z]{2,}' "$CADDYFILE" | sort -u |
      while IFS= read -r l; do note "  $l"; done
  fi
fi

# --- 5. certificates ----------------------------------------------------------
step "Certificates"
CERTDIR=/var/lib/caddy/.local/share/caddy/certificates
if [[ -d "$CERTDIR" ]]; then
  find "$CERTDIR" -name '*.crt' 2>/dev/null | while IFS= read -r c; do
    note "$(basename "$c")  ($(stat -c %y "$c" 2>/dev/null | cut -d' ' -f1))"
  done
  find "$CERTDIR" -name "*${HOSTN}*" -name '*.crt' >/dev/null 2>&1 &&
    ok "A certificate exists for $HOSTN" || warn "No certificate found for $HOSTN"
else
  warn "No certificate directory at $CERTDIR"
  note "If Caddy has never successfully obtained one, check that port 80 is"
  note "reachable from the internet -- the ACME HTTP challenge needs it, and a"
  note "router forwarding only 443 will fail every issuance forever."
fi

# --- 6. what the port actually says -------------------------------------------
step "What port 443 actually returns"
if command -v openssl >/dev/null; then
  if timeout 8 openssl s_client -connect 127.0.0.1:443 -servername "$HOSTN" </dev/null 2>&1 |
       grep -q 'BEGIN CERTIFICATE'; then
    ok "A TLS handshake on 443 succeeds locally"
    note "If the browser still fails, the problem is between your router and"
    note "the Pi, not on the Pi. Check the port forward for 443."
  else
    bad "No TLS handshake on 443 locally"
    note "Whatever is on that port is not speaking TLS. See the checks above."
  fi
fi

# --- 7. repair ----------------------------------------------------------------
if $FIX; then
  step "Attempting repair"
  docker update --restart=no caddy-proxy >/dev/null 2>&1 || true
  docker stop caddy-proxy >/dev/null 2>&1 || true
  install -d -o caddy -g caddy -m 755 /var/log/caddy 2>/dev/null || true
  if caddy validate --config "$CADDYFILE" --adapter caddyfile >/dev/null 2>&1; then
    systemctl restart caddy && sleep 3
    if systemctl is-active --quiet caddy; then
      ok "Caddy restarted and is running"
    else
      bad "Caddy still will not start"
      journalctl -u caddy -n 25 --no-pager | while IFS= read -r l; do note "$l"; done
    fi
  else
    bad "Refusing to restart: the config does not validate. Fix it first."
  fi
fi

printf '\n%s  Most useful next command:%s\n' "$C" "$O"
note "sudo journalctl -u caddy -n 50 --no-pager"
printf '\n'
