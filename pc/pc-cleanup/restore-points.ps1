<#
  restore-points.ps1 -- Jason, 2026-11-04: "make one now and remove any before three [days] ago".
  Run in PowerShell as administrator. With no switch it only LISTS what it would do.
    .\restore-points.ps1          list restore points; mark the ones older than 3 days
    .\restore-points.ps1 -Apply   make a fresh one (if none today), then delete those older than 3 days
  The newest restore point is never deleted, even if it is old. Deleting a restore point cannot be
  undone -- that is why it lists first.
#>
param([switch]$Apply, [int]$Days = 3)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrators')
if (-not $isAdmin) { throw 'Run as administrator.' }
$cut = (Get-Date).AddDays(-$Days)
$rps = @(Get-ComputerRestorePoint | Sort-Object SequenceNumber)
$snaps = @(Get-CimInstance Win32_ShadowCopy | Where-Object VolumeName -eq (Get-CimInstance Win32_Volume -Filter "DriveLetter='C:'").DeviceID)
"Restore points on C: ($($rps.Count)):"
foreach ($r in $rps) {
  $when = [Management.ManagementDateTimeConverter]::ToDateTime($r.CreationTime)
  "  {0}  {1}  {2}" -f $when.ToString('yyyy-MM-dd HH:mm'), $(if ($when -lt $cut) { 'OLD ' } else { 'keep' }), $r.Description
}
"Shadow copies behind them: $($snaps.Count), oldest $(($snaps | Sort-Object InstallDate | Select-Object -First 1).InstallDate)"
if (-not $Apply) { "List only. Run with -Apply to make one now and delete those older than $Days days."; return }

$today = $rps | Where-Object { [Management.ManagementDateTimeConverter]::ToDateTime($_.CreationTime) -gt (Get-Date).Date }
if (-not $today) { Checkpoint-Computer -Description "Manual $(Get-Date -Format yyyy-MM-dd)" -RestorePointType MODIFY_SETTINGS; 'made a new restore point' }
else { "already have one from today ($($today[-1].Description)); not making another" }

$cvol = (Get-CimInstance Win32_Volume -Filter "DriveLetter='C:'").DeviceID   # C: only; other drives' snapshots are left alone
$snaps = @(Get-CimInstance Win32_ShadowCopy | Where-Object VolumeName -eq $cvol | Sort-Object InstallDate)
if (-not $snaps) { 'no snapshots on C:'; return }
$newest = $snaps[-1]
foreach ($s in $snaps) {
  if ($s.InstallDate -lt $cut -and $s.ID -ne $newest.ID) {
    Invoke-CimMethod -InputObject $s -MethodName Delete | Out-Null
    "deleted snapshot from $($s.InstallDate)"
  }
}
"Now: $(@(Get-ComputerRestorePoint).Count) restore point(s)."
