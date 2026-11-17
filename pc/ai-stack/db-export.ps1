# Export every database on SuperNova. Mondays and Thursdays, 07:00.
#
# Jason, 2026-09-07: "we can run a script to export the databases to back up
# the table monday and Thur every week at 7 am".
#
# WHY THIS EXISTS WHEN THE NUC ALREADY BACKS UP NIGHTLY. vault-backup.sh runs
# on the NUC and dumps the NUC's databases. Nothing has ever dumped this
# machine's -- and this machine holds Nextcloud, Tandoor, LiteLLM's usage
# history and the portfolio database. Docker volumes are captured on the NUC
# side, but a tar of a running postgres data directory is not something you
# can restore from with any confidence: it is a copy of files that were being
# written to while they were copied.
#
# A DUMP IS DIFFERENT FROM A FILE COPY, and that difference is the whole
# point. `pg_dumpall` asks postgres for a consistent snapshot; postgres
# decides when it is safe to answer. Copying the same directory with tar asks
# the filesystem, which does not know what a transaction is.
#
# WHAT IT WRITES, AND WHERE. One dated directory per run, onto the NUC's
# encrypted vault -- so the PC's databases survive the PC. Kept 8 weeks
# (about 16 runs), then pruned.
#
# EVERY DUMP IS CHECKED FOR SIZE. A failed dump still produces a file, and a
# zero-byte "backup" that nobody notices is worse than no backup at all,
# because it looks like one in a directory listing.

param(
    [string]$Dest = "/mnt/vault/backups/pc-databases",
    [string]$NucHost = "server@192.168.1.86",
    [int]$KeepWeeks = 8,
    [switch]$Install       # create the scheduled task and exit
)

$ErrorActionPreference = "Continue"
$stamp = Get-Date -Format "yyyy-MM-dd"
$log = Join-Path $PSScriptRoot "transcripts\db-export.log"

function Say($m) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $m
    Write-Output $line
    Add-Content -Path $log -Value $line -Encoding utf8 -ErrorAction SilentlyContinue
}

