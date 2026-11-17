<#
  compact-docker-disk.ps1 -- give Docker's freed space back to C: (2026-11-11).

  WHY: deleting images frees space INSIDE Docker's disk file (docker_data.vhdx), but the file itself
  never shrinks on its own. On 11-11 the prune freed ~111 GB inside it, and fstrim marked 94 GB of
  blocks free, yet the file stayed 308 GB because it isn't a sparse file (checked with fsutil). Only
  diskpart "compact vdisk" shrinks it, and that needs admin and Docker fully stopped.

  WHAT IT DOES: asks for admin (Windows shows a Yes/No prompt) -> quits Docker Desktop ->
  wsl --shutdown -> diskpart compact -> starts Docker Desktop again.
  The PC-hosted sites are down while it runs (about 10-20 min). Log: C:\Logs\docker-compact.log
#>
param([string]$Vhd = (Join-Path $env:LOCALAPPDATA 'Docker\wsl\disk\docker_data.vhdx'))
$vhd = $Vhd
$log = 'C:\Logs\docker-compact.log'

# Re-run as admin if needed. The path to the disk is passed in, because the admin copy may run as
# a different profile and $env:LOCALAPPDATA would then point somewhere else.
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass',
    '-File', "`"$PSCommandPath`"", '-Vhd', "`"$vhd`""
  exit
}

New-Item -ItemType Directory -Force (Split-Path $log) | Out-Null
function Say($m) { $l = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m; Add-Content $log $l; Write-Host $l }

if (-not (Test-Path $vhd)) { Say "FAIL: no disk at $vhd"; Read-Host 'Press Enter to close'; exit 1 }
$before = (Get-Item $vhd).Length
Say ("start. docker_data.vhdx = {0:N1} GB" -f ($before / 1GB))

Say 'quitting Docker Desktop'
Get-Process 'Docker Desktop', 'com.docker.backend', 'com.docker.build' -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 5
wsl.exe --shutdown
Start-Sleep 5

Say 'compacting (this is the slow part)'
$dp = Join-Path $env:TEMP 'compact-docker.txt'
@"
select vdisk file="$vhd"
attach vdisk readonly
compact vdisk
detach vdisk
"@ | Set-Content $dp -Encoding ascii
diskpart /s $dp | ForEach-Object { Add-Content $log "  $_" }
Remove-Item $dp -ErrorAction SilentlyContinue

$after = (Get-Item $vhd).Length
Say ("done. {0:N1} GB -> {1:N1} GB (freed {2:N1} GB)" -f ($before / 1GB), ($after / 1GB), (($before - $after) / 1GB))

Say 'starting Docker Desktop'
# This PC has a per-user install (AppData\Local\Programs\DockerDesktop, seen on PATH 11-11); the
# Program Files path is the usual all-users install, kept as a fallback.
$dd = @("$env:USERPROFILE\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe",
        "C:\Users\USER\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe",
        (Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe')) | Where-Object { Test-Path $_ } | Select-Object -First 1
# Started via explorer so it runs as you, not as admin.
# Fixed 2026-11-14: this comment used to sit between `else` and its { } block on one line, which
# swallowed the block and made the whole script a parse error (it never ran). Checked with the
# PowerShell parser after the fix: no errors.
if ($dd) { Start-Process explorer.exe "`"$dd`"" } else { Say 'Docker Desktop.exe not found; start it by hand' }
Read-Host 'Press Enter to close'
