# schedule-finish-setup.ps1 -- run ONCE, as administrator, right before you reboot:
#   powershell -ExecutionPolicy Bypass -File "C:\Jasons New Site\www\pc-cleanup\schedule-finish-setup.ps1"
# Registers "Finish setup (one time)": at the next boot, wait 15 minutes (Windows Update finishes),
# then run finish-setup.ps1 as SYSTEM -- no sign-in needed. The job deletes this task when done.
# Watch it over SSH:  type "C:\Jasons New Site\www\pc-cleanup\finish-setup.log"
# Cancel before rebooting:  Unregister-ScheduledTask -TaskName "Finish setup (one time)" -Confirm:$false
$script = 'C:\Jasons New Site\www\pc-cleanup\finish-setup.ps1'
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$t = New-ScheduledTaskTrigger -AtStartup; $t.Delay = 'PT15M'
$p = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 2) -StartWhenAvailable
Register-ScheduledTask -TaskName 'Finish setup (one time)' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
'Scheduled. Reboot now; the setup starts 15 minutes after Windows is back.'
