<#
hwmon_pc_fix_task_path.ps1 - move the SYSTEM task's script to an admin-only folder (audit 2026-11-24, High #2).

    Admin PowerShell:
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\hwmon\hwmon_pc_fix_task_path.ps1

Why: hwmon_pc_admin_setup.ps1 (run by Jason 11-24) registered task "PC hardware SMART" running
D:\scratchpad\tools\hwmon\hw_pc_smart.ps1 every minute as SYSTEM. That file is writable by Authenticated Users
(icacls, audit 11-24), so anything running as any user could edit it and get SYSTEM.
Fix: copy the script to C:\ProgramData\hwmon\ with an ACL of SYSTEM + Administrators full control and Users read
only (inheritance removed), then re-register the task to run that copy. Future edits: change the D:\ copy, then
re-run this script as admin to publish it. Undo: re-run hwmon_pc_admin_setup.ps1 (old path) or delete the task.
#>
$ErrorActionPreference = 'Stop'
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Run this in an admin PowerShell.' }
$src = 'D:\scratchpad\tools\hwmon\hw_pc_smart.ps1'
$dir = 'C:\ProgramData\hwmon'
$dst = Join-Path $dir 'hw_pc_smart.ps1'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item -LiteralPath $src -Destination $dst -Force
foreach ($p in @($dir, $dst)) {
    # /inheritance:r drops inherited entries; SID names avoid localisation issues.
    icacls $p /inheritance:r /grant:r '*S-1-5-18:(OI)(CI)F' '*S-1-5-32-544:(OI)(CI)F' '*S-1-5-32-545:(OI)(CI)RX' | Out-Null
}
$t = Get-ScheduledTask -TaskName 'PC hardware SMART' -ErrorAction Stop
$act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dst`""
Set-ScheduledTask -TaskName 'PC hardware SMART' -Action $act | Out-Null
$t = Get-ScheduledTask -TaskName 'PC hardware SMART'
"Task now runs: " + $t.Actions[0].Arguments
"ACL of the script:"; icacls $dst
Start-ScheduledTask -TaskName 'PC hardware SMART'; Start-Sleep 20
$prom = 'C:\Program Files\windows_exporter\textfile_inputs\hw_smart.prom'
if (Test-Path $prom) { "hw_smart.prom updated: " + (Get-Item $prom).LastWriteTime } else { "hw_smart.prom not found" }
