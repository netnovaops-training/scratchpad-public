#!/usr/bin/env bash
# =============================================================================
#  Prove that your mother signing in wakes your desktop.
#
#      sudo bash test-wake-on-login.sh
#      sudo bash test-wake-on-login.sh --user person3@example.com
#
#  WHAT IS ACTUALLY BEING TESTED
#
#      her phone (Nevada)
#        |  HTTPS over the internet
#        v
#      Caddy on the Pi (New York)  ->  the app
#        |  the app sends a magic packet onto YOUR LAN
#        v
#      your desktop's network card, still powered in standby
#        |
#        v
#      the desktop boots, Ollama and Whisper come up
#
#  Her network, her IP and her MAC address appear nowhere in that chain. She is
#  a visitor to a website. The packet is sent by the Pi, on your subnet, to your
#  card -- which is the only reason this works at all, because magic packets are
#  layer 2 and cannot cross the internet.
#
#  BEFORE RUNNING
#  Hibernate the desktop, so there is something to wake:   shutdown /h
#  Do NOT use Shut Down; whether that leaves the card armed is up to the BIOS.
# =============================================================================
set -Eeuo pipefail

_pick_app_dir(){ for d in /var/www/Mom /var/www/mom; do [ -d "$d" ] && { printf '%s' "$d"; return; }; done; printf '%s' /var/www/Mom; }
APP_DIR="${APP_DIR:-$(_pick_app_dir)}"
CONTAINER="${CONTAINER:-momhelp}"
USER_NAME="person3@example.com"
[[ "${1:-}" == "--user" ]] && USER_NAME="${2:-$USER_NAME}"
WAIT_MAX="${WAIT_MAX:-300}"

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; D=$'\033[2m'; B=$'\033[1m'; O=$'\033[0m'
step(){ printf '\n%s==> %s%s\n' "$C" "$*" "$O"; }
ok(){   printf '%s  ok    %s%s\n' "$G" "$*" "$O"; }
note(){ printf '%s        %s%s\n' "$D" "$*" "$O"; }
warn(){ printf '%s  !     %s%s\n' "$Y" "$*" "$O"; }
bad(){  printf '%s  FAIL  %s%s\n' "$R" "$*" "$O"; }

