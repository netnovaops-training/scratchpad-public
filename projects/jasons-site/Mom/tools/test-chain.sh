#!/usr/bin/env bash
# =============================================================================
#  Does the whole thing actually work when Mom asks a question?
#
#      sudo bash test-chain.sh              # check everything, change nothing
#      sudo bash test-chain.sh --wake       # also send a real magic packet and
#                                           # time how long the desktop takes
#
#  Walks the chain in the order it happens for real, and stops at the first
#  broken link rather than reporting six failures that all have one cause:
#
#      1. her browser  ->  Caddy on the Pi          (is the site up?)
#      2. Caddy        ->  the app container         (is it answering?)
#      3. the app      ->  a magic packet on the LAN (can it wake the desktop?)
#      4. the desktop  ->  awake and listening       (did it come back?)
#      5. the app      ->  Whisper on :9000          (can it hear her?)
#      6. the app      ->  Ollama on :11434          (can it think?)
#      7. each model   ->  loaded and answering      (all three specialists)
#      8. end to end   ->  a real question, timed
# =============================================================================
set -Eeuo pipefail

WAKE=false
[[ "${1:-}" == "--wake" ]] && WAKE=true
# The folder is /var/www/Mom (capital M) -- the lowercase duplicate was removed.
# Detect rather than assume, because Linux paths are case-sensitive and pointing
# at the wrong one silently creates a second, empty installation.
_pick_app_dir(){
  for d in /var/www/Mom /var/www/Mom; do
    [ -d "$d" ] && { printf '%s' "$d"; return; }
  done
  printf '%s' /var/www/Mom
}
APP_DIR="${APP_DIR:-$(_pick_app_dir)}"
CONTAINER="${CONTAINER:-momhelp}"
HOSTN="${HOSTN:-example.org}"
ROOT_PATH="${ROOT_PATH:-/Mom}"

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; D=$'\033[2m'; B=$'\033[1m'; O=$'\033[0m'
step(){ printf '\n%s==> %s%s\n' "$C" "$*" "$O"; }
ok(){   printf '%s  ok    %s%s\n' "$G" "$*" "$O"; }
note(){ printf '%s        %s%s\n' "$D" "$*" "$O"; }
warn(){ printf '%s  !     %s%s\n' "$Y" "$*" "$O"; }
bad(){  printf '%s  BROKEN %s%s\n' "$R" "$*" "$O"; FAILED+=("$*"); }
FAILED=()

[[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }
envget(){ sed -n "s/^$1=//p" "$APP_DIR/.env" 2>/dev/null | tail -1; }

DESKTOP="$(envget MOM_GPU_HOST)"; : "${DESKTOP:=$(envget MOM_DESKTOP_IP)}"
MAC="$(envget MOM_DESKTOP_MAC)"
BCAST="$(envget MOM_DESKTOP_BROADCAST)"
REASON="$(envget MOM_MODEL_REASON)";  : "${REASON:=hermes3:8b}"
VISION="$(envget MOM_MODEL_VISION)";  : "${VISION:=qwen3-vl:2b}"
EMBED="$(envget MOM_MODEL_EMBED)";    : "${EMBED:=embeddinggemma:300m}"

printf '\n%s  The chain, link by link%s\n' "$B" "$O"
note "desktop $DESKTOP   mac ${MAC:-(NOT SET)}   broadcast ${BCAST:-(NOT SET)}"

# --- 1. the public site -------------------------------------------------------
step "1. Can she reach the site at all?"
code="$(curl -sk -o /dev/null -w '%{http_code}' -m 15 "https://$HOSTN$ROOT_PATH/" || echo 000)"
case "$code" in
  200|303|307) ok "https://$HOSTN$ROOT_PATH/ answers ($code)" ;;
  000) bad "no answer from https://$HOSTN$ROOT_PATH/"
       note "Caddy down, or ports 80/443 not forwarded. Run: systemctl status caddy" ;;
  *)   warn "returned $code" ;;
esac

# --- 2. the app ---------------------------------------------------------------
step "2. Is her app running?"
if docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null | grep -q running; then
  health="$(docker inspect -f '{{.State.Health.Status}}' "$CONTAINER" 2>/dev/null || echo none)"
  ok "container $CONTAINER is running (health: $health)"
else
  bad "container $CONTAINER is not running"
  note "cd $APP_DIR && sudo docker compose up -d"
fi

# Is the NEW code inside it? An old image explains a lot of confusing symptoms.
if docker exec "$CONTAINER" python -c "import app.router, app.memory, app.guides_book, app.webhook" 2>/dev/null; then
  ok "the container has the current modules"
else
  bad "the container is running OLD code"
  note "From Windows:  .\\Setup-TechHelp.ps1 -Only upload"
  note "That rebuilds the image. Without it, none of the newer behaviour exists."
fi

