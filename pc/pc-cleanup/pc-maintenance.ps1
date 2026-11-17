<#
  pc-maintenance.ps1 -- the PC's half of the night maintenance (Jason, 2026-11-04: "during the 3 am
  NUC reboot ... only after the NUC recovers ... perform the remaining updates to NVIDIA and Docker,
  then reboot, and email before and after, but the NUC leads").
  Started ONLY by the NUC (task "PC maintenance (NUC-led)", no schedule of its own), after the NUC's
  own planned reboot came back healthy. The NUC sends the emails and checks the PC afterwards.
    1. winget-update.ps1 -IncludeHeld   everything, including the held Docker Desktop + CUDA
    2. Windows Update                   install everything already downloaded/available (drivers too)
    3. reboot in 2 minutes              (Autologon brings the desktop and Docker back)
  NVIDIA's graphics DRIVER is not scriptable reliably; Windows Update may carry one, otherwise the
  NVIDIA app. Progress: C:\Logs\pc-maint-status.json + C:\Logs\pc-maint.log
#>
$log = 'C:\Logs\pc-maint.log'; $state = 'C:\Logs\pc-maint-status.json'
New-Item -ItemType Directory -Force C:\Logs | Out-Null
function Stage($s, $note = '') {
  @{ stage = $s; note = $note; at = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') } | ConvertTo-Json -Compress | Set-Content $state -Encoding utf8
  Add-Content $log ("{0}  [{1}] {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $s, $note) -Encoding utf8
}
Stage 'started'

Stage 'apps' 'winget, held packages included tonight'
& (Join-Path $PSScriptRoot 'winget-update.ps1') -NoPause -IncludeHeld 'Docker.DockerDesktop', 'Nvidia.CUDA' *>&1 | Out-Null
Stage 'apps-done' ((Get-Content 'C:\Logs\winget_upgrade.log' -Tail 6) -join ' / ')

Stage 'windows-update'
try {
  $ses = New-Object -ComObject Microsoft.Update.Session
  $found = $ses.CreateUpdateSearcher().Search('IsInstalled=0 and IsHidden=0').Updates
  if ($found.Count -gt 0) {
    $coll = New-Object -ComObject Microsoft.Update.UpdateColl
    foreach ($u in $found) { if (-not $u.EulaAccepted) { $u.AcceptEula() }; [void]$coll.Add($u) }
    $dl = $ses.CreateUpdateDownloader(); $dl.Updates = $coll; [void]$dl.Download()
    $in = $ses.CreateUpdateInstaller(); $in.Updates = $coll; $r = $in.Install()
    Stage 'windows-update-done' ("{0} update(s), result code {1}: {2}" -f $found.Count, $r.ResultCode, (($found | ForEach-Object Title) -join '; '))
  } else { Stage 'windows-update-done' 'nothing to install' }
} catch { Stage 'windows-update-failed' $_.Exception.Message }

Stage 'rebooting' 'in 2 minutes'
shutdown /r /t 120 /c "Night maintenance (NUC-led): updates installed, restarting."
