<#
  round2.ps1 -- one elevated run (2026-11-04). Log: pc-cleanup\round2.log
   1. winget-update.ps1: install the 5 tools, pin the AI-stack packages, update everything else
   2. scheduled task "Update all apps (weekly)": Sundays 04:30, runs winget-update.ps1 as Jason,
      elevated (so installers don't stop for a Yes); fixes the desktop shortcut "update all apps"
      (it pointed at a placeholder path) to the same script
   3. ONE delete folder: everything marked for deletion today is moved into Downloads\_To-delete
      (Jason sorts and empties it). The OneDrive screenshots leave OneDrive this way, so their cloud
      copies go to OneDrive's recycle bin.
#>
. (Join-Path $PSScriptRoot 'autoclick.ps1'); Disable-QuickEdit
$log = Join-Path $PSScriptRoot 'round2.log'
Start-Transcript -Path $log -Force | Out-Null

"=== 1. install + update"
& (Join-Path $PSScriptRoot 'winget-update.ps1') -NoPause

"=== 2. weekly task + desktop shortcut"
$script = Join-Path $PSScriptRoot 'winget-update.ps1'
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`" -NoPause"
$t = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '4:30AM'
$p = New-ScheduledTaskPrincipal -UserId 'SUPERNOVA\USER' -LogonType Interactive -RunLevel Highest
$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName 'Update all apps (weekly)' -Action $a -Trigger $t -Principal $p -Settings $s -Force | Out-Null
"task registered: $((Get-ScheduledTask -TaskName 'Update all apps (weekly)').State)"
$desk = 'C:\Users\USER\OneDrive - Empire State University\Desktop'
$lnk = (New-Object -ComObject WScript.Shell).CreateShortcut("$desk\update all apps.lnk")
$lnk.TargetPath = 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
$lnk.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$lnk.Save(); "desktop shortcut now runs $script"

"=== 3. one delete folder"
$bin = 'C:\Users\USER\Downloads\_To-delete'
New-Item -ItemType Directory -Force $bin | Out-Null
$moves = [ordered]@{
  'C:\_To-delete-2026-11-04'                                                    = 'from-C-root (shader caches, EA)'
  'C:\_To-delete-uninstall-leftovers-2026-11-04'                                = 'uninstall-leftovers'
  'G:\_To-delete-2026-11-04'                                                    = 'from-G (Steam library, Wallpaper Engine)'
  'C:\Jasons New Site\www\_To-delete-2026-11-04'                                = 'from-www (old notes, Garden backup)'
  'C:\Users\USER\.lmstudio\_To-delete'                                         = 'lmstudio-models (LFM2-24B, Gemma-3-4B)'
  'C:\Users\USER\OneDrive - Empire State University\Pictures\_To-delete-screenshots' = 'screenshots'
  "$desk\Games"                                                                 = 'desktop-Games-folder'
  "$desk\PC Building Simulator.url"                                             = 'desktop-PC Building Simulator.url'
  "$desk\Samsung_Magician_Installer_Official_8.3.2.850.exe"                     = 'desktop-Samsung_Magician_Installer.exe'
  "$desk\winget update.ps1"                                                     = 'desktop-old winget update.ps1'
}
foreach ($src in $moves.Keys) {
  if (-not (Test-Path -LiteralPath $src)) { "  (not there) $src"; continue }
  $dest = Join-Path $bin $moves[$src]
  try {
    if ([IO.Path]::GetPathRoot($src) -ne [IO.Path]::GetPathRoot($dest) -and (Test-Path -LiteralPath $src -PathType Container)) {
      robocopy $src $dest /E /MOVE /R:1 /W:1 /NFL /NDL /NJH /NJS /NP | Out-Null   # Move-Item cannot move a folder to another drive
      if (Test-Path -LiteralPath $src) { throw "robocopy left $src behind (exit $LASTEXITCODE)" }
    } else { Move-Item -LiteralPath $src -Destination $dest -ErrorAction Stop }
    "  moved $src -> $dest" }
  catch { "  COULD NOT move $src : $($_.Exception.Message)" }
}
$m = Get-ChildItem $bin -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum
"Downloads\_To-delete now: {0:N0} files, {1:N1} GB" -f $m.Count, ($m.Sum / 1GB)
"=== round2 done"
Stop-Transcript | Out-Null
Read-Host 'Done. Press Enter to close'
