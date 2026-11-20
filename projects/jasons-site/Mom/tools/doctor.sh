#!/usr/bin/env bash
# =============================================================================
#  Tech Help — end-to-end diagnostic
#
#      sudo bash doctor.sh
#
#  Checks every link in the chain and tells you the specific fix for whatever
#  is broken. Read-only: it changes nothing.
#
#      internet -> Caddy(443) -> container(8001) -> LAN -> Ollama + Whisper
# =============================================================================
set -uo pipefail        # deliberately NOT -e: a failing check must not stop the run

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
PORT="${PORT:-8001}"
ROOT_PATH="${ROOT_PATH:-/mom}"

G=$'\033[32m'; R=$'\033[31m'; Y=$'\033[33m'; C=$'\033[36m'; B=$'\033[1m'; D=$'\033[2m'; O=$'\033[0m'
PASS=0; FAIL=0; WARN=0
FIXES=()

sec(){ printf '\n%s── %s%s\n' "$C" "$*" "$O"; }
ok(){  PASS=$((PASS+1)); printf '  %s✓%s %s\n' "$G" "$O" "$*"; }
addfix(){   # only record a suggestion once, however many checks hit it
  local f="$1"; [[ -z "$f" ]] && return 0
  local e; for e in "${FIXES[@]:-}"; do [[ "$e" == "$f" ]] && return 0; done
  FIXES+=("$f"); }
