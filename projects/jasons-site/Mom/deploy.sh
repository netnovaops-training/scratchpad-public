#!/usr/bin/env bash
# =============================================================================
#  Tech Help — deploy to /var/www/Mom on the Raspberry Pi
#
#  Run it from the unpacked source directory (the one with Dockerfile in it):
#
#      sudo bash deploy.sh
#
#  Or point it somewhere else:
#
#      sudo APP_DIR=/var/www/Mom bash deploy.sh
#
#  Safe to re-run. Upgrades the code and the rules; never touches the database,
#  the audio she has uploaded, or the tickets already written.
# =============================================================================
set -Eeuo pipefail

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
DATA_DIR="${DATA_DIR:-/var/lib/momhelp}"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The Caddy route and the folder are /Mom with a capital M. Defaulting to
# lowercase here is why deploy kept reporting "Caddy is not routing /mom" on a
# system where routing was perfectly fine -- it was checking for a path that
# does not exist.
ROOT_PATH="${ROOT_PATH:-/Mom}"
PORT="${PORT:-8001}"

BOLD=$'\033[1m'; GRN=$'\033[32m'; YEL=$'\033[33m'; RED=$'\033[31m'
CYN=$'\033[36m'; DIM=$'\033[2m'; OFF=$'\033[0m'
step(){ printf '\n%s==> %s%s\n' "$CYN" "$*" "$OFF"; }
ok(){   printf '%s  ✓ %s%s\n' "$GRN" "$*" "$OFF"; }
note(){ printf '%s    %s%s\n' "$DIM" "$*" "$OFF"; }
warn(){ printf '%s  ! %s%s\n' "$YEL" "$*" "$OFF"; }
die(){  printf '%s  ✗ %s%s\n' "$RED" "$*" "$OFF" >&2; exit 1; }
trap 'die "Failed on line $LINENO. Nothing further was changed."' ERR

[[ $EUID -eq 0 ]] || die "Run with sudo:  sudo bash deploy.sh"

# Allow running from the source dir OR from an already-populated APP_DIR.
if [[ ! -f "$SRC_DIR/Dockerfile" ]]; then
  if [[ -f "$APP_DIR/Dockerfile" ]]; then
    SRC_DIR="$APP_DIR"
  else
    die "No Dockerfile here or in $APP_DIR. Run this from the unpacked mom/ directory."
  fi
fi

printf '\n%s  Tech Help  ->  %s%s\n' "$BOLD" "$APP_DIR" "$OFF"

# --- 1. docker ---------------------------------------------------------------
step "Checking Docker"
if ! command -v docker >/dev/null 2>&1; then
  warn "Docker is not installed. Installing (a few minutes on a Pi)..."
  curl -fsSL https://get.docker.com | sh
fi
docker info >/dev/null 2>&1 || die "Docker is installed but not running: systemctl start docker"
if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  warn "Installing the compose plugin..."
  apt-get update -qq && apt-get install -y -qq docker-compose-plugin >/dev/null
  DC="docker compose"
fi
ok "Docker $(docker version --format '{{.Server.Version}}' 2>/dev/null || echo '?')  |  $DC"
ok "Architecture: $(uname -m)"

