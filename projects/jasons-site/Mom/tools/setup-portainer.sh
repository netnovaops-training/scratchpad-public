#!/usr/bin/env bash
# =============================================================================
#  Portainer at /portainer, through Caddy, LAN-only.
#
#      sudo bash setup-portainer.sh              # show what it would do
#      sudo bash setup-portainer.sh --apply
#
#  WHY IT DOES NOT WORK RIGHT NOW
#  ------------------------------
#  Two separate things have to be true, and only one of them is a Caddy
#  setting.
#
#  1. Caddy must strip the /portainer prefix before proxying. It already does.
#
#  2. PORTAINER ITSELF must be told it lives at /portainer, with the
#     --base-url flag. Without it, Portainer serves an HTML page whose script
#     and API links are absolute -- /js/app.js, /api/status. The browser asks
#     Caddy for those, Caddy has no rule for them, and they go to Apache. You
#     get a blank page or a login screen that never logs in.
#
#     This is the part that was missing. Stripping the prefix is not enough;
#     the app has to generate the right links in the first place.
#
#  A KNOWN BUG WORTH CHECKING
#  --------------------------
#  Portainer 2.27.7 LTS and 2.31.1 STS regressed: login fails specifically
#  behind a subdirectory reverse proxy, while direct access works. 2.27.6 was
#  fine. This script reports your version so you know whether a failed login is
#  your configuration or theirs.
#
#  WHY LAN ONLY
#  ------------
#  Portainer is full control of the Docker daemon. Anyone who reaches it can
#  start a privileged container and own this Pi -- and through it, everything
#  else on your network. It is not a dashboard, it is a root shell with a nice
#  interface.
#
#  So the Caddy rule answers only for clients on 192.168.1.0/24. From the
#  public internet it returns 404, which does not even confirm it exists. That
#  costs you nothing at home. From outside, use the SSH tunnel printed at the
#  end -- you already have SSH, and it is strictly safer than any password.
# =============================================================================
set -Eeuo pipefail

APPLY=false
[[ "${1:-}" == "--apply" || "${1:-}" == "-a" ]] && APPLY=true

LAN="${LAN:-192.168.1.0/24}"
BASE="${BASE:-/portainer}"
CADDYFILE="${CADDYFILE:-/etc/caddy/Caddyfile}"
SSH_PORT="${SSH_PORT:-1143}"
SNIPPET="/root/portainer-caddy.conf"

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; D=$'\033[2m'; B=$'\033[1m'; O=$'\033[0m'
step(){ printf '\n%s==> %s%s\n' "$C" "$*" "$O"; }
ok(){   printf '%s  ok  %s%s\n' "$G" "$*" "$O"; }
note(){ printf '%s      %s%s\n' "$D" "$*" "$O"; }
warn(){ printf '%s  !   %s%s\n' "$Y" "$*" "$O"; }
die(){  printf '%s  !!  %s%s\n' "$R" "$*" "$O" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run with sudo: sudo bash setup-portainer.sh"
$APPLY && printf '\n%s  APPLYING%s\n' "$R" "$O" || printf '\n%s  DRY RUN -- nothing will change%s\n' "$Y" "$O"

# --- 1. is Portainer there, and how is it configured -------------------------
step "Looking for Portainer"
RUNNING="$(docker ps -a --filter name=portainer --format '{{.Names}}\t{{.Image}}\t{{.Status}}' || true)"
if [[ -z "$RUNNING" ]]; then
  warn "No Portainer container exists."
  HAVE=false
else
  ok "Found:"
  printf '%s' "$RUNNING" | while IFS= read -r l; do note "$l"; done
  HAVE=true
  IMAGE="$(docker inspect -f '{{.Config.Image}}' portainer 2>/dev/null || echo '?')"
  CMD="$(docker inspect -f '{{json .Config.Cmd}}' portainer 2>/dev/null || echo '[]')"
  note "image: $IMAGE"
  note "args:  $CMD"
  if grep -q -- "--base-url" <<<"$CMD"; then
    ok "--base-url is already set"
    BASEOK=true
  else
    warn "--base-url is NOT set. This is why $BASE does not work."
    note "Portainer is serving absolute asset links, so the browser asks Caddy"
    note "for /js/... and /api/..., which go to Apache instead."
    BASEOK=false
  fi
  VER="$(docker exec portainer /portainer --version 2>/dev/null | head -1 || echo '')"
  [[ -n "$VER" ]] && note "version: $VER"
  case "$VER" in
    *2.27.7*|*2.31.1*)
      warn "This version has a known subdirectory-proxy login bug."
      note "Logging in through $BASE fails while direct access works."
      note "Move off this version, or use the SSH tunnel below instead." ;;
  esac
