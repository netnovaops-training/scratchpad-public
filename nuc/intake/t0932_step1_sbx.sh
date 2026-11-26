#!/usr/bin/env bash
# t0932_step1_sbx.sh - T0932 step 1 sandbox (2026-11-22, autopilot): Atomic Drop staging on the USB SSD.
#
# What it proves, against a throw-away intake built from a FRESH copy of the live code (live intake untouched):
#   1. With the WHOLE /srv/unsorted bind-mounted from a folder on the USB SSD (/mnt/vault), the resumable
#      staging (INTAKE_STAGE = /srv/unsorted/.atomic-staging), TMPDIR (/srv/unsorted/.tmp), .pending and
#      .failed all sit on the SSD - one disk, so the same-disk renames in app.py:85-88 / resumable.py:546 still
#      work. (Moving only TMPDIR + STAGE would split them across disks: 11-21 audit point 2.)
#   2. test_resumable.py (start, pieces, cut-off piece, resume, finish + sha256) passes on that layout.
#   3. INTAKE_SENTINELS with /srv/unsorted/.mounted: healthz 200 while the sentinel exists, 503
#      "not mounted: unsorted" when it is gone (stands in for "USB SSD dropped, writes would hit the NUC disk").
#
# Why a stand-in folder: /mnt/vault is root-owned and NUC sudo needs Jason's password, so the real
# /mnt/vault/drop-staging is made by him before the swap (step 6). The stand-in is on the same SSD
# (/mnt/vault/media is server-owned), so disk and filesystem are the real ones. It goes to the trash at the end.
#
#     bash t0932_step1_sbx.sh            (exit 0 = all PASS)
set -u
RUN=$(mktemp -d ~/sandbox/intake-t0932-XXXXXX)            # src copy lives here (NUC disk)
SSD=$(mktemp -d /mnt/vault/media/.sbx-t0932-XXXXXX)        # the stand-in for /mnt/vault/drop-staging
cp -a ~/nuc-homelab/intake "$RUN/src"
mkdir -p "$SSD/unsorted"
touch "$SSD/unsorted/.mounted"
IMG=intake-sandbox:t0932; NAME=sbx-t0932; K=sbx-test-key-t0932; PORT=18241
CONF=$HOME/.config/drop/drop.toml
BAD=0
ok()  { echo "PASS $*"; }
bad() { echo "FAIL $*"; BAD=$((BAD + 1)); }
echo "run: $RUN  ssd stand-in: $SSD  (live intake at $(git -C ~/nuc-homelab log -1 --format=%h -- intake))"
docker build -q -t $IMG "$RUN/src" >/dev/null || { echo "BUILD FAILED"; exit 1; }
docker rm -f $NAME >/dev/null 2>&1
# Same env shape as the live service, but no LLM/auth/gotenberg/clamd (unreachable address = fast fail), and the
# SSD sentinel added to INTAKE_SENTINELS. No NAS shares mounted, so only the unsorted sentinel is listed.
docker run -d --name $NAME -p 127.0.0.1:$PORT:8100 \
  -e INTAKE_KEY=$K -e INTAKE_ALLOWED_PEERS=127.0.0.1,172.17.0.1 -e LLM_BASE=http://10.255.255.1 \
  -e AUTH_LEVELS_URL=http://10.255.255.1/auth/levels -e GOTENBERG_URL=http://10.255.255.1:3000 \
  -e INTAKE_LLM_TIMEOUT=2 -e PENDING_ROOT=/srv/unsorted/.pending -e INTAKE_MAX_MB=10240 \
  -e TMPDIR=/srv/unsorted/.tmp -e INTAKE_SENTINELS=/srv/unsorted/.mounted \
  -v $CONF:/config/drop.toml:ro -v "$SSD/unsorted:/srv/unsorted" $IMG >/dev/null
for i in $(seq 1 30); do curl -s -o /dev/null http://127.0.0.1:$PORT/healthz && break; sleep 1; done

