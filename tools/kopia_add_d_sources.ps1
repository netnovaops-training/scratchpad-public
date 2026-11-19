# kopia_add_d_sources.ps1 - add the D:\ folders that were in no backup to the PC's KopiaUI repo
# (NAS \\192.168.1.68\Computer backup), with a 24h schedule, and take their first snapshot.
#
# Why (T0004 step 2, 2026-11-18 autopilot): step 1 found 13 D:\ folders in no Kopia source and
# not in unified_backup.py either (its SOURCES list has only the site, Garden for All, the
# phone apps and the NUC/Pi trees). stock-list is the the store work tool, so losing it hurts.
# Secrets: vault-sealed and secrets-tools may hold sealed secret files; that is allowed because
# the Kopia repo is encrypted (T0004 rule: "secrets stay encrypted inside Kopia only"). Never
# copy those folders anywhere else.
#
# Uses KopiaUI's own kopia.exe and its config (%APPDATA%\kopia\repository.config); the
# repo password is already saved there, so nothing secret is typed or printed here.
# Adding a source is additive: nothing in the repo is changed or deleted.
#
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\kopia_add_d_sources.ps1 [-NoSnapshot]
# OUTCOME 2026-11-18 12:13-12:15 EST (first run, autopilot): all 13 folders got the 24h policy and a
# first snapshot, checked with `kopia snapshot list` + `kopia policy show` per folder (whole run ~90 s,
# dedup did most of it). Ignore rules confirmed: stock-list 706 MB on disk -> 102 MB snapshot, the
# difference is D:\stock-list\embed\node_modules (609 MB, rebuilt by npm install); tools 8 GB -> 112 MB
# (stag venv + hf-cache left out). Known glitch: the log file missed most lines while another process
# had it open (tail -f); the repo itself is the record, not the log.
param([switch]$NoSnapshot)

$ErrorActionPreference = 'Continue'
$env:KOPIA_CHECK_FOR_UPDATES = 'false'
$kopia = "$env:LOCALAPPDATA\Programs\KopiaUI\resources\server\kopia.exe"
$log = 'D:\scratchpad\audit\kopia-add-d-sources.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null

# The folders step 1 listed (sizes 2026-11-18). D:\tools is 8 GB, but 7.9 GB of that is
# D:\tools\stag (a downloaded app: venv + Hugging Face model cache, rebuilt by reinstalling),
# so those two are ignored below. Left out on purpose: wsl-archive (54 GB), backup-stage,
# ai-stack-backup-2026-11-12 and gfa-*target (dated copies or build output).
$folders = 'stock-list','Backups','vault-sealed','docker-backups','tools','pi-data','secrets-tools',
           'pc-health','paperless-feed','homelab-docs','portfolio','joblog','_parked'

# gitignore-style rules: a bare "venv/" matches a venv folder at any depth.
$ignores = 'venv/','.venv/','node_modules/','__pycache__/','hf-cache/','target/'

function Write-Log($msg) { $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg"; Add-Content $log $line; $line }

foreach ($f in $folders) {
    $p = "D:\$f"
    if (-not (Test-Path $p)) { Write-Log "$p missing, skipped"; continue }
    $args1 = @('policy','set',$p,'--snapshot-interval=24h')
    foreach ($i in $ignores) { $args1 += "--add-ignore=$i" }
    & $kopia @args1 2>&1 | Out-Null
    $pol = if ($LASTEXITCODE -eq 0) { 'policy ok' } else { "policy FAILED ($LASTEXITCODE)" }
    if ($NoSnapshot) { Write-Log "$p $pol"; continue }
    $t = Get-Date
    $out = & $kopia snapshot create $p 2>&1 | Select-Object -Last 1
    $res = if ($LASTEXITCODE -eq 0) { 'snapshot ok' } else { "snapshot FAILED ($LASTEXITCODE): $out" }
    Write-Log ("$p $pol, $res in {0:N0}s" -f ((Get-Date) - $t).TotalSeconds)
}