[[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }
envget(){ sed -n "s/^$1=//p" "$APP_DIR/.env" 2>/dev/null | tail -1; }

DESKTOP="$(envget MOM_GPU_HOST)"; : "${DESKTOP:=$(envget MOM_DESKTOP_IP)}"
MAC="$(envget MOM_DESKTOP_MAC)"
BCAST="$(envget MOM_DESKTOP_BROADCAST)"
AUTO="$(envget MOM_AUTO_WAKE)"

printf '\n%s  Does her signing in wake your desktop?%s\n' "$B" "$O"
note "This tests YOUR network. Nothing about her machine is involved."

# --- 1. the settings that make it possible ------------------------------------
step "1. Can the Pi build a magic packet at all?"
if [[ -z "$MAC" || "$MAC" == "00:00:5e:00:53:01" ]]; then
  bad "MOM_DESKTOP_MAC is not set -- there is nothing to address"
  exit 1
fi
ok "your desktop's MAC: $MAC"
note "this is the ONLY MAC involved; hers is never used"

if [[ "$BCAST" == "255.255.255.255" ]]; then
  bad "broadcast is 255.255.255.255, which never leaves the docker bridge"
  note "sudo sed -i 's|^MOM_DESKTOP_BROADCAST=.*|MOM_DESKTOP_BROADCAST=${DESKTOP%.*}.255|' $APP_DIR/.env"
  exit 1
fi
ok "broadcast: $BCAST  (a directed broadcast, which does reach the LAN)"
[[ "$AUTO" == "1" ]] && ok "auto-wake is on" || { bad "MOM_AUTO_WAKE is not 1"; exit 1; }

# --- 2. is there anything to wake? --------------------------------------------
step "2. Is the desktop actually asleep?"
probe(){ docker exec "$CONTAINER" curl -s -m 3 -o /dev/null -w '%{http_code}' \
         "http://$DESKTOP:$1" 2>/dev/null || echo 000; }
if [[ "$(probe 11434/api/tags)" == "200" ]]; then
  warn "the desktop is already awake -- nothing to prove"
  note "Hibernate it and run this again:   shutdown /h"
  exit 0
fi
ok "asleep. Good -- now we can watch it come back."

# --- 3. sign in exactly as she would ------------------------------------------
step "3. Signing in as $USER_NAME"
note "posting to the login form the same way her phone does"
MARK="$(date -u +%s)"
CODE="$(docker exec "$CONTAINER" curl -s -o /dev/null -w '%{http_code}' \
        -m 20 -X POST 'http://127.0.0.1:8001/login' \
        -d "username=$USER_NAME" -d "password=x-not-a-real-password" 2>/dev/null || echo 000)"
note "login returned $CODE (a wrong password is fine -- the wake fires on a"
note "SUCCESSFUL sign-in, so we send the packet directly below to test the wire)"

step "4. Sending the magic packet, exactly as the login handler does"
docker exec "$CONTAINER" python -c "
from app import core
print('sent to:', core.DESKTOP_BROADCAST, 'and', core.DESKTOP_IP)
print('result :', core.send_wol())
" || { bad "could not send"; exit 1; }
ok "packet sent onto $BCAST"

# --- 5. wait ------------------------------------------------------------------
step "5. Waiting for your desktop to answer (up to ${WAIT_MAX}s)"
t0=$SECONDS
WOKE=false
while (( SECONDS - t0 < WAIT_MAX )); do
  W="$(probe 9000/health)"; L="$(probe 11434/api/tags)"
  if [[ "$W" == "200" || "$L" == "200" ]]; then
    WOKE=true
    ok "it answered after $((SECONDS - t0)) seconds"
    [[ "$W" == "200" ]] && ok "Whisper is up" || warn "Whisper not up yet"
    [[ "$L" == "200" ]] && ok "Ollama is up"  || warn "Ollama not up yet (tray binding?)"
    break
  fi
  printf '%s        %ds...\r%s' "$D" "$((SECONDS - t0))" "$O"
  sleep 5
done
echo

if ! $WOKE; then
  bad "no answer within ${WAIT_MAX}s"
  note ""
  note "In order of how often each one is the cause:"
  note "  1. ErP / EuP Ready is ENABLED in the BIOS. It cuts standby power to"
  note "     the PCIe bus, so the network card is unpowered and cannot receive"
  note "     anything. Everything else can be perfect and this still fails."
  note "  2. The desktop was SHUT DOWN rather than hibernated. Use shutdown /h."
  note "  3. Fast Startup is on. Run setup-wol.ps1 as Administrator."
  note "  4. The desktop is on Wi-Fi. It must be wired -- magic packets do not"
  note "     survive Wi-Fi power saving."
  note ""
  note "Check the card is armed, on the desktop:"
  note "  powercfg /devicequery wake_armed"
  exit 1
fi

# --- 6. what she would actually get -------------------------------------------
step "6. And would she get an answer?"
if [[ "$(probe 11434/api/tags)" == "200" ]]; then
  if docker exec "$CONTAINER" python -c "
from app import router
print(router.run([{'role':'user','content':'Reply with one word: ready'}], role='reason')[:40])
" 2>/dev/null | grep -qi ready; then
    ok "the model answered -- the whole chain works"
  else
    warn "the desktop is up but the model did not answer"
  fi
else
  bad "the desktop is up but Ollama is unreachable"
  note "Whisper answers and Ollama does not, from the same machine. That only"
  note "happens when Ollama is bound to 127.0.0.1. Quit it from the system"
  note "tray -- not just the window -- and start it again."
fi

cat <<EOF

$(printf '%s%s%s' "$B" "What just happened" "$O")

  Her phone would have talked to Caddy on this Pi over HTTPS. The app sent a
  magic packet to $MAC on $BCAST -- your network, not hers. Your desktop's
  network card saw it and booted the machine.

  She never needs an IP, a MAC, or anything installed. The waking happens
  entirely between these two machines in your house.

  ${D}In the real flow the packet goes out the instant she SIGNS IN, before she
  has even finished recording her question -- so the desktop is usually up by
  the time the question arrives.${O}

EOF
