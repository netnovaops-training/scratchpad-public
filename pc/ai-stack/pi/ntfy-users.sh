#!/bin/sh
# Create the ntfy accounts. Run ON THE PI.
#
# TWO ACCOUNTS, WITH DELIBERATELY DIFFERENT POWERS.
#
#   kuma    A MACHINE. It publishes alerts and must never be able to read
#           them back or touch anything else, so it gets write-only access
#           to exactly one topic and nothing more. It authenticates with a
#           TOKEN rather than a password, because the token is going to be
#           pasted into Uptime Kuma's notification settings and stored in
#           its database -- and a token can be revoked on its own without
#           locking anyone out of anything else.
#
#   jason   A PERSON, on a phone. Admin, because he needs to read every
#           topic and add new ones without coming back here.
#
# WHY WRITE-ONLY MATTERS FOR A MONITORING ACCOUNT. Its credential lives in
# a container's database on a machine whose whole job is to be reachable.
# If that leaks, the blast radius should be "someone can send me a fake
# notification", not "someone can read every alert this house has ever
# generated and learn exactly when the NUC is down".
#
#   ./ntfy-users.sh
set -e

TOPIC="${NTFY_TOPIC:-house}"

gen() { head -c 24 /dev/urandom | base64 | tr -d '/+=' | head -c 20; }

KUMA_PW=$(gen)
JASON_PW=$(gen)

echo
echo "  creating accounts on ntfy (topic: $TOPIC)"
echo

if docker exec ntfy ntfy user list 2>/dev/null | grep -q '^user kuma'; then
    echo "    kuma already exists"
else
    docker exec -e NTFY_PASSWORD="$KUMA_PW" ntfy \
        ntfy user add --role=user kuma
    echo "    + kuma created"
fi
# Write-only on the one topic. Explicitly denied everywhere else by the
# server's auth-default-access: deny-all.
docker exec ntfy ntfy access kuma "$TOPIC" wo
echo "    kuma -> write-only on '$TOPIC'"

if docker exec ntfy ntfy user list 2>/dev/null | grep -q '^user jason'; then
    echo "    jason already exists"
else
    docker exec -e NTFY_PASSWORD="$JASON_PW" ntfy \
        ntfy user add --role=admin jason
    echo "    + jason created (admin)"
    echo
    echo "    PHONE LOGIN     jason / $JASON_PW"
    echo "    Change it once you are in. It was generated here and has"
    echo "    therefore been on a terminal and in a log."
fi

echo
echo "  ---- the token to paste into Uptime Kuma ----"
docker exec ntfy ntfy token add --label "uptime-kuma" kuma
echo
echo "  Uptime Kuma -> Settings -> Notifications -> New:"
echo "      Type          ntfy"
echo "      Server URL    https://push.home.example.net"
echo "      Topic         $TOPIC"
echo "      Auth          Access token (paste the tk_... above)"
echo "      [x] Apply on all existing monitors   <- do not skip this"
echo
echo "  Then hit Test. If nothing arrives, it is the token or the topic;"
echo "  the server itself is already proven to reject anonymous posts."
echo
echo "  access table now:"
docker exec ntfy ntfy access
