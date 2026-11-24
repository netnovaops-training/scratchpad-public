<#
pc_noise_logger_setup.ps1 - ONE TIME, as admin: install the 5-second noise logger as a SYSTEM task (2026-11-23).

    Right-click PowerShell > Run as administrator, then:
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\pc_noise_logger_setup.ps1

Does: copies LibreHardwareMonitorLib and its DLLs from the winget folder to C:\ProgramData\pc-noise\lhm (a winget
update moves that folder; SYSTEM then still finds a fixed copy), registers task "PC noise logger" (SYSTEM, at
startup, restart every minute up to 999 times if it stops, no time limit), and starts it now. Prints the first
logged row after 15 s. Undo: Unregister-ScheduledTask -TaskName 'PC noise logger' -Confirm:$false
Motherboard fan speeds need admin/SYSTEM (the Super I/O chip); that is why this is a SYSTEM task.
#>
$ErrorActionPreference = 'Stop'
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Run this in an ADMIN PowerShell (right-click > Run as administrator).'; exit 1
}
$src = (Get-ChildItem "C:\Users\USER\AppData\Local\Microsoft\WinGet\Packages" -Filter LibreHardwareMonitorLib.dll -Recurse | Select-Object -First 1).DirectoryName
$dst = 'C:\ProgramData\pc-noise\lhm'
New-Item -ItemType Directory -Force -Path $dst | Out-Null
Copy-Item "$src\*.dll" $dst -Force
$act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File D:\scratchpad\tools\pc_noise_logger.ps1'
$trg = New-ScheduledTaskTrigger -AtStartup
$set = New-ScheduledTaskSettingsSet -RestartInterval (New-TimeSpan -Minutes 1) -RestartCount 999 -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'PC noise logger' -Action $act -Trigger $trg -Settings $set -User 'SYSTEM' -RunLevel Highest -Force | Out-Null
Start-ScheduledTask -TaskName 'PC noise logger'
Start-Sleep -Seconds 15
$f = Get-ChildItem D:\pc-health -Filter 'noise-*.csv' | Sort-Object LastWriteTime | Select-Object -Last 1
if ($f) { Get-Content $f.FullName -Tail 2 } else { 'No row yet: see D:\pc-health\noise-errors.log' }