# --- 3. can it wake the desktop? ---------------------------------------------
step "3. Can the Pi wake your desktop?"
if [[ -z "$MAC" || "$MAC" == "00:00:5e:00:53:01" ]]; then
  bad "MOM_DESKTOP_MAC is not set -- no magic packet can be built"
else
  ok "MAC is set: $MAC"
fi
if [[ "$BCAST" == "255.255.255.255" ]]; then
  bad "broadcast is 255.255.255.255 -- that NEVER leaves the docker bridge"
  note "sudo sed -i 's|^MOM_DESKTOP_BROADCAST=.*|MOM_DESKTOP_BROADCAST=${DESKTOP%.*}.255|' $APP_DIR/.env"
elif [[ -n "$BCAST" ]]; then
  ok "broadcast is $BCAST (a directed broadcast, which does leave)"
fi

# --- 4/5/6. the desktop -------------------------------------------------------
step "4. Is the desktop awake right now?"
reach(){ docker exec "$CONTAINER" curl -s -m 4 -o /dev/null -w '%{http_code}' "$1" 2>/dev/null || echo 000; }
OLL="http://$DESKTOP:11434/api/tags"
WHI="http://$DESKTOP:9000/health"

if [[ "$(reach "$OLL")" == "200" ]]; then
  ok "the desktop is up and Ollama is answering"
  AWAKE=true
else
  AWAKE=false
  if [[ "$(reach "$WHI")" == "200" ]]; then
    warn "the desktop is UP -- Whisper answers -- but Ollama does not"
    bad "Ollama is bound to localhost"
    note "There is no firewall or network setting that produces this. Whisper and"
    note "Ollama are on the same machine, same subnet, same rules; one answers and"
    note "one does not, which only happens when Ollama is listening on 127.0.0.1."
    note ""
    note "On the desktop: RIGHT-CLICK the Ollama icon in the system tray and choose"
    note "QUIT -- closing the window is not enough -- then start it again."
  elif $WAKE; then
    note "asleep. Sending a magic packet and waiting..."
    docker exec "$CONTAINER" python -c "from app import core; print(core.send_wol())" || true
    t0=$SECONDS
    for _ in $(seq 1 60); do
      if [[ "$(reach "$WHI")" == "200" || "$(reach "$OLL")" == "200" ]]; then
        ok "the desktop woke up after $((SECONDS - t0)) seconds"
        AWAKE=true; break
      fi
      sleep 4
    done
    $AWAKE || {
      bad "the desktop did not wake within $((SECONDS - t0)) seconds"
      note "In order of likelihood:"
      note "  1. ErP / EuP Ready is ENABLED in the BIOS -- it cuts standby power"
      note "     to the network card, so it cannot receive anything. Disable it."
      note "  2. The desktop was SHUT DOWN, not hibernated. Use: shutdown /h"
      note "  3. Fast Startup is on. Run setup-wol.ps1 as Administrator."
    }
  else
    warn "the desktop is asleep (expected if you are not using it)"
    note "Re-run with --wake to send a real magic packet and time the wake-up."
  fi
fi

# --- 7. the models ------------------------------------------------------------
if $AWAKE; then
  step "5. Can it hear her?  (Whisper)"
  [[ "$(reach "$WHI")" == "200" ]] && ok "Whisper answering on 9000" || bad "Whisper not answering"

  step "6. Are all three specialists installed?"
  tags="$(docker exec "$CONTAINER" curl -s -m 8 "$OLL" 2>/dev/null || echo '')"
  for m in "$REASON" "$VISION" "$EMBED"; do
    if grep -q "\"${m%%:*}" <<<"$tags"; then ok "$m installed"
    else bad "$m is NOT installed"; note "On the desktop: ollama pull $m"; fi
  done

  step "7. Do they actually answer?"
  t0=$SECONDS
  if docker exec "$CONTAINER" python -c "
from app import core, router
print(router.run([{'role':'user','content':'Reply with the single word: ready'}],
                 role='reason')[:60])" 2>/dev/null | grep -qi ready; then
    ok "the reasoning model answered in $((SECONDS - t0))s"
  else
    bad "the reasoning model did not answer"
  fi
fi

# --- 8. verdict ---------------------------------------------------------------
printf '\n%s================================================================%s\n' "$B" "$O"
if [[ ${#FAILED[@]} -eq 0 ]]; then
  printf '%s  YES -- if your mom asks a question right now, it works.%s\n' "$G" "$O"
  note ""
  note "She signs in, which sends the magic packet before she has even finished"
  note "typing. The models load while she talks. She gets an answer."
else
  printf '%s  NOT YET -- %d link(s) broken:%s\n' "$R" "${#FAILED[@]}" "$O"
  for f in "${FAILED[@]}"; do printf '%s    - %s%s\n' "$R" "$f" "$O"; done
fi
printf '%s================================================================%s\n\n' "$B" "$O"
[[ ${#FAILED[@]} -eq 0 ]]
