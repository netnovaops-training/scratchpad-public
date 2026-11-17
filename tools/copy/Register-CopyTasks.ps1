<#
.SYNOPSIS
  Register-CopyTasks.ps1 -- creates the scheduled task "NAS backup (verified)" that runs
  Backup-ToNas.ps1 every night at 3:15 AM. 2026-11-14, Claude. JASON RUNS THIS (admin PowerShell);
  Claude did not run it.

.DESCRIPTION
  Default: runs whether or not you are signed in. Windows asks for YOUR Windows password once
  (Get-Credential window) and stores it inside Task Scheduler; that is what lets the job reach the
  NAS while you are signed out (an "S4U" task without a password gets no network access).
  -OnlyWhenLoggedOn: no password asked; the job runs only if you are signed in at 3:15.
  3:15 AM was picked to stay clear of: household backup 2:30, DocsToPaperless 3:40 (it may overlap
  that one on a big night; both only read the source folders, so they do not conflict).
  Also starts the job if the PC was off at 3:15 (StartWhenAvailable). Remove: -Remove.
#>
param([switch]$OnlyWhenLoggedOn, [switch]$Remove)
$name = 'NAS backup (verified)'
if ($Remove) { Unregister-ScheduledTask -TaskName $name -Confirm:$false; Write-Output "Removed $name"; return }
$script = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'Backup-ToNas.ps1'
$pwsh = (Get-Command pwsh -ErrorAction SilentlyContinue).Source
if (-not $pwsh) { $pwsh = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" }
$action = New-ScheduledTaskAction -Execute $pwsh -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At 3:15AM
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 10) -MultipleInstances IgnoreNew
$user = "$env:USERDOMAIN\$env:USERNAME"
if ($OnlyWhenLoggedOn) {
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -User $user -RunLevel Limited -Force | Out-Null
} else {
  $cred = Get-Credential -UserName $user -Message 'Your Windows password, so the nightly NAS backup can run while you are signed out'
  Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -User $user `
    -Password $cred.GetNetworkCredential().Password -RunLevel Limited -Force | Out-Null
  $cred = $null
}
Get-ScheduledTask -TaskName $name | Select-Object TaskName, State
Write-Output "Try it now:  Start-ScheduledTask -TaskName '$name'   then look at D:\joblog\events.jsonl"
