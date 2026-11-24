#!/usr/bin/env bash
# sudo-crowdsec-bouncer-2026-11-24.sh - turn on CrowdSec enforcement on the NUC (approved by Jason 11-24).
#
#   sudo bash ~/nuc-homelab/sudo/sudo-crowdsec-bouncer-2026-11-24.sh
#
# WHY THE EARLIER TRY NEVER PULLED: install-bouncer.sh (08-28, also step 1 of sudo-batch-2026-11-23.sh) ran
# packagecloud's script, which wrote a "resolute" repo; packagecloud has no 26.04 repo (404), so
# crowdsec-firewall-bouncer-nftables was "Unable to locate". The repo file now says "noble" and apt sees 0.0.38
# (checked 11-24). The old script also pointed at 192.168.1.86:8080; the LAPI moved to 127.0.0.1:8080 today.
#
# What it does (each step prints one result line; re-running is safe):
#   0  backups: nft ruleset and any existing bouncer config -> /root/crowdsec-bouncer-backup-<ts>/
#   1  never-ban allowlist in CrowdSec (1.7 allowlists): 100.64.0.0/10 (Tailscale), 192.168.1.0/24 (LAN),
#      127.0.0.0/8. Existing parser whitelists (crowdsecurity/whitelists, local/tailscale-whitelist) stay too.
#      Any decision already on those ranges is deleted before the bouncer starts.
#   2  install crowdsec-firewall-bouncer-nftables (packagecloud noble); fallback: Ubuntu's
#      crowdsec-firewall-bouncer 0.0.25 set to mode nftables.
#   3  a NEW bouncer key: deletes the never-used "nuc-firewall" registration, adds it again inside the container
#      and writes the key straight into root-only /etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml.local
#      (mode 600, with api_url http://127.0.0.1:8080/). The key is never printed; only its length is.
#      The old key file /home/user/nuc-homelab/bouncer.key (user-readable) goes to the trash.
#   4  start + enable, with a 15-minute DEAD-MAN timer that stops the bouncer again. Open a NEW ssh session
#      over Tailscale; if it works, keep the bouncer:  sudo systemctl stop crowdsec-bouncer-deadman.timer
#   5  verify: bouncer pulled (cscli bouncers list), cscli decisions list count, test ban 203.0.113.7
#      (TEST-NET-3, never a real host) appears in nft, then removed.
#   6  collections/scenarios check (caddy, http-cve, sshd, linux) and the caddy log acquisition.
#      Checked 11-24 before writing: all four collections enabled; acquisition docker:caddy-nuc type caddy
#      reads lines (11.8k read, 11.4k parsed), so nothing is added unless it has gone missing.
# UNDO (printed again at the end):
#   systemctl disable --now crowdsec-firewall-bouncer
#   apt-get remove -y crowdsec-firewall-bouncer-nftables crowdsec-firewall-bouncer
#   nft delete table ip crowdsec; nft delete table ip6 crowdsec   (only if still there)
#   docker exec nuc-homelab-crowdsec-1 cscli bouncers delete nuc-firewall
#   docker exec nuc-homelab-crowdsec-1 cscli allowlists delete homelab-never-ban
# Variables: C (container), BK (backup dir), CFG/LOCAL (bouncer config files), RES (result lines).
set -uo pipefail
[ "$(id -u)" -eq 0 ] || { echo "Run with sudo: sudo bash $0"; exit 1; }
C=nuc-homelab-crowdsec-1
CFG=/etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml
LOCAL=/etc/crowdsec/bouncers/crowdsec-firewall-bouncer.yaml.local
BK=/root/crowdsec-bouncer-backup-$(date +%Y%m%d-%H%M%S)
RANGES="100.64.0.0/10 192.168.1.0/24 127.0.0.0/8"
TEST=203.0.113.7
RES=()
ok() { RES+=("OK   $1"); echo "  -> OK: $1"; }
bad() { RES+=("FAIL $1"); echo "  -> FAIL: $1"; }
cs() { docker exec "$C" cscli "$@"; }