bad(){ FAIL=$((FAIL+1)); printf '  %s✗%s %s\n' "$R" "$O" "$1"; [[ $# -gt 1 ]] && addfix "$2"; return 0; }
wrn(){ WARN=$((WARN+1)); printf '  %s!%s %s\n' "$Y" "$O" "$1"; [[ $# -gt 1 ]] && addfix "$2"; return 0; }
inf(){ printf '    %s%s%s\n' "$D" "$*" "$O"; }

[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo bash doctor.sh"; exit 1; }

printf '\n%s  Tech Help — diagnostic%s\n' "$B" "$O"

# --- 1. who owns the web ports ----------------------------------------------
sec "Web ports"
listeners="$(ss -tlnp 2>/dev/null || true)"
for p in 80 443; do
  who="$(grep -E ":$p " <<<"$listeners" | grep -oP 'users:\(\("\K[^"]+' | head -1)"
  if [[ -z "$who" ]]; then
    wrn "Nothing is listening on port $p" \
        "Start Caddy:  sudo systemctl enable --now caddy"
  elif [[ "$who" == "caddy" ]]; then
    ok "Port $p is Caddy"
  else
    bad "Port $p is held by '$who', not Caddy" \
        "Move $who off 80/443. If it is Apache:
     sudo sed -i 's/^Listen 80\$/Listen 127.0.0.1:8080/' /etc/apache2/ports.conf
     sudo sed -i 's/^Listen 443\$/#Listen 443/' /etc/apache2/ports.conf
     sudo sed -i 's/<VirtualHost \\*:80>/<VirtualHost 127.0.0.1:8080>/' /etc/apache2/sites-enabled/*.conf
     sudo apachectl configtest && sudo systemctl restart apache2
     sudo systemctl enable --now caddy"
  fi
done
port8080="$(grep -E ':8080 ' <<<"$listeners" | grep -oP 'users:\(\("\K[^"]+' | head -1)"
[[ -n "$port8080" ]] && inf "Port 8080: $port8080 (this is where Apache belongs)"

# --- 2. caddy ----------------------------------------------------------------
sec "Caddy"
if ! command -v caddy >/dev/null 2>&1; then
  bad "Caddy is not installed" "See the runbook for the install steps."
else
  ok "Installed: $(caddy version 2>/dev/null | head -1)"
  if systemctl is-active --quiet caddy; then
    ok "Service is running"
  else
    state="$(systemctl is-active caddy 2>/dev/null || echo unknown)"
    bad "Service is NOT running (state: $state)" \
        "sudo systemctl enable --now caddy   then re-run this script"
    inf "Why it stopped — last 12 lines:"
    journalctl -u caddy -n 12 --no-pager 2>/dev/null | sed 's/^/      /'
  fi
  if caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    ok "Caddyfile is valid"
  else
    bad "Caddyfile is invalid" "sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile"
  fi
  if grep -q "handle_path $ROOT_PATH/\*" /etc/caddy/Caddyfile 2>/dev/null; then
    ok "Caddyfile routes $ROOT_PATH"
    # The catch-all must come last or it swallows the sub-paths.
    ln_mom="$(grep -n "handle_path $ROOT_PATH/\*" /etc/caddy/Caddyfile | head -1 | cut -d: -f1)"
    ln_all="$(grep -nE '^\s*handle\s*\{' /etc/caddy/Caddyfile | head -1 | cut -d: -f1)"
    if [[ -n "$ln_all" && -n "$ln_mom" && "$ln_all" -lt "$ln_mom" ]]; then
      bad "The catch-all 'handle' is ABOVE $ROOT_PATH (line $ln_all vs $ln_mom)" \
          "Move the $ROOT_PATH block above the catch-all handle in /etc/caddy/Caddyfile.
     Caddy matches in order, so the catch-all is swallowing $ROOT_PATH."
    else
      ok "Route order is correct"
    fi
  else
    bad "Caddyfile does not route $ROOT_PATH" \
        "Paste the block from $APP_DIR/caddy-mom.conf into /etc/caddy/Caddyfile"
  fi
fi

# --- 3. apache ---------------------------------------------------------------
sec "Apache"
if systemctl list-unit-files 2>/dev/null | grep -q '^apache2.service'; then
  if systemctl is-active --quiet apache2; then
    ports="$(grep -hoP '^\s*Listen\s+\K\S+' /etc/apache2/ports.conf 2>/dev/null | tr '\n' ' ')"
    inf "Listening on: ${ports:-unknown}"
    if grep -qE '^\s*Listen\s+(80|443)\s*$' /etc/apache2/ports.conf 2>/dev/null; then
      bad "Apache is bound to 80 and/or 443 — this is what blocks Caddy" \
          "Edit /etc/apache2/ports.conf so it reads only:  Listen 127.0.0.1:8080"
    else
      ok "Apache is off the TLS ports"
    fi
  else
    inf "Installed but not running (fine — Caddy can serve everything)"
  fi
else
  inf "Not installed"
fi

# --- 4. docker ---------------------------------------------------------------
sec "Docker"
if ! command -v docker >/dev/null 2>&1; then
  bad "Docker is not installed" "curl -fsSL https://get.docker.com | sh"
elif ! docker info >/dev/null 2>&1; then
  bad "Docker daemon is not running" "sudo systemctl enable --now docker"
else
  ok "Daemon running ($(docker version --format '{{.Server.Version}}' 2>/dev/null))"
  ok "Architecture: $(uname -m)"
  if docker inspect momhelp >/dev/null 2>&1; then
    state="$(docker inspect -f '{{.State.Status}}' momhelp)"
    health="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' momhelp)"
    if [[ "$state" == "running" ]]; then
      ok "Container 'momhelp' is running (health: $health)"
      restarts="$(docker inspect -f '{{.RestartCount}}' momhelp)"
      [[ "$restarts" -gt 3 ]] && wrn "It has restarted $restarts times — something is crashing" \
        "docker logs momhelp --tail 50"
    else
      bad "Container 'momhelp' is $state" \
          "cd $APP_DIR && docker compose up -d   then: docker compose logs --tail 40"
      docker logs momhelp --tail 12 2>&1 | sed 's/^/      /'
    fi
  else
    bad "Container 'momhelp' does not exist" "cd $APP_DIR && docker compose up -d --build"
  fi
fi

# --- 5. the app --------------------------------------------------------------
sec "The app"
if curl -fsS -m 5 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
  ok "Answering on 127.0.0.1:$PORT"
else
  bad "Not answering on 127.0.0.1:$PORT" "docker logs momhelp --tail 40"
fi
if [[ -f "$APP_DIR/.env" ]]; then
  ok ".env present"
  perms="$(stat -c '%a' "$APP_DIR/.env")"
  [[ "$perms" == "600" ]] || wrn ".env permissions are $perms, should be 600" \
    "sudo chmod 600 $APP_DIR/.env"
  # shellcheck disable=SC1090
  set -a; . "$APP_DIR/.env" 2>/dev/null; set +a
  [[ -n "${MOM_DESKTOP_IP:-}" ]] || bad "MOM_DESKTOP_IP is not set in .env" \
    "Add MOM_DESKTOP_IP=192.168.1.205 to $APP_DIR/.env and: docker compose restart"
  [[ -n "${MOM_DESKTOP_MAC:-}" ]] || wrn "MOM_DESKTOP_MAC is not set — the desktop cannot be woken" \
    "Add the wired MAC to $APP_DIR/.env"
else
  bad ".env missing at $APP_DIR/.env" "cd $APP_DIR && cp .env.example .env && nano .env"
fi

# --- 6. the AI backend, tested FROM INSIDE the container ---------------------
sec "AI backend (tested from inside the container)"
inf "This is the test that matters: the Pi reaching it proves nothing if the"
inf "container cannot. Docker masquerades outbound traffic as the Pi's own IP,"
inf "so your 192.168.1.0/24 firewall rule on the desktop does allow it."
DIP="${MOM_DESKTOP_IP:-}"
if [[ -z "$DIP" ]]; then
  bad "No desktop IP configured — skipping backend tests"
elif ! docker inspect momhelp >/dev/null 2>&1; then
  bad "No container — skipping backend tests"
else
  incontainer(){ docker exec momhelp python -c "
import sys, json, urllib.request
try:
    with urllib.request.urlopen('$1', timeout=6) as r:
        d = r.read().decode()[:400]
    print('OK'); print(d)
except Exception as e:
    print('ERR'); print(type(e).__name__ + ': ' + str(e))
" 2>/dev/null; }

  out="$(incontainer "http://$DIP:${MOM_OLLAMA_PORT:-11434}/api/tags")"
  if head -1 <<<"$out" | grep -q OK; then
    ok "Container can reach Ollama at $DIP:${MOM_OLLAMA_PORT:-11434}"
    models="$(tail -n +2 <<<"$out" | grep -oP '"name":"\K[^"]+' | tr '\n' ' ')"
    if [[ -n "$models" ]]; then
      ok "Models available: $models"
      want="${MOM_OLLAMA_MODEL:-gemma4:12b-it-qat}"
      grep -q "$want" <<<"$models" && ok "Configured model '$want' is installed" \
        || bad "Configured model '$want' is NOT installed" \
               "On the desktop:  ollama pull $want"
    else
      bad "Ollama answered but has no models" "On the desktop:  ollama pull gemma4:12b-it-qat"
    fi
  else
    bad "Container CANNOT reach Ollama at $DIP:${MOM_OLLAMA_PORT:-11434}" \
        "1. Is the desktop awake?  2. On the desktop check OLLAMA_HOST=0.0.0.0:11434
     (not 127.0.0.1), then QUIT Ollama from the system tray and relaunch it.
     3. Check the firewall rule covers this Pi's subnet: 192.168.1.0/24"
    inf "$(tail -n +2 <<<"$out")"
  fi

  out="$(incontainer "http://$DIP:${MOM_WHISPER_PORT:-9000}/health")"
  if head -1 <<<"$out" | grep -q OK; then
    ok "Container can reach Whisper at $DIP:${MOM_WHISPER_PORT:-9000}"
    body="$(tail -n +2 <<<"$out")"
    grep -q '"cuda_available": *true' <<<"$body" \
      && ok "Whisper is using the GPU" \
      || wrn "Whisper is NOT using CUDA — transcription will be ~20x slower" \
             "On the desktop open:  http://localhost:9000/diagnose"
    m="$(grep -oP '"model": *"\K[^"]+' <<<"$body")"; [[ -n "$m" ]] && inf "Model: $m"
  else
    bad "Container CANNOT reach Whisper at $DIP:${MOM_WHISPER_PORT:-9000}" \
        "Desktop: check Task Scheduler -> LocalWhisperService -> Last Run Result"
    inf "$(tail -n +2 <<<"$out")"
  fi

  # The real proof: a generation through the container's own code path.
  sec "End-to-end generation"
  gen="$(docker exec momhelp python -c "
from app import core
try:
    r = core.chat([{'role':'user','content':'Reply with exactly: READY'}], temperature=0)
    print('OK'); print(r[:200])
except Exception as e:
    print('ERR'); print(type(e).__name__ + ': ' + str(e)[:300])
" 2>/dev/null)"
  if head -1 <<<"$gen" | grep -q OK; then
    ok "The app generated a response through Ollama"
    inf "Model said: $(tail -n +2 <<<"$gen" | head -2 | tr '\n' ' ')"
  else
    bad "Generation failed inside the app" "docker logs momhelp --tail 40"
    inf "$(tail -n +2 <<<"$gen")"
  fi
fi

# --- 7. from the outside -----------------------------------------------------
sec "Public address"
HOSTN="${PUBLIC_HOST:-example.org}"
res="$(getent hosts "$HOSTN" 2>/dev/null | awk '{print $1}' | head -1)"
pub="$(curl -fsS -m 8 https://api.ipify.org 2>/dev/null || echo '')"
inf "$HOSTN resolves to: ${res:-(nothing)}"
inf "This network's public IP: ${pub:-(unknown)}"
if [[ -n "$res" && -n "$pub" && "$res" == "$pub" ]]; then
  ok "DNS matches your public IP"
elif [[ -z "$res" ]]; then
  bad "$HOSTN does not resolve" "Create it at noip.com and check: systemctl status noip-duc"
else
  wrn "DNS does not match your public IP" "sudo systemctl restart noip-duc"
fi
code="$(curl -sk -o /dev/null -w '%{http_code}' -m 10 "https://127.0.0.1$ROOT_PATH/" \
        -H "Host: $HOSTN" 2>/dev/null)"
code="${code:-000}"
case "$code" in
  200|303|307) ok "Caddy serves $ROOT_PATH (HTTP $code)" ;;
  000) bad "Caddy is not answering on 443 at all" "Start Caddy:  sudo systemctl enable --now caddy" ;;
  404) bad "Caddy answers but $ROOT_PATH is not routed (404)" \
           "Add the block from $APP_DIR/caddy-mom.conf, above the catch-all handle" ;;
  502|503) bad "Caddy is routing $ROOT_PATH but the container is not answering ($code)" \
               "cd $APP_DIR && docker compose up -d && docker compose logs --tail 30" ;;
  *) wrn "Unexpected response from $ROOT_PATH: HTTP $code" "" ;;
esac

# --- summary -----------------------------------------------------------------
printf '\n%s%s\n' "$B" "════════════════════════════════════════════════════════"
printf '  %s%d passed%s   %s%d warnings%s   %s%d failed%s\n' \
  "$G" "$PASS" "$O" "$Y" "$WARN" "$O" "$R" "$FAIL" "$O"
printf '%s%s\n' "════════════════════════════════════════════════════════" "$O"
if [[ ${#FIXES[@]} -gt 0 ]]; then
  printf '\n%s  Do these, in order:%s\n\n' "$B" "$O"
  n=1
  for f in "${FIXES[@]}"; do
    [[ -z "$f" ]] && continue
    printf '  %s%d.%s %s\n\n' "$B" "$n" "$O" "$f"
    n=$((n+1))
  done
else
  printf '\n  %sEverything is connected. She can use it.%s\n\n' "$G" "$O"
fi
exit 0