# --- install the schedule and stop ------------------------------------------
if ($Install) {
    # RUNS AS THE LOGGED-IN USER, not SYSTEM -- the same choice
    # hostmetrics.ps1 documents. SYSTEM has no ssh key to the NUC, so a dump
    # would be produced and then have nowhere to go.
    #
    # A .vbs launcher, because `powershell -WindowStyle Hidden` does NOT stop
    # the console flashing: Task Scheduler starts a console host first and
    # only then does PowerShell read the flag. At 07:00 on a workday that is
    # a black window appearing over whatever you are doing.
    $vbs = Join-Path $PSScriptRoot "db-export-launcher.vbs"
    @"
' Starts the export with no console window. See db-export.ps1 for why this
' file exists rather than -WindowStyle Hidden.
CreateObject("WScript.Shell").Run _
  "powershell.exe -NoProfile -ExecutionPolicy Bypass -File ""$PSScriptRoot\db-export.ps1""", 0, False
"@ | Set-Content -Path $vbs -Encoding ascii

    $action  = New-ScheduledTaskAction -Execute "wscript.exe" -Argument "`"$vbs`""
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Thursday -At 7am
    # WAKE THE MACHINE, and run it late rather than not at all. A backup that
    # only happens when somebody was already at the desk is not a schedule.
    $settings = New-ScheduledTaskSettingsSet -WakeToRun -StartWhenAvailable `
                    -ExecutionTimeLimit (New-TimeSpan -Hours 2)
    Register-ScheduledTask -TaskName "ai-stack database export" -Action $action `
        -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Output "Scheduled: Mondays and Thursdays at 07:00 (wakes the machine)."
    Write-Output "Run once now with:  .\db-export.ps1"
    exit 0
}

Say "=== database export starting ==="
$tmp = Join-Path $env:TEMP "dbexport-$stamp"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null

$done = 0; $failed = 0

# --- postgres ---------------------------------------------------------------
# Found by image rather than by a hardcoded list, so a database added later is
# backed up without anybody remembering to edit this file.
$pg = docker ps --format "{{.Names}}`t{{.Image}}" |
      Where-Object { $_ -match "postgres|pgvect|timescale" } |
      ForEach-Object { ($_ -split "`t")[0] }

foreach ($c in $pg) {
    $out = Join-Path $tmp "$c.sql.gz"
    Say "dumping postgres: $c"
    # pg_dumpall over the container's own socket, gzipped on the way out.
    # POSTGRES_USER rather than a guess at "postgres": several of these were
    # created with their own role name and would refuse the default.
    docker exec $c sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' 2>$null |
        Out-File -FilePath "$out.raw" -Encoding utf8
    if ((Test-Path "$out.raw") -and (Get-Item "$out.raw").Length -gt 2000) {
        # A dump that failed still leaves a file. Size is crude and catches
        # the case that actually happens: an empty one.
        Compress-Archive -Path "$out.raw" -DestinationPath "$out.zip" -Force
        Remove-Item "$out.raw" -Force
        $done++
    } else {
        Say "  ERROR: $c produced nothing usable"
        Remove-Item "$out.raw" -Force -ErrorAction SilentlyContinue
        $failed++
    }
}

# --- sqlite, via the online backup API --------------------------------------
# NOT a file copy. SQLite in WAL mode keeps recent writes in a side file, so
# copying the .db alone loses them and can produce something that will not
# open. The backup API asks SQLite for a consistent snapshot instead.
$sqlite = @(
    @{ c = "casey";     f = "/data/zeeportal.db"; n = "casey-portal" },
    @{ c = "ability"; f = "/data/zeeportal.db"; n = "ability-portal" }
)
foreach ($s in $sqlite) {
    $running = docker ps --format "{{.Names}}" | Where-Object { $_ -eq $s.c }
    if (-not $running) { continue }
    Say "snapshotting sqlite: $($s.c)"
    docker exec $s.c python -c @"
import sqlite3, sys
src = sqlite3.connect('$($s.f)')
dst = sqlite3.connect('/tmp/snap.db')
with dst: src.backup(dst)
dst.close(); src.close()
"@ 2>$null
    docker cp "$($s.c):/tmp/snap.db" (Join-Path $tmp "$($s.n).db") 2>$null
    docker exec $s.c rm -f /tmp/snap.db 2>$null
    if ((Get-Item (Join-Path $tmp "$($s.n).db") -ErrorAction SilentlyContinue).Length -gt 1000) {
        $done++
    } else {
        Say "  ERROR: $($s.c) snapshot is empty"; $failed++
    }
}

# --- send it to the vault ---------------------------------------------------
# ONTO THE OTHER MACHINE, deliberately. A backup of this PC that only exists
# on this PC is not a backup of anything; it is a second copy of a file on a
# disk that fails at the same moment as the first.
Say "copying to the vault"
$sent = $false
# NO `2>$null` ON THESE. In PowerShell 5.1, redirecting a native command's
# stderr wraps each line in an ErrorRecord and sets $? to $false even when
# the exe exited 0 -- so the first version reported "could not reach the
# vault" while ssh was working perfectly. Only $LASTEXITCODE is trustworthy
# for a native command, and it needs an unredirected one to read.
& ssh -o BatchMode=yes -o ConnectTimeout=10 $NucHost "mkdir -p '$Dest/$stamp'"
if ($LASTEXITCODE -eq 0) {
    # EXPLICIT FILE LIST, NOT A WILDCARD. PowerShell does not expand globs
    # for native commands, so `scp "$tmp\*"` passed a literal asterisk and
    # scp looked for a file called "*". Six good dumps went nowhere because
    # of one character.
    $files = Get-ChildItem -File $tmp | ForEach-Object { $_.FullName }
    if ($files) {
        & scp -q -o BatchMode=yes @files "${NucHost}:$Dest/$stamp/"
        if ($LASTEXITCODE -eq 0) { $sent = $true }
    }
}

if ($sent) {
    Say "sent $done dump(s) to $Dest/$stamp"
    # Prune only after a SUCCESSFUL send. Deleting old backups on a night the
    # new one failed is how you end up with none.
    $cutoff = (Get-Date).AddDays(-7 * $KeepWeeks).ToString("yyyy-MM-dd")
    & ssh -o BatchMode=yes $NucHost "cd $Dest 2>/dev/null && for d in 20*; do [ `"`$d`" \< `"$cutoff`" ] && rm -rf `"`$d`"; done; true" 2>$null
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
} else {
    # KEPT LOCALLY when the copy fails, rather than discarded. A dump on the
    # wrong disk is worth more than no dump, and it says where it is.
    Say "WARNING: could not reach the vault -- dumps kept at $tmp"
}

# --- the reference voices, which are not databases and belong here anyway ---
# Two irreplaceable recordings arrived on 2026-09-07: a voicemail from Mom and
# one from Nellie, his grandmother. They were in NO backup at all.
#
# `voices/` is gitignored -- correctly, because a cloned family voice and a
# personal message are not source code and have no business on a git server.
# But the household backup covers kitchen, mom-site, nextcloud-files and
# casey-career, and does not cover this. So the only copy of his grandmother's
# voice was one directory on one disk.
#
# THEY RIDE WITH THE DATABASE EXPORT because this job already runs twice a
# week and already ships to the encrypted vault over ssh. A separate schedule
# for 21MB that changes once a month would be more moving parts for less
# protection. The naming is imprecise and the alternative was a family
# recording with no second copy.
$voices = Join-Path $PSScriptRoot "voices"
if (Test-Path $voices) {
    & ssh -o BatchMode=yes $NucHost "mkdir -p '$Dest/../voices'"
    if ($LASTEXITCODE -eq 0) {
        $vf = Get-ChildItem -File $voices | ForEach-Object { $_.FullName }
        if ($vf) {
            # Explicit file list, not a wildcard -- PowerShell does not expand
            # globs for native commands, which is what silently sent nothing
            # the first time this script tried it.
            & scp -q -o BatchMode=yes @vf "${NucHost}:$Dest/../voices/"
            if ($LASTEXITCODE -eq 0) { Say "voices: $($vf.Count) file(s) copied" }
            else { Say "WARNING: voices did not copy" }
        }
    }
}

Say "=== finished: $done exported, $failed failed ==="
if ($failed -gt 0) { exit 1 }
