#!/usr/bin/env bash
# =============================================================================
#  Sign-in not working? This finds out why, then fixes it.
#
#      sudo bash fix-login.sh              # diagnose only
#      sudo bash fix-login.sh --reset      # set fresh passwords and print them
#
#  WHY IT PROBABLY BROKE
#  Every `adduser ... --generate` REPLACES the password. It has been run several
#  times across the setup script, the recovery commands and deploy.sh, and only
#  the last one counts. Any password written down before the last run is simply
#  wrong now -- which looks exactly like the login being broken.
#
#  This does not guess. It tests the stored hash directly, so you find out
#  whether the password is wrong or something else is.
# =============================================================================
set -Eeuo pipefail

_pick(){ for d in /var/www/Mom /var/www/mom; do [ -d "$d" ] && { printf '%s' "$d"; return; }; done; printf '%s' /var/www/Mom; }
APP_DIR="${APP_DIR:-$(_pick)}"
CONTAINER="${CONTAINER:-momhelp}"
RESET=false
[[ "${1:-}" == "--reset" ]] && RESET=true

G=$'\033[32m'; Y=$'\033[33m'; R=$'\033[31m'; C=$'\033[36m'; D=$'\033[2m'; B=$'\033[1m'; O=$'\033[0m'
step(){ printf '\n%s==> %s%s\n' "$C" "$*" "$O"; }
ok(){   printf '%s  ok    %s%s\n' "$G" "$*" "$O"; }
note(){ printf '%s        %s%s\n' "$D" "$*" "$O"; }
warn(){ printf '%s  !     %s%s\n' "$Y" "$*" "$O"; }
bad(){  printf '%s  FAIL  %s%s\n' "$R" "$*" "$O"; }
[[ $EUID -eq 0 ]] || { echo "Run with sudo"; exit 1; }