echo "=== 1. one disk: staging, tmp, pending, failed all on the SSD"
docker exec $NAME python -c "
import os, tempfile, pathlib, resumable
for p in [resumable.STAGE, pathlib.Path('/srv/unsorted/.pending'), resumable.FAILED]: p.mkdir(parents=True, exist_ok=True)
print('TMP', tempfile.gettempdir())
for name, p in [('unsorted','/srv/unsorted'),('stage',str(resumable.STAGE)),('tmp',tempfile.gettempdir()),
                ('pending','/srv/unsorted/.pending'),('failed',str(resumable.FAILED))]:
    print('DEV', name, os.stat(p).st_dev)
" > "$RUN/dev.txt" 2>&1
cat "$RUN/dev.txt"
grep -q "^TMP /srv/unsorted/.tmp$" "$RUN/dev.txt" && ok "tempfile uses /srv/unsorted/.tmp" || bad "tempfile dir is not /srv/unsorted/.tmp"
n=$(grep "^DEV" "$RUN/dev.txt" | awk '{print $3}' | sort -u | wc -l)
[ "$n" = 1 ] && ok "all five folders on one device" || bad "folders on $n devices"
host_dev=$(stat -c %d "$SSD/unsorted"); vault_dev=$(stat -c %d /mnt/vault)
[ "$host_dev" = "$vault_dev" ] && ok "host side is the USB SSD (dev $vault_dev)" || bad "host folder dev $host_dev != /mnt/vault $vault_dev"

echo "=== 2. healthz with the SSD sentinel present"
c=$(curl -s -H "X-Drop-Key: $K" -o "$RUN/h1.json" -w "%{http_code}" http://127.0.0.1:$PORT/healthz); cat "$RUN/h1.json"; echo
# CLAMD_HOST is unset here, so the virus check may be off or report down: accept 200, or 503 whose ONLY problem is
# the virus check, so this test judges the disk part and nothing else.
if [ "$c" = 200 ] || { python3 -c "import json,sys;p=json.load(open(sys.argv[1]))['problems'];sys.exit(0 if all('virus' in x for x in p) else 1)" "$RUN/h1.json"; }; then
  ok "healthz has no disk/mount problem ($c)"; else bad "healthz $c with sentinel present"; fi

echo "=== 3. test_resumable on the SSD layout"
out=$(INTAKE_KEY=$K timeout 900 python3 "$RUN/src/test_resumable.py" http://127.0.0.1:$PORT "$SSD" 2>&1)
printf '%s\n' "$out" | grep -E "^(PASS|FAIL|ALL|SOME)|Traceback" | tail -20
if printf '%s\n' "$out" | grep -q "^FAIL" || ! printf '%s\n' "$out" | grep -q "^ALL PASS"; then bad "test_resumable"; else ok "test_resumable"; fi

echo "=== 4. SSD gone (sentinel removed) -> healthz 503 not mounted"
# Renamed, not deleted: trash-put cannot trash on /mnt/vault (no per-volume trash dir; found 11-22).
mv "$SSD/unsorted/.mounted" "$SSD/unsorted/.mounted.off"
c=$(curl -s -H "X-Drop-Key: $K" -o "$RUN/h2.json" -w "%{http_code}" http://127.0.0.1:$PORT/healthz); cat "$RUN/h2.json"; echo
[ "$c" = 503 ] && grep -q "not mounted: unsorted" "$RUN/h2.json" && ok "503 not mounted: unsorted" || bad "healthz $c without sentinel"

docker rm -f $NAME >/dev/null 2>&1
du -sh "$SSD" | cat
# trash-put refuses files on /mnt/vault (found 11-22: no /mnt/vault/.Trash-1000 and /mnt/vault is root-owned),
# so the stand-in is moved to the home disk first and trashed there (30-day trash, never a hard delete).
mv "$SSD" "$RUN/ssd-standin" && trash-put "$RUN/ssd-standin"
echo "sandbox removed; SSD stand-in moved to the trash; run folder kept: $RUN"
[ "$BAD" = 0 ] && echo "T0932 STEP1: ALL PASS" || echo "T0932 STEP1: $BAD check(s) failed"
exit $(( BAD > 0 ))
