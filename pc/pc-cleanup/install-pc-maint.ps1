<#
  install-pc-maint.ps1 -- one-time, elevated (2026-11-04). Sets up the PC side of the NUC-led night maintenance.
   1. Scheduled task "PC maintenance (NUC-led)": runs pc-maintenance.ps1 as Jason, elevated, in his session.
      No schedule of its own; only the NUC starts it.
   2. The NUC's SSH key, RESTRICTED: accepted only from the NUC's Tailscale address (100.64.0.11), and it can
      only run pc-maint-remote.ps1 (start / status / health). No shell, no tunnels, no other command.
      Added to C:\ProgramData\ssh\administrators_authorized_keys (backup made first; file kept admin+SYSTEM only).
  Undo: delete the line ending "nuc-pc-maint" from that file; Unregister-ScheduledTask "PC maintenance (NUC-led)".
#>
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$here\pc-maintenance.ps1`""
$p = New-ScheduledTaskPrincipal -UserId 'SUPERNOVA\USER' -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 3) -AllowStartIfOnBatteries
Register-ScheduledTask -TaskName 'PC maintenance (NUC-led)' -Action $a -Principal $p -Settings $s -Force | Out-Null
"task: $((Get-ScheduledTask -TaskName 'PC maintenance (NUC-led)').State)"

$ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
$key = (Get-Content (Join-Path $here 'nuc-key.pub') -Raw).Trim() -split '\s+'
$opts = 'from="100.64.0.11",command="powershell -NoProfile -ExecutionPolicy Bypass -File \"' + "$here\pc-maint-remote.ps1" + '\"",no-port-forwarding,no-agent-forwarding,no-X11-forwarding,no-pty'
$line = "$opts $($key[0]) $($key[1]) nuc-pc-maint"
if (Test-Path $ak) { Copy-Item $ak "$ak.bak-20261104" -Force }
$old = if (Test-Path $ak) { @(Get-Content $ak | Where-Object { $_ -notmatch 'nuc-pc-maint$' }) } else { @() }
Set-Content $ak -Value ($old + $line) -Encoding ascii
icacls $ak /inheritance:r /grant 'Administrators:F' /grant 'SYSTEM:F' | Out-Null
"key added (restricted to start/status/health from 100.64.0.11)"
Read-Host 'Done. Press Enter to close'
