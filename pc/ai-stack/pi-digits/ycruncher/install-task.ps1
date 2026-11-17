# install-task.ps1 -- RUN ONCE AS ADMINISTRATOR. Registers the scheduled task "Pi y-cruncher" that runs
# ycrun.ps1 (in this folder) with highest privileges, so y-cruncher's swap mode can use your drives.
# It has no schedule of its own: it only runs when started (schtasks /run /tn "Pi y-cruncher").
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$me = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$act = New-ScheduledTaskAction -Execute 'powershell.exe' `
       -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$here\ycrun.ps1`"" -WorkingDirectory $here
$pr  = New-ScheduledTaskPrincipal -UserId $me -LogonType Interactive -RunLevel Highest
# No time limit (a big run takes days), don't stop on battery, don't start a second copy.
$set = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries `
       -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Pi y-cruncher' -Action $act -Principal $pr -Settings $set -Force | Out-Null
Write-Host "Registered 'Pi y-cruncher' for $me (runs $here\ycrun.ps1 elevated)." -ForegroundColor Green
