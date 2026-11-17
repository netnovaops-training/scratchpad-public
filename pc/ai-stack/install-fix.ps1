# install-fix.ps1 -- one-time setup for the `fix` command. Run in PowerShell as administrator:
#   powershell -ExecutionPolicy Bypass -File "C:\Jasons New Site\www\ai-stack\install-fix.ps1"
# (finish-setup.ps1 also runs it, as SYSTEM, passing -User and -HomeDir.)
#
# 1. Registers the scheduled task "Docker Fix": runs docker-fix.ps1 as you, with highest
#    privileges, in your desktop session. No schedule -- it only runs when `fix` starts it.
# 2. Copies fix.cmd into <profile>\.local\bin (already on your PATH), so `fix` works in
#    cmd, PowerShell and over SSH.
# Undo: Unregister-ScheduledTask -TaskName "Docker Fix" -Confirm:$false; del ~\.local\bin\fix.cmd
param([string]$User = "$env:USERDOMAIN\$env:USERNAME", [string]$HomeDir = $env:USERPROFILE)
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$here\docker-fix.ps1`""
$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName 'Docker Fix' -Action $action -Principal $principal -Settings $settings `
  -Description 'Restart Docker Desktop; reboot if it will not come back. Started by the fix command.' -Force | Out-Null
$bin = Join-Path $HomeDir '.local\bin'
New-Item -ItemType Directory -Force $bin | Out-Null
Copy-Item (Join-Path $here 'fix.cmd') (Join-Path $bin 'fix.cmd') -Force
"Installed for $User. Type  fix  in any prompt (or over SSH) to restart Docker."
