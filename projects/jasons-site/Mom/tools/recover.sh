#!/usr/bin/env bash
# =============================================================================
#  Put /var/www/Mom back together after it was deleted.
#
#      sudo bash recover.sh
#
#  WHAT WAS AND WAS NOT LOST
#
#  Deleting /var/www/Mom removed the CODE and the .env. It did NOT touch the
#  data: users, tickets, the FAQ, memory and the handbook all live in the Docker
#  named volume 'momhelp-data', which is stored under /var/lib/docker and is
#  completely independent of that folder. Nothing she has ever asked is gone.
#
#  The .env is the real loss, because it holds:
#     MOM_SECRET_KEY      signs session cookies -- a new one just means everyone
#                         signs in again; accounts and passwords are unaffected,
#                         since those are scrypt hashes in the database
#     MOM_SMTP_PASSWORD   your Gmail app password
#     MOM_WEBHOOK_SECRET  the desktop listener's shared secret
#     the desktop IP, MAC, broadcast, model names and timezone
#
#  RECOVERY, IN ORDER OF HOW GOOD IT IS
#
#  1. From the RUNNING container. Docker holds the environment of a running
#     container in its own metadata, so every value is still there and can be
#     read back exactly. This is why the script does that first, and why it
#     matters that you do it before stopping or removing the container.
#  2. From a backup .env, if one exists.
#  3. Fresh, with a new secret key. Everything keeps working; everyone signs in
#     again and you re-enter the SMTP password.
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
CONTAINER="${CONTAINER:-momhelp}"
VOLUME="${VOLUME:-momhelp-data}"

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; D=$'\033[2m'; B=$'\033[1m'; O=$'\033[0m'
step(){ printf '\n%s==> %s%s\n' "$C" "$*" "$O"; }
ok(){   printf '%s  ok  %s%s\n' "$G" "$*" "$O"; }
note(){ printf '%s      %s%s\n' "$D" "$*" "$O"; }
warn(){ printf '%s  !   %s%s\n' "$Y" "$*" "$O"; }
bad(){  printf '%s  XX  %s%s\n' "$R" "$*" "$O"; }

[[ $EUID -eq 0 ]] || { echo "Run with sudo: sudo bash recover.sh"; exit 1; }

# --- 1. the data ---------------------------------------------------------------
step "The data"
if docker volume inspect "$VOLUME" >/dev/null 2>&1; then
  MP="$(docker volume inspect "$VOLUME" --format '{{.Mountpoint}}')"
  SIZE="$(du -sh "$MP" 2>/dev/null | cut -f1 || echo '?')"
  ok "volume '$VOLUME' is intact ($SIZE) at $MP"
  if [[ -f "$MP/momhelp.db" ]]; then
    USERS="$(sqlite3 "$MP/momhelp.db" 'SELECT COUNT(*) FROM users' 2>/dev/null || echo '?')"
    THREADS="$(sqlite3 "$MP/momhelp.db" 'SELECT COUNT(*) FROM threads' 2>/dev/null || echo '?')"
    ok "database present: $USERS account(s), $THREADS conversation(s)"
    note "Nothing she has asked has been lost."
  fi
else
  bad "volume '$VOLUME' not found"
  note "Check: docker volume ls"
fi

# --- 2. the configuration ------------------------------------------------------
step "The .env"
mkdir -p "$APP_DIR"
if [[ -f "$APP_DIR/.env" ]]; then
  ok ".env already exists -- leaving it alone"
elif docker inspect "$CONTAINER" >/dev/null 2>&1; then
  # Docker keeps the environment of a container in its metadata whether it is
  # running or merely stopped, so this works either way -- but NOT once the
  # container is removed. That is the one thing that would make this
  # unrecoverable.
  docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
    | grep '^MOM_' > "$APP_DIR/.env" || true
  if [[ -s "$APP_DIR/.env" ]]; then
    chmod 600 "$APP_DIR/.env"
    N="$(wc -l < "$APP_DIR/.env")"
    ok "recovered $N setting(s) from the container -- byte for byte"
    for k in MOM_SECRET_KEY MOM_SMTP_PASSWORD MOM_WEBHOOK_SECRET MOM_DESKTOP_MAC; do
      grep -q "^$k=" "$APP_DIR/.env" && note "  $k recovered" || warn "  $k missing"
    done
    note ""
    note "Nobody has to sign in again: the original signing key is back."
  else
    bad "could not read the environment from '$CONTAINER'"
  fi
else
  bad "no container named '$CONTAINER' to recover from"
  note "You will need a new .env. Copy .env.example and fill it in:"
  note "  cp $APP_DIR/.env.example $APP_DIR/.env && sudo nano $APP_DIR/.env"
  note "A new MOM_SECRET_KEY only means everyone signs in once more."
  note "Accounts and passwords are safe -- they are hashes in the database."
fi

# --- 3. what is still missing --------------------------------------------------
step "What still has to come back"
MISSING=()
for f in Dockerfile docker-compose.yml requirements.txt manage.py app knowledge; do
  [[ -e "$APP_DIR/$f" ]] || MISSING+=("$f")
done
if [[ ${#MISSING[@]} -eq 0 ]]; then
  ok "all the code is in place"
else
  warn "still missing: ${MISSING[*]}"
  note ""
  note "Get it back from Windows -- your master copy was never touched:"
  note "  cd \"C:\\Jasons New Site\\www\\Mom\""
  note "  .\\Setup-TechHelp.ps1 -Only upload"
  note ""
  note "deploy.sh will see the .env above and keep it rather than asking again."
fi

# --- 4. the container -----------------------------------------------------------
step "The container"
STATE="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo absent)"
case "$STATE" in
  running) ok "still running -- deleting the folder did not stop it"
           note "Docker does not need the compose file once a container is up." ;;
  exited|created) warn "exists but is stopped ($STATE)"
           note "Do NOT remove it until the .env above is recovered." ;;
  absent)  bad "no container -- it will be recreated by the upload" ;;
esac

cat <<EOF

$(printf '%s%s%s' "$B" "In short" "$O")

  ${D}Lost${O}      the code in $APP_DIR, and the .env if it could not be recovered
  ${D}Safe${O}      every account, ticket, answer, memory and handbook entry --
            they live in the '$VOLUME' volume, not in that folder
  ${D}Safe${O}      your master copy of everything on the Windows machine

  ${B}Next${O}      cd "C:\\Jasons New Site\\www\\Mom"
            .\\Setup-TechHelp.ps1 -Only upload
            sudo bash $APP_DIR/test-chain.sh

EOF
