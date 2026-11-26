<#
hwmon_pc_admin_setup.ps1 - the ONE admin step for PC drive SMART monitoring (2026-11-24).

Run once, in PowerShell opened with "Run as administrator":
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\hwmon\hwmon_pc_admin_setup.ps1
What it does (nothing else):
  1. Installs smartmontools 7.5 (pinned) with winget, unless that exact version is already there.
  2. Registers task "PC hardware SMART": hw_pc_smart.ps1 every minute as SYSTEM (SMART needs admin), hidden.
     It only READS drives: no self-test, no setting changed. Hard drives are read every 10 minutes only.
  3. Runs it once and prints what it read: temperature, reallocated / pending / uncorrectable sectors, head
     parks (load_unload), NVMe media errors and wear, per drive.
No new port: the numbers go to windows_exporter's textfile folder, which the NUC's Prometheus already scrapes.
Fans and CPU/GPU temperatures already come from the "PC noise logger" task (pc_noise_logger_setup.ps1).
Undo: Unregister-ScheduledTask -TaskName 'PC hardware SMART' -Confirm:$false; winget uninstall smartmontools.smartmontools
Variables: $exe (smartctl), $script (the collector), $act / $trg / $pri / $set (task parts), $prom (output file).
#>
$ErrorActionPreference = 'Stop'
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Run this in an ADMIN PowerShell (right-click > Run as administrator).'; exit 1
}
$exe = 'C:\Program Files\smartmontools\bin\smartctl.exe'
# Admin-only copy (audit 2026-11-24 High #1): the D:\ copy is writable by Authenticated Users and this task runs as
# SYSTEM. hwmon_pc_fix_task_path.ps1 publishes the D:\ copy here with a SYSTEM/Administrators-only write ACL.
$script = 'C:\ProgramData\hwmon\hw_pc_smart.ps1'

$have = if (Test-Path $exe) { (& $exe -V | Select-Object -First 1) } else { '' }
if ($have -notmatch 'smartctl 7\.5 ') {
    winget install --id smartmontools.smartmontools --version 7.5 --exact --scope machine --silent --accept-package-agreements --accept-source-agreements
    if (-not (Test-Path $exe)) { Write-Host "smartctl not found at $exe after the install; stopping."; exit 1 }
}
"1. $(& $exe -V | Select-Object -First 1)"

$act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trg = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$pri = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'PC hardware SMART' -Action $act -Trigger $trg -Principal $pri -Settings $set -Force | Out-Null
"2. task 'PC hardware SMART' registered (every minute, SYSTEM)"

& powershell -NoProfile -ExecutionPolicy Bypass -File $script
$prom = 'C:\Program Files\windows_exporter\textfile_inputs\hw_smart.prom'
"3. first read:"
Get-Content $prom | Where-Object { $_ -match 'field="(temp_c|reallocated|pending|uncorrectable|load_unload|media_errors|percentage_used|passed)"' } |
    ForEach-Object { if ($_ -match 'drive="([^"]+)".*field="([^"]+)"\} (\S+)') { '   {0,-6} {1,-16} {2}' -f $Matches[1], $Matches[2], $Matches[3] } }
if (Test-Path 'D:\pc-health\smart-errors.log') { "errors (if any):"; Get-Content 'D:\pc-health\smart-errors.log' -Tail 5 }
