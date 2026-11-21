#!/usr/bin/env bash
# test_scan.sh - T0920 sandbox test: intake (patched copy) + clamav on a private docker network; intake streams
# each file to clamd (INSTREAM). Never touches the live intake.   bash test_scan.sh
# Checks: EICAR test file -> 422 + held in .quarantine with WHY.txt; clean file -> 200 filed; scanner stopped ->
# clean file 503 + held (nothing filed unchecked, nothing lost); then the 7 old suites still pass is a separate run.
set -u
SB=/home/user/sandbox/t0920
SRC=$SB/intake
DATA=$SB/data
IMG=intake-sandbox:t0920
NET=t0920net
K=sbx-test-key-0920
fail=0
ok()  { echo "PASS $1"; }
bad() { echo "FAIL $1"; fail=1; }
mkdir -p $DATA/unsorted $DATA/books $DATA/manga
docker build -q -t $IMG "$SRC" >/dev/null || { echo "BUILD FAILED"; exit 1; }
docker network inspect $NET >/dev/null 2>&1 || docker network create $NET >/dev/null
GW=$(docker network inspect $NET -f '{{(index .IPAM.Config 0).Gateway}}')
docker rm -f sbx-clamav sbx-scan >/dev/null 2>&1
docker run -d --name sbx-clamav --network $NET -v $SB/db:/var/lib/clamav clamav/clamav:stable >/dev/null
docker run -d --name sbx-scan --network $NET -p 127.0.0.1:18231:8100 -e INTAKE_KEY=$K \
  -e INTAKE_ALLOWED_PEERS=127.0.0.1,$GW -e LLM_BASE=http://10.255.255.1 -e INTAKE_LLM_TIMEOUT=2 \
  -e PENDING_ROOT=/srv/unsorted/.pending -e CLAMD_HOST=sbx-clamav \
  -v $DATA/unsorted:/srv/unsorted -v $DATA/books:/srv/books -v $DATA/manga:/srv/manga $IMG >/dev/null
for i in $(seq 1 60); do docker logs sbx-clamav 2>&1 | grep -q "clamd started" && break; sleep 3; done
sleep 3
up() {  # $1 local file -> "code" ; body saved to /tmp/t920.out
  curl -s -o /tmp/t920.out -w "%{http_code}" -H "X-Drop-Key: $K" -H "X-Auth-Email: tester@example.com" \
    -H "X-Auth-Level: full" -F "file=@$1" http://127.0.0.1:18231/upload
}
T=$(mktemp -d)
printf '%s' 'X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*' > $T/eicar-test.txt
echo "a perfectly clean note for the virus check test" > $T/clean-note.txt
q0=$(find $DATA/unsorted/.quarantine -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
c=$(up $T/eicar-test.txt); [ "$c" = 422 ] && ok "EICAR -> 422 ($c)" || bad "EICAR -> $c $(head -c 200 /tmp/t920.out)"
q1=$(find $DATA/unsorted/.quarantine -mindepth 1 -maxdepth 1 -type d 2>/dev/null | wc -l)
[ $((q1 - q0)) -eq 1 ] && ok "EICAR held in .quarantine (+1)" || bad "quarantine count $q0 -> $q1"
w=$(ls -t $DATA/unsorted/.quarantine/*/WHY.txt 2>/dev/null | head -1)
grep -q '"infected"' "$w" 2>/dev/null && grep -q Eicar "$w" && ok "WHY.txt says infected + signature" || bad "WHY.txt"
find $DATA -name 'eicar-test.txt' -not -path '*/.quarantine/*' | grep -q . && bad "EICAR also filed somewhere" || ok "EICAR not filed anywhere"
c=$(up $T/clean-note.txt); [ "$c" = 200 ] && ok "clean file -> 200 filed" || bad "clean -> $c $(head -c 200 /tmp/t920.out)"
docker stop sbx-clamav >/dev/null
c=$(up $T/clean-note.txt); [ "$c" = 503 ] && ok "scanner down -> 503 ($c)" || bad "scanner down -> $c"
q2=$(find $DATA/unsorted/.quarantine -mindepth 1 -maxdepth 1 -type d | wc -l)
[ $((q2 - q1)) -eq 1 ] && ok "scanner down: file kept in .quarantine, not lost" || bad "kept count $q1 -> $q2"
grep -q "virus check" /tmp/t920.out && ok "phone gets plain words" || bad "message: $(head -c 150 /tmp/t920.out)"
trash-put $T /tmp/t920.out
docker rm -f sbx-clamav sbx-scan >/dev/null; docker network rm $NET >/dev/null
echo "$([ $fail = 0 ] && echo ALL PASS || echo SOME FAILED)"
exit $fail