# --- 1. who exists -------------------------------------------------------------
step "1. Which accounts exist"
docker exec "$CONTAINER" python -c "
from app import core
rows = core.conn().execute('SELECT username, role, must_change, tz FROM users ORDER BY role DESC, username').fetchall()
if not rows: print('  NO ACCOUNTS AT ALL')
for r in rows:
    flag = '  <- will be asked to change it' if r['must_change'] else ''
    print(f\"  {r['username']:26} {r['role']:6} tz={r['tz'] or '(default)':22}{flag}\")
" || { bad "could not read the database"; exit 1; }

# --- 2. is the app even serving the form? --------------------------------------
step "2. Is the sign-in page being served?"
CODE="$(docker exec "$CONTAINER" curl -s -o /dev/null -w '%{http_code}' -m 8 http://127.0.0.1:8001/login || echo 000)"
[[ "$CODE" == "200" ]] && ok "the app serves /login ($CODE)" || bad "/login returned $CODE"

PUB="$(curl -sk -o /dev/null -w '%{http_code}' -m 15 https://example.org/Mom/login || echo 000)"
[[ "$PUB" == "200" ]] && ok "reachable from the internet ($PUB)" || warn "public /Mom/login returned $PUB"

# --- 3. the cookie ------------------------------------------------------------
step "3. Will the browser keep the session cookie?"
RP="$(sed -n 's/^MOM_ROOT_PATH=//p' "$APP_DIR/.env" 2>/dev/null | tail -1)"
CP="$(sed -n 's/^MOM_COOKIE_PATH=//p' "$APP_DIR/.env" 2>/dev/null | tail -1)"
note "MOM_ROOT_PATH=${RP:-(unset)}   MOM_COOKIE_PATH=${CP:-(follows root path)}"
if [[ -n "$RP" && "$RP" != "/Mom" ]]; then
  bad "MOM_ROOT_PATH is '$RP' but Caddy serves /Mom"
  note "The cookie is scoped to '$RP', so the browser will not send it back to"
  note "/Mom -- you sign in successfully and land straight back on the form."
  note "  sudo sed -i 's|^MOM_ROOT_PATH=.*|MOM_ROOT_PATH=/Mom|' $APP_DIR/.env"
  note "  cd $APP_DIR && sudo docker compose restart"
else
  ok "cookie path matches the URL she uses"
fi

# --- 4. test the actual stored hashes -----------------------------------------
if ! $RESET; then
  step "4. Test a password without changing anything"
  note "Paste one to check it against the stored hash. Enter to skip."
  read -r -p "    username: " U </dev/tty || U=""
  if [[ -n "$U" ]]; then
    read -r -s -p "    password: " P </dev/tty || P=""; echo
    docker exec -e U="$U" -e P="$P" "$CONTAINER" python -c "
import os
from app import core
u = core.normalize_username(os.environ['U'])
r = core.get_user(u)
if not r:
    print('  NO SUCH ACCOUNT:', u)
    print('  (usernames are stored lowercase; the email must match exactly)')
elif core.verify_password(os.environ['P'], r['pw_salt'], r['pw_hash']):
    print('  THAT PASSWORD IS CORRECT for', u)
    print('  must_change =', r['must_change'], '(1 means it will ask you to change it)')
else:
    print('  That password is WRONG for', u)
    print('  The stored hash does not match. Reset it:  sudo bash fix-login.sh --reset')
"
  fi
fi

# --- 5. reset -----------------------------------------------------------------
if $RESET; then
  step "5. Setting fresh passwords"
  docker exec "$CONTAINER" python -c "
from app import core
import secrets
words = 'amber anchor basil cedar copper daisy ember fable garnet harbor indigo jasper kettle lantern meadow nutmeg olive pepper quartz ribbon saffron timber umber velvet willow yarrow'.split()
rows = core.conn().execute('SELECT username, role FROM users').fetchall()
if not rows:
    print('  no accounts to reset')
for r in rows:
    pw = '-'.join(secrets.choice(words) for _ in range(3)) + str(secrets.randbelow(90)+10)
    core.set_password(r['username'], pw)
    with core.tx() as c:
        c.execute('UPDATE users SET must_change=0 WHERE username=?', (r['username'],))
    print(f\"  {r['username']:26} {r['role']:6}  {pw}\")
print()
print('  Nobody will be asked to change these. They are the passwords.')
"
  step "6. Proving one actually signs in"
  note "posting to the real login form, exactly as a browser would"
  docker exec "$CONTAINER" python -c "
from fastapi.testclient import TestClient
from app import core, main
import secrets
u = core.conn().execute(\"SELECT username FROM users WHERE role='user' LIMIT 1\").fetchone()
if not u:
    u = core.conn().execute('SELECT username FROM users LIMIT 1').fetchone()
if u:
    pw = 'proof-' + secrets.token_hex(6)
    core.set_password(u['username'], pw)
    with core.tx() as c:
        c.execute('UPDATE users SET must_change=0 WHERE username=?', (u['username'],))
    c = TestClient(main.app, base_url='https://x')
    r = c.post('/login', data={'username': u['username'], 'password': pw},
               follow_redirects=False)
    print('  login status:', r.status_code, '(303 means it worked)')
    print('  session cookie set:', 'momhelp_session' in r.cookies or
          any('momhelp_session' in v for v in r.headers.get_list('set-cookie')))
    print()
    print('  TEMPORARY password just used for the test:')
    print('   ', u['username'], '->', pw)
    print('  Run with --reset again if you want a fresh one.')
" 2>/dev/null || warn "could not run the sign-in proof"
fi

cat <<EOF

$(printf '%s%s%s' "$B" "If it still will not let you in" "$O")

  ${D}Wrong password${O}   Most likely. Every 'adduser --generate' replaced it, and
                   only the last run counts. Reset with --reset above.
  ${D}Wrong username${O}   It is the full email, lowercase.
  ${D}Old cookie${O}       Sign out, or open a private window. A cookie signed with
                   a previous MOM_SECRET_KEY is silently ignored.
  ${D}Locked out${O}       Ten wrong tries locks that address for 10 minutes.
                   Clear it:  docker exec $CONTAINER python -c \\
                     "from app import core; core.conn().execute('DELETE FROM login_attempts'); core.conn().commit()"

EOF
