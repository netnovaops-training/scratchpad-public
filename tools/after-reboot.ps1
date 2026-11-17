# after-reboot.ps1: bring the /backlog feed and page back after a restart, then log the result.
#
# Jason, 2026-11-13: "run a reboot script ... can resume after startup". Registered ONCE in
# HKCU\Software\Microsoft\Windows\CurrentVersion\RunOnce (Windows deletes the entry after it runs), so this is not a
# permanent startup item. Run it by hand any time:  powershell -ExecutionPolicy Bypass -File D:\scratchpad\tools\after-reboot.ps1
#
# What it does: waits up to 10 minutes for Docker Desktop, starts the backlog-sync feed (it has no restart policy, so a
# reboot stops it), tries to rebuild/start the /backlog page, and writes one line per step to D:\joblog\after-reboot.log.
# Nothing here calls a paid API or writes to a database.
# Status 2026-11-13: written before the reboot; NOT yet seen running after one (check the log).

$log = 'D:\joblog\after-reboot.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Say($m) { "{0:yyyy-MM-dd HH:mm:ss} {1}" -f (Get-Date), $m | Add-Content $log }

Say 'START after-reboot'
$dash = 'D:\Garden for all\_local-data\pipeline\dash-rust'
$bash = 'C:\Program Files\Git\bin\bash.exe'

$ok = $false
for ($i = 0; $i -lt 60; $i++) {
    docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Start-Sleep -Seconds 10
}
if (-not $ok) { Say 'FAIL Docker did not answer within 10 minutes; start Docker Desktop, then run this script again'; exit 1 }
Say 'PASS Docker is up'

Push-Location $dash
try {
    $env:RUNS = 'run-current,run-full'
    $r = & $bash -lc 'sh backlog.sh sync-up' 2>&1 | Out-String
    Say ("feed: " + $r.Trim())
    $r = & $bash -lc 'sh backlog.sh up' 2>&1 | Select-Object -Last 3 | Out-String
    Say ("page: " + $r.Trim())
} finally { Pop-Location }
Say 'END after-reboot'
