#!/usr/bin/env bash
# t0932_step5_sbx.sh - T0932 step 5 sandbox (2026-11-22, autopilot): a 2 GB file through the PATCHED intake
# (~/sandbox/t0932-src = live f4bd272 + steps 2-4) on the real USB SSD layout. Live intake untouched.
#
#   Part A (host -> HTTP): test_t0932_s5.py: staging cap with 2 GB starts, signal drop ~1 GB in, resume,
#          finish, sha256 of the stored file, no leftovers.
#   Part B (in the container): test_t0932_s5_place.py: place() of that 2 GB file to a folder on the NUC disk
#          (stands in for a NAS share: a different disk), wrong digest kept + no .part, right digest filed.
#   Part C: memory high-water mark of the server process (pid 1) after the upload - it must stream, not hold.
#
# Stand-in for /mnt/vault/drop-staging: a folder under /mnt/vault/media (same SSD; the real folder needs
# Jason's sudo, Ask on T0932). Test data goes to the 30-day trash at the end (moved to the home disk first:
# trash-put cannot trash on /mnt/vault until /mnt/vault/.Trash-1000 exists, found in step 1).
#
#     bash t0932_step5_sbx.sh            (exit 0 = all PASS)
set -u
RUN=$(mktemp -d ~/sandbox/intake-t0932s5-XXXXXX)
SSD=$(mktemp -d /mnt/vault/media/.sbx-t0932s5-XXXXXX)
mkdir -p "$SSD/unsorted" "$RUN/other"
touch "$SSD/unsorted/.mounted"
IMG=intake-sandbox:t0932s5; NAME=sbx-t0932s5; K=sbx-test-key-t0932s5; PORT=18252
CONF=$HOME/.config/drop/drop.toml
BAD=0
echo "run: $RUN  ssd stand-in: $SSD  src: ~/sandbox/t0932-src"
cp ~/sandbox/test_t0932_s5.py ~/sandbox/test_t0932_s5_place.py ~/sandbox/t0932-src/
docker build -q -t $IMG ~/sandbox/t0932-src >/dev/null || { echo "BUILD FAILED"; exit 1; }
docker rm -f $NAME >/dev/null 2>&1
# INTAKE_STAGE_MAX_MB=3000 so one 2 GB upload fits and a second is refused (the cap check, at real size).
docker run -d --name $NAME -p 127.0.0.1:$PORT:8100 \
  -e INTAKE_KEY=$K -e INTAKE_ALLOWED_PEERS=127.0.0.1,172.17.0.1 -e LLM_BASE=http://10.255.255.1 \
  -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels -e GOTENBERG_URL=http://10.255.255.1:3000 \
  -e INTAKE_LLM_TIMEOUT=2 -e PENDING_ROOT=/srv/unsorted/.pending -e INTAKE_MAX_MB=10240 \
  -e INTAKE_STAGE_MAX_MB=3000 \
  -e TMPDIR=/srv/unsorted/.tmp -e INTAKE_SENTINELS=/srv/unsorted/.mounted \
  -v $CONF:/config/drop.toml:ro -v "$SSD/unsorted:/srv/unsorted" -v "$RUN/other:/srv/other" $IMG >/dev/null
for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:$PORT/healthz && break; sleep 1; done

echo "=== making a 2 GB random source file on the NUC disk"
head -c 2147483648 /dev/urandom > "$RUN/src-2gb.bin"
ls -l "$RUN/src-2gb.bin"

echo "=== A. 2 GB over HTTP onto the SSD staging"
INTAKE_KEY=$K timeout 1800 python3 ~/sandbox/test_t0932_s5.py http://127.0.0.1:$PORT "$SSD" "$RUN/src-2gb.bin" \
  2>&1 | tee "$RUN/a.txt" | grep -E "^(PASS|FAIL|ALL|SOME|source|  verdict)|Traceback|Error"
grep -q "^ALL PASS" "$RUN/a.txt" || BAD=$((BAD + 1))

echo "=== C. server memory after the upload"
docker exec $NAME sh -c 'grep -E "VmHWM|VmRSS" /proc/1/status' | tee "$RUN/c.txt"
hwm=$(awk '/VmHWM/ {print $2}' "$RUN/c.txt")
if [ -n "$hwm" ] && [ "$hwm" -lt 524288 ]; then echo "PASS server VmHWM ${hwm} kB < 512 MB for a 2 GB upload"; \
else echo "FAIL server VmHWM ${hwm:-?} kB"; BAD=$((BAD + 1)); fi

echo "=== B. place() 2 GB across disks"
stored=$(python3 -c "import json,re,sys;t=open(sys.argv[1]).read();m=re.search(r'\"path\": \"(/srv/unsorted/[^\"]+)\"',t);print(m.group(1) if m else '')" "$RUN/a.txt")
if [ -n "$stored" ]; then
  docker exec $NAME python test_t0932_s5_place.py "$stored" 2>&1 | tee "$RUN/b.txt" | grep -E "^(PASS|FAIL|ALL|SOME|  memory)|Traceback|Error"
  grep -q "^ALL PASS" "$RUN/b.txt" || BAD=$((BAD + 1))
else echo "FAIL no stored path from part A"; BAD=$((BAD + 1)); fi

echo "=== D. regression on the same patched copy"
if [ -x ~/sandbox/intake_regress_live.sh ]; then
  timeout 1800 bash ~/sandbox/intake_regress_live.sh ~/sandbox/t0932-src > "$RUN/d.txt" 2>&1
  # Run 1 grepped "REGRESSION ALL PASS"; the script prints "REGRESSION: ALL PASS" (false FAIL). Both accepted.
  tail -3 "$RUN/d.txt"; grep -Eq "REGRESSION:? ALL PASS" "$RUN/d.txt" || BAD=$((BAD + 1))
fi

docker rm -f $NAME >/dev/null 2>&1
du -sh "$SSD" "$RUN" | cat
mv "$SSD" "$RUN/ssd-standin" && trash-put "$RUN/ssd-standin" "$RUN/src-2gb.bin" "$RUN/other"
echo "sandbox removed; test data moved to the 30-day trash; logs kept: $RUN"
[ "$BAD" = 0 ] && echo "T0932 STEP5: ALL PASS" || echo "T0932 STEP5: $BAD part(s) failed"
exit $(( BAD > 0 ))
