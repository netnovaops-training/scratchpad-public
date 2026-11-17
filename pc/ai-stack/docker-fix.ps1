# docker-fix.ps1 -- what the `fix` command runs (Jason, 2026-11-04: "type fix over SSH and it
# does exactly what the Docker script on my desktop does, elevated, and reboots if it has to").
#
# Runs INSIDE Jason's desktop session, started by the scheduled task "Docker Fix" (install-fix.ps1
# makes it; `fix` starts it). It has to be the desktop session: Docker Desktop started from an SSH
# session would run invisibly in the wrong session and never come up properly.
#
#   1. restart-docker.ps1 (end Docker, wsl --shutdown, start Docker, wait up to 5 minutes)
#   2. if the engine still isn't back -- e.g. the stuck dockerInference socket file
#      ("The file cannot be accessed by the system"), which only a reboot clears (seen
#      2026-10 and 2026-11-04) -- reboot the PC with a 2-minute warning on screen.
#      Cancel that from any prompt with:  shutdown /a
#
# Log: ai-stack\logs\docker-fix.log  (fix.cmd shows it live)
param([switch]$NoReboot)
$log = Join-Path $PSScriptRoot 'logs\docker-fix.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Say($m) { $l = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m; Add-Content $log $l -Encoding utf8; Write-Host $l }

Say 'fix: start'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'restart-docker.ps1') *>&1 |
    ForEach-Object { Say "  $_" }
$ok = $false
try { $v = docker info --format '{{.ServerVersion}}' 2>$null; $ok = ($LASTEXITCODE -eq 0 -and "$v" -match '^\d') } catch {}

if ($ok) { Say "fix: DONE - Docker is back (engine $v)"; exit 0 }
if ($NoReboot) { Say 'fix: DONE - Docker did NOT come back; -NoReboot given, so no reboot'; exit 1 }
Say 'fix: Docker did not come back. Rebooting the PC in 2 minutes (cancel: shutdown /a)'
shutdown /r /t 120 /c "fix: Docker would not restart; rebooting in 2 minutes. Cancel with: shutdown /a"
Say 'fix: DONE - reboot scheduled. Sign in after it restarts so Docker Desktop starts.'
exit 2