# --- 2. files ----------------------------------------------------------------
step "Installing to $APP_DIR"
install -d -m 755 "$APP_DIR"
if [[ "$SRC_DIR" != "$APP_DIR" ]]; then
  # Preserve anything runtime-owned; replace only what ships with the app.
  rm -rf "$APP_DIR/app"
  cp -r "$SRC_DIR/app" "$APP_DIR/"
  # Named individually rather than copying everything, so runtime-owned files
  # in $APP_DIR (.env above all) are never clobbered by a stale copy.
  #
  # Keep this list in step with new files. It fell behind once already, which
  # is why install-nightly.sh existed in the upload folder but not here, and
  # `sudo bash /var/www/Mom/install-nightly.sh` said "No such file".
  for f in Dockerfile docker-compose.yml docker-compose.peppermint.yml \
           docker-entrypoint.sh requirements.txt manage.py \
           test_mom.py test_growth.py \
           install-nightly.sh create-accounts.sh setup-portainer.sh \
           doctor.sh .env.example; do
    [[ -f "$SRC_DIR/$f" ]] && cp -f "$SRC_DIR/$f" "$APP_DIR/" || true
  done
  # Shell scripts arrive from Windows, so strip carriage returns and make them
  # runnable. A lone \r turns `set -euo pipefail` into the baffling
  # ": invalid option name: set: pipefail".
  sed -i 's/\r$//' "$APP_DIR"/*.sh 2>/dev/null || true
  chmod +x "$APP_DIR"/*.sh 2>/dev/null || true
  install -d -m 755 "$APP_DIR/knowledge"
  cp -f "$SRC_DIR/knowledge/"*.md "$APP_DIR/knowledge/"
fi
find "$APP_DIR" -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
chmod +x "$APP_DIR/docker-entrypoint.sh" 2>/dev/null || true
ok "Code in place"

install -d -m 750 "$DATA_DIR"
ok "Data directory $DATA_DIR"

# Git snapshot so the AI's rules can always be reset to the shipped version.
if [[ ! -d "$APP_DIR/.git" ]]; then
  git -C "$APP_DIR" init -q 2>/dev/null || true
  git -C "$APP_DIR" config user.email "momhelp@localhost" 2>/dev/null || true
  git -C "$APP_DIR" config user.name "Tech Help" 2>/dev/null || true
fi
printf '.env\n__pycache__/\n*.pyc\n' > "$APP_DIR/.gitignore"
git -C "$APP_DIR" add -A >/dev/null 2>&1 || true
git -C "$APP_DIR" commit -qm "Deploy $(date -Iseconds)" >/dev/null 2>&1 || true
ok "Snapshotted (reset rules any time with: git -C $APP_DIR checkout knowledge/)"

# --- 3. configuration --------------------------------------------------------
step "Configuration"
ENV_FILE="$APP_DIR/.env"
envget(){ sed -n "s/^$1=//p" "$ENV_FILE" 2>/dev/null | tail -1; }

if [[ -f "$ENV_FILE" ]]; then
  ok "Keeping the existing .env"
  note "Edit it and re-run to change anything."
  # Read back what is already configured. Without this, every variable below
  # is undefined on the "existing .env" path and `set -u` aborts the run at
  # the closing summary -- after everything has already succeeded, which is
  # the most confusing possible place to fail.
  DESKTOP_IP="$(envget MOM_GPU_HOST)";  : "${DESKTOP_IP:=$(envget MOM_DESKTOP_IP)}"
  DESKTOP_MAC="$(envget MOM_GPU_MAC)";  : "${DESKTOP_MAC:=$(envget MOM_DESKTOP_MAC)}"
  MODEL="$(envget MOM_MODEL_REASON)";   : "${MODEL:=$(envget MOM_OLLAMA_MODEL)}"
  HELPER="$(envget MOM_HELPER_NAME)"
  : "${DESKTOP_IP:=192.168.1.205}"
  : "${DESKTOP_MAC:=}"
  : "${MODEL:=hermes3:8b}"
  : "${HELPER:=Jason}"

  # Nudge an older .env towards the settings the new code needs, without
  # touching anything already set. Appending is safe: later assignments win
  # in docker compose env_file parsing, and nothing here is a secret.
  add_missing(){ grep -q "^$1=" "$ENV_FILE" 2>/dev/null || printf '%s=%s\n' "$1" "$2" >> "$ENV_FILE"; }
  BCAST="$(printf '%s' "$DESKTOP_IP" | sed 's/\.[0-9]*$/.255/')"
  add_missing MOM_MODEL_PROFILE  resident
  add_missing MOM_MODEL_REASON   hermes3:8b
  add_missing MOM_MODEL_VISION   qwen3-vl:2b
  add_missing MOM_MODEL_EMBED    embeddinggemma:300m
  add_missing MOM_DEFAULT_TZ     America/New_York
  add_missing MOM_LEARNED_DIR    /app/knowledge/learned
  # 255.255.255.255 never leaves the docker bridge, so the magic packet dies
  # inside Docker. The subnet-directed broadcast is routable and does leave.
  if grep -q '^MOM_DESKTOP_BROADCAST=255\.255\.255\.255' "$ENV_FILE" 2>/dev/null; then
    sed -i "s|^MOM_DESKTOP_BROADCAST=.*|MOM_DESKTOP_BROADCAST=$BCAST|" "$ENV_FILE"
    ok "Wake-on-LAN broadcast corrected to $BCAST"
    note "255.255.255.255 is never forwarded off the docker bridge."
  else
    add_missing MOM_DESKTOP_BROADCAST "$BCAST"
  fi
else
  ask(){ local v="$1" p="$2" d="${3:-}" a
         if [[ -n "$d" ]]; then read -r -p "    $p [$d]: " a </dev/tty || true
         else read -r -p "    $p: " a </dev/tty || true; fi
         printf -v "$v" '%s' "${a:-$d}"; }

  echo "    Press Enter to accept the value in brackets."
  ask DESKTOP_IP  "Desktop LAN IP (the 4070 box)"   "192.168.1.205"
  ask DESKTOP_MAC "Desktop wired MAC address"       ""
  ask MODEL       "Reasoning model"                 "hermes3:8b"
  ask HELPER      "Your name"                       "Jason"
  ask HELPER_TEL  "Your phone (tap-to-call for her)" ""
  echo
  echo "    Email. Proton cannot SEND from an @proton.me address, so use any"
  echo "    free sender. Gmail app password is the usual choice. Leave the"
  echo "    password blank to skip email — tickets are still written to disk."
  ask SMTP_USER   "Sender address (gmail)"          ""
  ask SMTP_PASS   "App password (16 chars, no spaces)" ""
  ask MAIL_TO     "Send tickets to"                 "person13@example.com,person1@example.com"

  SECRET="$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets;print(secrets.token_hex(32))')"
  cat > "$ENV_FILE" <<ENVEOF
# Tech Help configuration. Edit, then: cd $APP_DIR && $DC up -d
MOM_SECRET_KEY=$SECRET
MOM_ROOT_PATH=$ROOT_PATH
MOM_SITE_NAME=Tech Help
MOM_HELPER_NAME=$HELPER
MOM_HELPER_PHONE=$HELPER_TEL

# --- the GPU desktop (not containerised: it has the graphics card) ---
MOM_DESKTOP_IP=$DESKTOP_IP
MOM_DESKTOP_MAC=$DESKTOP_MAC
MOM_DESKTOP_BROADCAST=$(printf '%s' "$DESKTOP_IP" | sed 's/\.[0-9]*$/.255/')
MOM_OLLAMA_PORT=11434
MOM_WHISPER_PORT=9000
MOM_OLLAMA_MODEL=$MODEL
MOM_OLLAMA_NUM_CTX=16384

# --- the three specialists (see setup-models.ps1 on the desktop) ---
MOM_MODEL_PROFILE=resident
MOM_MODEL_REASON=$MODEL
MOM_MODEL_VISION=qwen3-vl:2b
MOM_MODEL_EMBED=embeddinggemma:300m
MOM_DEFAULT_TZ=America/New_York
MOM_LEARNED_DIR=/app/knowledge/learned
MOM_AUTO_WAKE=1
MOM_BOOT_WAIT_SECONDS=240

# --- email ---
MOM_SMTP_PRESET=gmail
MOM_SMTP_USER=$SMTP_USER
MOM_SMTP_PASSWORD=$SMTP_PASS
MOM_MAIL_TO=$MAIL_TO
MOM_SMTP_FROM_NAME=Tech Help
ENVEOF
  chmod 600 "$ENV_FILE"
  ok "Wrote $ENV_FILE (permissions 600 — it holds your app password)"
fi

# --- 4. build and run --------------------------------------------------------
step "Building the container"
note "First build on a Pi takes several minutes. Later ones are cached."
cd "$APP_DIR"
$DC build --pull 2>&1 | tail -5
ok "Image built for $(uname -m)"

step "Starting"
$DC up -d
sleep 6
if [[ -n "$($DC ps -q momhelp 2>/dev/null)" ]] \
   && [[ "$(docker inspect -f '{{.State.Running}}' momhelp 2>/dev/null)" == "true" ]]; then
  ok "Container is running"
else
  warn "Container is not running. Recent output:"
  $DC logs --tail 30
  die "Fix the above, then: cd $APP_DIR && $DC up -d"
fi

# Note the explicit if/fi rather than `[[ ... ]] && { ... }`. Under `set -e`
# a failing && test as the last command in a loop body can abort the script.
HEALTHY=false
for i in $(seq 1 20); do
  if curl -fsS "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; then
    HEALTHY=true
    ok "Answering on 127.0.0.1:$PORT"
    break
  fi
  sleep 2
done
if [[ "$HEALTHY" != "true" ]]; then
  $DC logs --tail 30
  die "Never became healthy. Check the log above."
fi

# --- 5. accounts -------------------------------------------------------------
step "Accounts"
mk_user(){ docker exec momhelp python manage.py adduser "$1" ${2:+--admin} --generate \
             --name "$3" 2>/dev/null || true; }
EXISTING="$(docker exec momhelp python manage.py users 2>/dev/null || true)"
grep -q '^mom ' <<<"$EXISTING"   || mk_user mom   ""      "Mom"
grep -q '^jason ' <<<"$EXISTING" || mk_user jason "admin" "${HELPER:-Jason}"
docker exec momhelp python manage.py users 2>/dev/null || true

# --- 6. caddy ----------------------------------------------------------------
step "Caddy"
CADDY_SNIPPET="$APP_DIR/caddy-mom.conf"
cat > "$CADDY_SNIPPET" <<CADDYEOF
	# --- Tech Help (Mom) ------------------------------------------------
	# Paste inside the example.org block, BEFORE the catch-all
	# handle that forwards to Apache — otherwise Apache swallows it.
	redir $ROOT_PATH $ROOT_PATH/ 308
	handle_path $ROOT_PATH/* {
		request_body {
			max_size 64MB
		}
		reverse_proxy 127.0.0.1:$PORT {
			header_up X-Forwarded-For {remote_host}
			header_up X-Forwarded-Proto {scheme}
			header_up X-Forwarded-Prefix $ROOT_PATH
			transport http {
				read_timeout 300s
				write_timeout 300s
			}
		}
	}
CADDYEOF
if grep -q "handle_path $ROOT_PATH/\*" /etc/caddy/Caddyfile 2>/dev/null; then
  ok "Caddy already routes $ROOT_PATH"
else
  warn "Caddy is not routing $ROOT_PATH yet."
  note "The block to paste is saved at: $CADDY_SNIPPET"
  note "  sudo nano /etc/caddy/Caddyfile"
  note "  sudo caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile"
  note "  sudo systemctl reload caddy"
fi

# --- done --------------------------------------------------------------------
cat <<DONEEOF

$(ok "Deployed")

  ${BOLD}Her address${OFF}   https://example.org$ROOT_PATH/
  ${BOLD}Files${OFF}         $APP_DIR
  ${BOLD}Data${OFF}          docker volume 'momhelp-data'  (database, audio, tickets, sent mail)

  ${BOLD}Do these three things next${OFF}
  1. Fill in $APP_DIR/knowledge/10-profile.md with her actual phone and
     computer. Ten minutes there improves every answer she gets.
  2. On her phone: open the address, then Chrome menu -> "Add to Home screen".
     She never types it again.
  3. Check the backend is reachable from this Pi:
         curl -m5 http://$DESKTOP_IP:11434/api/tags
         curl -m5 http://$DESKTOP_IP:9000/health

  ${BOLD}Commands${OFF}
    cd $APP_DIR
    $DC logs -f                        watch it work
    $DC restart                        after editing .env
    $DC up -d --build                  after changing code
    docker exec momhelp python manage.py status     is the desktop awake?
    docker exec momhelp python manage.py alerts     anything the safety rules stopped
    docker exec momhelp python manage.py passwd mom --generate
    docker exec momhelp python test_mom.py          95 self-checks
         docker exec momhelp python test_growth.py       86 more
    git -C $APP_DIR checkout knowledge/             reset the AI's rules

  ${BOLD}Editing the rules${OFF}
    knowledge/ is mounted live. Edit a file, save, and the next question she
    asks uses it. No rebuild, no restart.

DONEEOF
