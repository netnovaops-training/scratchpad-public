#!/usr/bin/env bash
# intake_regress_live.sh - every intake test against throw-away sandboxes built from a FRESH copy of the LIVE
# intake, with today's live setup: people from drop.toml (T0927) and the virus check ON (T0920, a sandbox clamav).
# Never touches the live intake.     bash intake_regress_live.sh
#
# Why (2026-11-21): intake_regress3.sh tested a fixed old copy and REUSED ~/sandbox/t0931b-data, so a file left
# by an earlier run made test_finish fail ("filed exactly once (2 copies)") - T0952. Here every run starts from a
# new mktemp data folder and a new copy of ~/nuc-homelab/intake, so the result always describes the live code.
#
# Variables: RUN (this run's folder: src/ copy + data/), IMG (sandbox image tag), NET (private docker network
# shared with the sandbox clamav), GW (that network's gateway = the address the host's requests arrive from),
# K (a throw-away test key), CONF (the live drop.toml, mounted read-only), DB (clamav signature cache, reused).
set -u
RUN=$(mktemp -d ~/sandbox/intake-run-XXXXXX)
FROM=${1:-$HOME/nuc-homelab/intake}   # optional: test a patched copy instead of the live code (copied, never edited)
cp -a "$FROM" "$RUN/src"
SRC=$RUN/src; B=$RUN/data
IMG=intake-sandbox:live; NET=intake-sbx-net; K=sbx-test-key-live
CONF=$HOME/.config/drop/drop.toml; DB=$HOME/sandbox/t0920/db
echo "run folder: $RUN (live intake at $(git -C ~/nuc-homelab log -1 --format=%h -- intake))"
export INTAKE_KEY=$K
mkdir -p $B/a/books $B/a/manga $B/a/media $B/a/unsorted $B/a/consume $B/ro/unsorted $B/b/unsorted $B/b2/unsorted \
  $B/n/unsorted $B/r/unsorted $B/s/unsorted
docker build -q -t $IMG "$SRC" >/dev/null || { echo "BUILD FAILED"; exit 1; }
docker network inspect $NET >/dev/null 2>&1 || docker network create $NET >/dev/null
GW=$(docker network inspect $NET -f '{{(index .IPAM.Config 0).Gateway}}')
ALL="rl-clamav sbx-a sbx-ro sbx-n sbx-b sbx-bres sbx-bblk sbx-r rl-scan"
for n in $ALL; do docker rm -f $n >/dev/null 2>&1; done
docker run -d --name rl-clamav --network $NET -v $DB:/var/lib/clamav clamav/clamav:stable >/dev/null
for i in $(seq 1 60); do docker logs rl-clamav 2>&1 | grep -q "clamd started" && break; sleep 3; done
M="-v $CONF:/config/drop.toml:ro"
E="--network $NET -e INTAKE_KEY=$K -e INTAKE_TOKEN=t-regress -e INTAKE_ALLOWED_PEERS=127.0.0.1,$GW -e LLM_BASE=http://10.255.255.1 -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels -e GOTENBERG_URL=http://10.255.255.1:3000 -e INTAKE_LLM_TIMEOUT=2 -e PENDING_ROOT=/srv/unsorted/.pending -e INTAKE_MAX_MB=10240 -e CLAMD_HOST=rl-clamav $M"
docker run -d --name sbx-a -p 127.0.0.1:18201:8100 $E -v $B/a/books:/srv/books -v $B/a/manga:/srv/manga \
  -v $B/a/media:/srv/media -v $B/a/unsorted:/srv/unsorted -v $B/a/consume:/srv/consume $IMG >/dev/null
