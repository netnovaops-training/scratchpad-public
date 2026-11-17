<#
  winget-update.ps1 -- Jason's "update all apps", rewritten 2026-11-04 (his old desktop
  "winget update.ps1" ran `winget upgrade --all` twice and would also upgrade CUDA, Docker, Ollama
  and Python, which can break the AI stack).
    1. makes sure the wanted tools are installed (silent, terms accepted)
    2. PINS the packages that must only be updated on purpose (winget skips pinned ones)
    3. winget upgrade --all  (everything else, silent, terms accepted)
  Log: C:\Logs\winget_upgrade.log (same place as before). Runs weekly from the scheduled task
  "Update all apps (weekly)", and from the desktop shortcut "update all apps".
  To update a pinned one on purpose:  winget upgrade --id Nvidia.CUDA --force
#>
param([switch]$NoPause, [string[]]$IncludeHeld = @())   # -IncludeHeld: update these held ones too (the NUC-led night run)

# Run in Windows PowerShell 5.1, never in PowerShell 7 (2026-11-11). Started from pwsh 7 (a right-click
# "Run with PowerShell" or a pwsh terminal), winget can't swap out the pwsh that is running this script,
# so the PowerShell update was left half-done ("Restart the application to complete the upgrade").
# The weekly task already uses powershell.exe and its log shows PowerShell 7.7.0 installed fine on
# 11-11 04:21, so this only moves hand-started runs onto the same path. The window stays open as before.
if ($PSVersionTable.PSEdition -eq 'Core') {
  $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`"")
  if ($NoPause) { $a += '-NoPause' }
  if ($IncludeHeld) { $a += '-IncludeHeld'; $a += ($IncludeHeld -join ',') }
  Start-Process powershell.exe -ArgumentList $a
  exit
}
$log = 'C:\Logs\winget_upgrade.log'
New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Say($m) { $l = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m; Add-Content $log $l -Encoding utf8; Write-Host $l }
$common = '--accept-package-agreements', '--accept-source-agreements', '--disable-interactivity', '--silent'
Say '===== start'

# 1. wanted tools (Jason, 2026-11-04)
$want = 'Microsoft.PowerShell', 'REALiX.HWiNFO', 'CrystalDewWorld.CrystalDiskInfo', 'voidtools.Everything',
        'Notepad++.Notepad++', 'AntibodySoftware.WizTree'
$have = (winget list --accept-source-agreements --disable-interactivity 2>$null) -join "`n"
foreach ($id in $want) {
  if ($have -match [regex]::Escape($id)) { continue }
  Say "installing $id"
  winget install --id $id -e @common 2>&1 | Select-Object -Last 2 | ForEach-Object { Say "  $_" }
}

# 2. hold these: they change the AI stack or the network, so they are updated by hand, on purpose
$hold = 'Nvidia.CUDA', 'Docker.DockerDesktop', 'Ollama.Ollama', 'Tailscale.Tailscale', 'Python.Python.3.11',
        'Python.Python.3.12', 'Python.Python.3.14', 'MSYS2.MSYS2', 'Rustlang.Rustup', 'ggml.llamacpp'
$pins = (winget pin list --disable-interactivity 2>$null) -join "`n"
foreach ($id in $hold) { if ($pins -notmatch [regex]::Escape($id)) { winget pin add --id $id --blocking --disable-interactivity 2>&1 | Out-Null } }
Say "held back (pinned): $($hold -join ', ')"

# 3. everything else
winget upgrade --all @common 2>&1 | Where-Object { $_ -match '\S' -and $_ -notmatch '^\s*[-\\|/]\s*$|^\s*[\d.]+ ?[KMG]?B|[\u2580-\u259F]' } |
  ForEach-Object { Add-Content $log "  $_" -Encoding utf8; Write-Host "  $_" }
$rc = $LASTEXITCODE
foreach ($id in $IncludeHeld) {   # unpin, update, re-pin: held packages only move during the night maintenance
  Say "updating held package on purpose: $id"
  winget pin remove --id $id --disable-interactivity 2>&1 | Out-Null
  winget upgrade --id $id -e @common 2>&1 | Select-Object -Last 2 | ForEach-Object { Say "  $_" }
  winget pin add --id $id --blocking --disable-interactivity 2>&1 | Out-Null
}
# 4. what is waiting for Jason: held packages that have a newer version (he updates these by hand)
$waiting = winget upgrade --include-pinned --accept-source-agreements --disable-interactivity 2>$null |
  Where-Object { $line = $_; $hold | Where-Object { $line -match [regex]::Escape($_) } }
if ($waiting) { Say 'WAITING FOR YOU (held back; update by hand when ready):'; $waiting | ForEach-Object { Say "  $($_.Trim())" } }
else { Say 'nothing held back is waiting' }
Say "===== done (exit $rc)"
if (-not $NoPause -and [Environment]::UserInteractive -and $Host.Name -eq 'ConsoleHost') { Read-Host 'Press Enter to close' }