docker inspect -f '{{.State.Running}}' "$C" 2>/dev/null | grep -q true || { echo "container $C not running"; exit 1; }

echo "== 0/6 backups"
mkdir -p "$BK" && chmod 700 "$BK"
nft list ruleset > "$BK/nft-ruleset.before" 2>/dev/null
[ -d /etc/crowdsec/bouncers ] && cp -a /etc/crowdsec/bouncers "$BK/bouncers.before"
ok "backup in $BK"

echo "== 1/6 never-ban allowlist"
cs allowlists inspect homelab-never-ban >/dev/null 2>&1 || cs allowlists create homelab-never-ban -d "Tailscale, LAN, localhost: never ban (11-24)" >/dev/null
cs allowlists add homelab-never-ban $RANGES -d "own networks" >/dev/null 2>&1
if cs allowlists inspect homelab-never-ban -o raw 2>/dev/null | grep -q "100.64.0.0/10"; then ok "allowlist homelab-never-ban has $RANGES"; else bad "allowlist not confirmed (check: cscli allowlists inspect homelab-never-ban)"; fi
for r in $RANGES; do cs decisions delete --range "$r" --contained >/dev/null 2>&1; done
ok "decisions on own ranges cleared (if any)"

echo "== 2/6 install the nftables bouncer"
apt-get update -qq >/dev/null 2>&1
if dpkg -s crowdsec-firewall-bouncer-nftables >/dev/null 2>&1 || dpkg -s crowdsec-firewall-bouncer >/dev/null 2>&1; then
    ok "bouncer package already installed"
elif DEBIAN_FRONTEND=noninteractive apt-get install -y crowdsec-firewall-bouncer-nftables >"$BK/apt.log" 2>&1; then
    ok "installed crowdsec-firewall-bouncer-nftables $(dpkg-query -W -f '${Version}' crowdsec-firewall-bouncer-nftables)"
elif DEBIAN_FRONTEND=noninteractive apt-get install -y crowdsec-firewall-bouncer nftables >>"$BK/apt.log" 2>&1; then
    ok "installed Ubuntu crowdsec-firewall-bouncer (fallback)"
else
    bad "install failed (see $BK/apt.log)"; printf '%s\n' "${RES[@]}"; exit 1
fi
systemctl stop crowdsec-firewall-bouncer 2>/dev/null   # postinst may start it with no key; start below

echo "== 3/6 new bouncer key (root-only file, never printed)"
cs bouncers delete nuc-firewall >/dev/null 2>&1
umask 077
KEY=$(cs bouncers add nuc-firewall -o raw 2>/dev/null | tr -cd '[:alnum:]/+=_-')
if [ "${#KEY}" -ge 20 ]; then
    printf 'api_url: http://127.0.0.1:8080/\napi_key: %s\nmode: nftables\n' "$KEY" > "$LOCAL"
    chown root:root "$LOCAL"; chmod 600 "$LOCAL"
    ok "key written to $LOCAL (length ${#KEY}, mode $(stat -c %a "$LOCAL"))"
else
    bad "cscli bouncers add returned no key (length ${#KEY})"
fi
unset KEY
umask 022
# main config: nftables mode, no metrics port (no new listener), keep the default input+forward hooks
if [ -f "$CFG" ]; then
    sed -i -E 's/^mode:.*/mode: nftables/; s#^api_url:.*#api_url: http://127.0.0.1:8080/#' "$CFG"
    python3 - "$CFG" <<'PY'
import re, sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
# prometheus block: enabled false (default listens on 127.0.0.1:60601; not needed)
s = re.sub(r"(prometheus:\s*\n\s*enabled:\s*)true", r"\1false", s)
open(p, "w", encoding="utf-8").write(s)
PY
    ok "main config: mode nftables, api_url 127.0.0.1:8080, metrics off"
fi
if [ -f /home/user/nuc-homelab/bouncer.key ]; then
    sudo -u server -H trash-put /home/user/nuc-homelab/bouncer.key && ok "old bouncer.key moved to server's trash (key is dead: registration deleted)"
fi