fi

# --- 1b. what is exposed to the network --------------------------------------
step "Which containers are reachable from the LAN"
# A port published as 0.0.0.0:PORT is reachable from every machine on the
# network, bypassing Caddy, its TLS, and its login. Bound to 127.0.0.1 it is
# reachable only through the reverse proxy, which is the whole point of having
# one.
EXPOSED=0
while IFS= read -r line; do
  cname="${line%%|*}"; ports="${line#*|}"
  [ -z "$ports" ] && continue
  case "$ports" in
    *0.0.0.0:*|*:::*)
      warn "$cname publishes on ALL interfaces:"
      note "  $ports"
      EXPOSED=$((EXPOSED+1)) ;;
  esac
done <<< "$(docker ps --format '{{.Names}}|{{.Ports}}')"

if [ "$EXPOSED" -eq 0 ]; then
  ok "Nothing is published beyond localhost"
else
  note ""
  note "Only ports 80 and 443 are forwarded by your router, so these are not"
  note "reachable from the internet -- but any device on your network can"
  note "reach them directly, with no TLS and no Caddy login in front."
  note "Rebind by changing  -p 9443:9443  to  -p 127.0.0.1:9443:9443"
fi

# --- 2. the container --------------------------------------------------------
step "The container command"
read -r -d '' RUNCMD <<RUNEOF || true
docker run -d \\
  --name portainer \\
  --restart=always \\
  -p 127.0.0.1:9443:9443 \\
  -v /var/run/docker.sock:/var/run/docker.sock \\
  -v portainer_data:/data \\
  portainer/portainer-ce:lts \\
  --base-url $BASE
RUNEOF
# Bound to 127.0.0.1 on purpose. Published on 0.0.0.0 it would bypass Caddy
# entirely and be reachable from anywhere that can route to the Pi, which
# defeats every rule written below.

if [[ "$HAVE" == "false" ]]; then
  note "Portainer is not installed. To install it with the right flag:"
  printf '%s\n' "$RUNCMD" | while IFS= read -r l; do note "$l"; done
elif [[ "$BASEOK" == "false" ]]; then
  warn "The existing container must be recreated to add --base-url."
  note "Your data lives in the 'portainer_data' volume and is NOT touched by"
  note "removing the container, so nothing is lost:"
  note ""
  note "  docker rm -f portainer"
  printf '%s\n' "$RUNCMD" | while IFS= read -r l; do note "$l"; done
  if $APPLY; then
    read -r -p "    Recreate the container now? [y/N] " a </dev/tty || a=n
    if [[ "$a" =~ ^[Yy] ]]; then
      docker rm -f portainer >/dev/null
      eval "$RUNCMD" >/dev/null
      sleep 3
      docker ps --filter name=portainer --format '{{.Status}}' | while IFS= read -r l; do note "$l"; done
      ok "Recreated with --base-url $BASE"
    else
      note "Left alone. $BASE will keep failing until this is done."
    fi
  fi
fi

