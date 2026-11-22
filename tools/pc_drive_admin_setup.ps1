<#
pc_drive_admin_setup.ps1 - the ONE admin step for drive SMART logging and fan speeds in Grafana (T0759, 2026-11-21).

Run once, in PowerShell opened with "Run as administrator":
    powershell -ExecutionPolicy Bypass -File D:\scratchpad\tools\pc_drive_admin_setup.ps1
What it does (nothing else):
  1. Registers the task "PC drive health": pc_drive_health.ps1 every 5 minutes as SYSTEM (SMART needs admin),
     hidden, no window. It only READS drive counters.
  2. Adds a Windows Firewall rule that BLOCKS inbound TCP 8085 from the network. LibreHardwareMonitor's web
     server (needed for fan RPM) listens on every network card; the house rule is no new open ports. Programs
     on this PC still reach it (Windows does not filter loopback), so pc_health_log.ps1 keeps working.
  3. Runs the logger once and shows the first rows.
Then, one click in LibreHardwareMonitor: Options > Remote Web Server > Run (port 8085). If Windows asks about
the firewall, choose Cancel / do not allow. Within a minute pc_health_log.ps1 starts logging fan RPM.
Check: D:\pc-health\drives-<today>.csv has rows with load_unload_cycles; Grafana > Explore > pc_drive_value.
Undo: Unregister-ScheduledTask "PC drive health" -Confirm:$false; Remove-NetFirewallRule -DisplayName "Block LHM 8085 inbound"
Variables: $script (the logger), $action / $trigger / $principal / $settings (task parts).
#>
$ErrorActionPreference = 'Stop'
$script = 'D:\scratchpad\tools\pc_drive_health.ps1'

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 2) -StartWhenAvailable
Register-ScheduledTask -TaskName 'PC drive health' -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
"1. task 'PC drive health' registered (every 5 minutes, SYSTEM)"

if (-not (Get-NetFirewallRule -DisplayName 'Block LHM 8085 inbound' -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName 'Block LHM 8085 inbound' -Direction Inbound -Protocol TCP -LocalPort 8085 -Action Block | Out-Null
}
"2. firewall: inbound TCP 8085 blocked from the network (this PC can still use it)"

& powershell -NoProfile -ExecutionPolicy Bypass -File $script
$f = Join-Path 'D:\pc-health' ("drives-" + (Get-Date -Format 'yyyy-MM-dd') + '.csv')
"3. first rows:"
Import-Csv $f | Select-Object -Last 4 | Format-Table disk, model, temp_c, load_unload_cycles, start_stop_cycles, note
"Now: LibreHardwareMonitor > Options > Remote Web Server > Run (and do not allow it through the firewall)."
