<#
pc_health_admin_setup.ps1 - the ONE admin step for fan and GPU metrics in Grafana (2026-11-17).

Run once, in PowerShell opened with "Run as administrator":
    powershell -ExecutionPolicy Bypass -File D:\scratchpad\tools\pc_health_admin_setup.ps1
What it does (nothing else):
  1. Lets your Windows account write to windows_exporter's textfile folder, so the every-minute logger
     (task "PC health log") can drop pc_health.prom there.
  2. Turns on windows_exporter's textfile collector (its config.yaml was empty, so only the defaults ran),
     keeping the same 8 collectors that were on, and restarts the service.
  3. Installs LibreHardwareMonitor from winget (free, open source) to read the AIO pump, radiator and case fans.
Then, in LibreHardwareMonitor: Options > Run On Windows Startup, Start Minimized, Remote Web Server > Run
(it listens on this PC, port 8085). The logger picks the fans up within a minute.
Check: http://127.0.0.1:9182/metrics should contain `pc_health_value`; Grafana > Explore > pc_health_value.
#>
$ErrorActionPreference = 'Stop'
$dir = 'C:\Program Files\windows_exporter'
$user = "$env:USERDOMAIN\$env:USERNAME"

icacls "$dir\textfile_inputs" /grant "${user}:(OI)(CI)M" | Out-Null
"1. $user can write to $dir\textfile_inputs"

Copy-Item "$dir\config.yaml" "$dir\config.yaml.bak-20261117" -Force
@"
collectors:
  enabled: cpu,logical_disk,memory,net,os,physical_disk,service,system,textfile
"@ | Set-Content "$dir\config.yaml" -Encoding ascii
Restart-Service windows_exporter
Start-Sleep 3
$ok = (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:9182/metrics -TimeoutSec 10).Content -match 'collector="textfile"'
"2. textfile collector on: $ok (backup of the old config: config.yaml.bak-20261117)"
if (-not $ok) { Copy-Item "$dir\config.yaml.bak-20261117" "$dir\config.yaml" -Force; Restart-Service windows_exporter; "   rolled back to the old config" }

winget install --id LibreHardwareMonitor.LibreHardwareMonitor --source winget --accept-package-agreements --accept-source-agreements
"3. LibreHardwareMonitor installed: start it as administrator and turn on Remote Web Server."