# --- 3. the Caddy block ------------------------------------------------------
step "The Caddy rule"
cat > "$SNIPPET" <<CADDYEOF
	# --- Portainer: LAN only ------------------------------------------------
	# Portainer is FULL DOCKER CONTROL of this Pi. Anyone who reaches it can
	# start a privileged container and own the machine, so it answers only on
	# the LAN; everything else gets a 404 that does not admit it exists.
	#
	# remote_ip reads the real TCP source address, never X-Forwarded-For, so a
	# spoofed header changes nothing. Caddy honours that header only when
	# trusted_proxies is configured, which it is not.
	#
	# NOTE the redirect form. It must be written at this level with an explicit
	# matcher, NOT as \`handle $BASE { redir $BASE/ 308 }\` -- inside a handle
	# block Caddy reads the leading-slash argument as a MATCHER, so that form
	# silently becomes "302 to the literal URL 308" and does nothing at all.
	#
	# The redirect is gated on the LAN too. Otherwise a stranger hitting $BASE
	# gets a 308 and learns the thing exists before being refused.
	@portainer_lan {
		path $BASE
		remote_ip $LAN 127.0.0.1/8 ::1
	}
	redir @portainer_lan $BASE/ 308

	@portainer_public {
		path $BASE $BASE/*
		not remote_ip $LAN 127.0.0.1/8 ::1
	}
	handle @portainer_public {
		respond 404
	}

	handle_path $BASE/* {
		reverse_proxy https://127.0.0.1:9443 {
			transport http {
				tls_insecure_skip_verify
			}
		}
	}
CADDYEOF
ok "Written to $SNIPPET"
note "Paste it inside the example.org block, ABOVE the bare"
note "\`handle {\` that forwards to Apache. That one matches everything left"
note "over, so anything below it never runs."

if grep -q "handle_path $BASE/\*" "$CADDYFILE" 2>/dev/null; then
  ok "$CADDYFILE already has a $BASE rule"
  if ! grep -q "portainer_public" "$CADDYFILE" 2>/dev/null; then
    warn "...but no LAN restriction. Right now it is reachable from the internet."
    note "Replace the existing block with the one above."
  fi
else
  warn "$CADDYFILE has no $BASE rule yet"
fi

if $APPLY && ! grep -q "portainer_public" "$CADDYFILE" 2>/dev/null; then
  cp -a "$CADDYFILE" "$CADDYFILE.bak-$(date +%s)"
  # Insert above the LAST bare `handle {` in the first site block.
  awk -v snip="$SNIPPET" '
    !done && /^[[:space:]]+handle \{[[:space:]]*$/ {
      while ((getline line < snip) > 0) print line
      close(snip); print ""
      done = 1
    }
    { print }
  ' "$CADDYFILE" > /tmp/Caddyfile.new
  if caddy validate --config /tmp/Caddyfile.new --adapter caddyfile >/dev/null 2>&1; then
    install -m 644 /tmp/Caddyfile.new "$CADDYFILE"
    systemctl reload caddy && ok "Caddy reloaded"
  else
    warn "The merged config did not validate. Nothing changed."
    note "Merged attempt kept at /tmp/Caddyfile.new"
    caddy validate --config /tmp/Caddyfile.new --adapter caddyfile 2>&1 | head -10 |
      while IFS= read -r l; do note "$l"; done
  fi
fi

# --- 4. how to actually reach it ---------------------------------------------
cat <<EOF

$(ok "Done")

  ${B}At home, on your own network${O}

      https://example.org$BASE/

  ${B}From anywhere else${O}   The rule above returns 404 to the internet, on
                       purpose. Reach it down your existing SSH connection
                       instead -- no password to guess, no port to forward:

      ssh -N -L 9443:127.0.0.1:9443 -p $SSH_PORT person14@example.com

                       Leave that running, then open:

      https://localhost:9443/

                       Your browser will warn about the certificate. That is
                       expected: it is Portainer's own self-signed one, and the
                       traffic is already inside the SSH tunnel.

  ${B}If you would rather have it public${O}

  I would not, and it is worth being plain about why: Portainer holds the
  Docker socket. A single working request creates a privileged container, and
  from there the Pi and everything it can reach are gone. A password does not
  change that -- it only means the attacker needs one working guess instead of
  none. The SSH tunnel above gives you the same access with none of that.

EOF
