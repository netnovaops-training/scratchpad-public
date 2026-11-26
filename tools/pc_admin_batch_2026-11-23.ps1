<#
pc_admin_batch_2026-11-23.ps1 - the PC steps from 2026-11-23 that need an ADMIN PowerShell, in one run.

    Right-click PowerShell > Run as administrator, then:
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\pc_admin_batch_2026-11-23.ps1

1. T0122: disable (not delete) the spare administrator account USER_0lyp1qt (never signs in). Undo: Enable-LocalUser.
2. T0131: disable the old task "Claude RC now" (10-31, starts `claude remote-control` from an old clone). The
   current task "Claude Remote Control" (scratchpad\tools\remote-control-server.ps1) is left as it is.
3. Noise logger: runs pc_noise_logger_setup.ps1 (SYSTEM task, 5-second fan/temperature/HDD log).
Each step prints OK or FAIL; one failing does not stop the others. Task XML is exported to D:\archive first.
#>
$ErrorActionPreference = 'Continue'
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Run this in an ADMIN PowerShell (right-click > Run as administrator).'; exit 1
}
$A = 'D:\archive\2026-11-23\pc-cleanup'; New-Item -ItemType Directory -Force $A | Out-Null

try {
    $u = Get-LocalUser -Name 'USER_0lyp1qt' -ErrorAction Stop
    if ($u.Enabled) { Disable-LocalUser -Name 'USER_0lyp1qt'; 'OK   T0122 spare admin USER_0lyp1qt disabled' } else { 'OK   T0122 spare admin already disabled' }
} catch { "FAIL T0122: $($_.Exception.Message)" }

try {
    Export-ScheduledTask -TaskName 'Claude RC now' | Out-File -Encoding utf8 "$A\Claude_RC_now.xml"
    Disable-ScheduledTask -TaskName 'Claude RC now' -ErrorAction Stop | Out-Null
    'OK   T0131 task "Claude RC now" disabled (XML in ' + $A + ')'
} catch { "FAIL T0131: $($_.Exception.Message)" }

try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\pc_noise_logger_setup.ps1
    'OK   noise logger set up (see the row above)'
} catch { "FAIL noise logger: $($_.Exception.Message)" }