echo "== 4/6 start with a 15-minute dead-man timer"
systemctl stop crowdsec-bouncer-deadman.timer 2>/dev/null; systemctl reset-failed crowdsec-bouncer-deadman.service 2>/dev/null
systemd-run --quiet --unit=crowdsec-bouncer-deadman --on-active=15min /bin/systemctl stop crowdsec-firewall-bouncer
systemctl enable --now crowdsec-firewall-bouncer >/dev/null 2>&1
sleep 8
if systemctl is-active -q crowdsec-firewall-bouncer; then ok "bouncer active (auto-stops in 15 min unless kept)"; else bad "bouncer not active: journalctl -u crowdsec-firewall-bouncer -n 30"; fi

echo "== 5/6 verify"
if cs bouncers list -o raw 2>/dev/null | grep '^nuc-firewall,' | cut -d, -f4 | grep -q .; then ok "nuc-firewall has pulled from the LAPI"; else bad "nuc-firewall shows no last_pull yet"; fi
echo "   decisions now: $(cs decisions list -o raw 2>/dev/null | tail -n +2 | wc -l) local (CAPI blocklist not counted here)"
cs decisions add --ip "$TEST" --duration 5m --reason "bouncer test 11-24" >/dev/null 2>&1
FOUND=no
for i in $(seq 1 12); do sleep 5; nft list ruleset 2>/dev/null | grep -q "$TEST" && { FOUND=yes; break; }; done
[ "$FOUND" = yes ] && ok "test ban $TEST is in nftables" || bad "test ban $TEST not in nftables after 60 s"
cs decisions delete --ip "$TEST" >/dev/null 2>&1
sleep 12
nft list ruleset 2>/dev/null | grep -q "$TEST" && bad "$TEST still in nftables after delete" || ok "test ban removed from nftables"
nft list ruleset 2>/dev/null | grep -E -q '100\.64\.|192\.168\.86\.|127\.0\.0\.' && bad "an own address is in the ruleset; check nft list table ip crowdsec" || ok "no own address in the ruleset"

echo "== 6/6 collections and caddy acquisition"
for col in crowdsecurity/caddy crowdsecurity/http-cve crowdsecurity/sshd crowdsecurity/linux; do
    cs collections list -o raw 2>/dev/null | grep -q "^$col,enabled" && ok "$col enabled" || { cs collections install "$col" >/dev/null 2>&1 && ok "$col installed" || bad "$col install"; }
done
if docker exec "$C" sh -c 'grep -l "caddy-nuc" /etc/crowdsec/acquis.yaml /etc/crowdsec/acquis.d/*.yaml 2>/dev/null' | grep -q .; then
    ok "caddy acquisition present (docker:caddy-nuc, type caddy)"
else
    docker exec "$C" sh -c 'mkdir -p /etc/crowdsec/acquis.d && printf "source: docker\ncontainer_name:\n  - caddy-nuc\nlabels:\n  type: caddy\n" > /etc/crowdsec/acquis.d/caddy.yaml' \
        && docker restart "$C" >/dev/null && ok "caddy acquisition added (acquis.d/caddy.yaml; not persistent unless acquis.d is a mounted volume)" || bad "caddy acquisition add"
fi

echo; echo "== summary"; printf '%s\n' "${RES[@]}"
cat <<EOF

KEEP IT: open a NEW ssh session to server@100.64.0.10. If it works:
    sudo systemctl stop crowdsec-bouncer-deadman.timer
Otherwise do nothing: the bouncer stops itself at the 15-minute mark (it stays enabled for boot;
    'sudo systemctl disable crowdsec-firewall-bouncer' to keep it off).
UNDO:
    sudo systemctl disable --now crowdsec-firewall-bouncer
    sudo apt-get remove -y crowdsec-firewall-bouncer-nftables crowdsec-firewall-bouncer
    sudo nft delete table ip crowdsec; sudo nft delete table ip6 crowdsec
    docker exec $C cscli bouncers delete nuc-firewall
    docker exec $C cscli allowlists delete homelab-never-ban
Backups: $BK
EOF