docker run -d --name sbx-ro -p 127.0.0.1:18202:8100 $E -v $B/ro/unsorted:/srv/unsorted:ro $IMG >/dev/null
docker run -d --name sbx-n -p 127.0.0.1:18205:8100 $E -v $B/n/unsorted:/srv/unsorted $IMG >/dev/null
docker run -d --name sbx-b -p 127.0.0.1:18211:8100 $E -v $B/b/unsorted:/srv/unsorted $IMG >/dev/null
docker run -d --name sbx-bres -p 127.0.0.1:18212:8100 $E -e INTAKE_RESERVE_MB=99999999 -v $B/b2/unsorted:/srv/unsorted $IMG >/dev/null
docker run -d --name sbx-bblk -p 127.0.0.1:18213:8100 --network $NET -e INTAKE_KEY=$K -e INTAKE_TOKEN=t-regress -e LLM_BASE=http://10.255.255.1 -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels -e GOTENBERG_URL=http://10.255.255.1:3000 -e INTAKE_ALLOWED_PEERS=127.0.0.1 $M -v $B/b2/unsorted:/srv/unsorted $IMG >/dev/null
docker run -d --name sbx-r -p 127.0.0.1:8199:8100 $E -v $B/r/unsorted:/srv/unsorted $IMG >/dev/null
docker run -d --name rl-scan -p 127.0.0.1:18231:8100 $E -v $B/s/unsorted:/srv/unsorted $IMG >/dev/null
sleep 6
# T0963 (2026-11-21): the script used to exit 0 whatever happened, and a suite that died without printing a
# FAIL line showed nothing. Now each suite must print "ALL PASS" (or "PASS tables") and no FAIL line; BAD counts
# the parts that did not, and the exit code is 1 if any did.
BAD=0
run() {
  local name=$1 out; shift; echo "=== $name"
  out=$(timeout 900 "$@" 2>&1)
  printf '%s\n' "$out" | grep -E "^(PASS|FAIL|ALL|SOME)|Traceback|Error|error" | tail -16
  if printf '%s\n' "$out" | grep -q "^FAIL" || ! printf '%s\n' "$out" | grep -qE "^(ALL PASS|PASS tables)"; then
    BAD=$((BAD + 1)); echo "!!! $name did not pass"
  fi
}
run tables docker run --rm $M --entrypoint python $IMG -c "import dropconfig as d, portal, classify, scan; print('PASS tables', repr(d.ERROR), len(portal.WHO), len(portal.PRIVATE_OK), len(classify.PEOPLE), len(classify.SHELF))"
run test_t0931b docker run --rm --network $NET -e INTAKE_KEY=$K -e INTAKE_TOKEN=t-regress -e CLAMD_HOST=rl-clamav $M --entrypoint python $IMG test_t0931b.py
run test_t0931 python3 $SRC/test_t0931.py http://127.0.0.1:18205 $K $B/n/unsorted sbx-n
run test_parallel python3 $SRC/test_parallel.py http://127.0.0.1:18201 $K $B/a
run test_part2 python3 $SRC/test_part2.py http://127.0.0.1:18201 $K $B/a http://127.0.0.1:18202
run test_finish python3 $SRC/test_finish.py http://127.0.0.1:18211 http://127.0.0.1:18212 $B/b
run test_gatekeeper python3 $SRC/test_gatekeeper.py http://127.0.0.1:18211 http://127.0.0.1:18213
run test_resumable python3 $SRC/test_resumable.py http://127.0.0.1:8199 $B/r
echo "=== virus check (EICAR held, clean filed)"
T=$(mktemp -d)
printf '%s' 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' > $T/eicar-test.txt
echo "a clean note for the live regression" > $T/clean-note.txt
up() { curl -s -o /dev/null -w "%{http_code}" -H "X-Drop-Key: $K" -H "X-Auth-Email: tester@example.com" -H "X-Auth-Level: full" -F "file=@$1" http://127.0.0.1:18231/upload; }
c=$(up $T/eicar-test.txt); [ "$c" = 422 ] && echo "PASS EICAR -> 422" || { echo "FAIL EICAR -> $c"; BAD=$((BAD + 1)); }
[ -n "$(ls -A $B/s/unsorted/.quarantine 2>/dev/null)" ] && echo "PASS EICAR held in .quarantine" || { echo "FAIL nothing in .quarantine"; BAD=$((BAD + 1)); }
c=$(up $T/clean-note.txt); [ "$c" = 200 ] && echo "PASS clean -> 200 filed" || { echo "FAIL clean -> $c"; BAD=$((BAD + 1)); }
trash-put $T
for n in $ALL; do docker rm -f $n >/dev/null 2>&1; done
echo "sandboxes removed; run folder kept for reading: $RUN (move it to the trash when done)"
# T0963: one result line and an exit code (0 = every suite and virus check passed)
[ "$BAD" = 0 ] && echo "REGRESSION: ALL PASS" || echo "REGRESSION: $BAD part(s) did not pass"
exit $(( BAD > 0 ))
