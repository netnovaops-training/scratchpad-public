<#
  verify-uninstalls.ps1 -- run AFTER finish-setup.ps1, as administrator (Jason, 2026-11-04: "log
  what is uninstalled, then follow up on the folder and verify the folder location and files are
  removed").

  For each program on the uninstall list:
    REMOVED     no longer registered, and no leftover folder found
    LEFTOVERS   uninstalled, but folders remain -> MOVED to C:\_To-delete-uninstall-leftovers-2026-11-04
                (moved, never deleted: Jason checks that folder and deletes it himself)
    STILL THERE still registered: the silent uninstall failed; remove it in Settings > Apps
  Report: pc-cleanup\verify-uninstalls.log. Moves are logged "from -> to" so any can be put back.

  Folder names are matched exactly or with a tight wildcard, and the keep-list below is checked
  first, so Kontakt 7, MySQL Workbench 8.0 CE, Guitar Rig 7, CUDA and your LM Studio MODELS
  (C:\Users\USER\.lmstudio) are never moved.
#>
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $here 'verify-uninstalls.log'
$bin = 'C:\_To-delete-uninstall-leftovers-2026-11-04'
function Say($m) { Add-Content $log $m -Encoding utf8; Write-Host $m }
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrators')
if (-not $isAdmin) { throw 'Run this from PowerShell as administrator (Program Files folders need it).' }
if (Get-CimInstance Win32_Process -Filter "Name='msiexec.exe'" | Where-Object { $_.CommandLine -match '/x ' }) {
  throw 'An uninstall is still running (finish-setup). Wait until finish-setup.log says DONE.'
}
Say "=== verify-uninstalls $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"

# program -> (registry DisplayName regex, leftover folder candidates)
$PF = $env:ProgramFiles; $PF86 = ${env:ProgramFiles(x86)}; $PD = $env:ProgramData
$LA = 'C:\Users\USER\AppData\Local'; $RA = 'C:\Users\USER\AppData\Roaming'
$targets = [ordered]@{
  'TeamViewer'                 = @('^TeamViewer', @("$PF\TeamViewer", "$PF86\TeamViewer", "$RA\TeamViewer"))
  'Intel Driver & Support'     = @('^Intel. Driver & Support Assistant', @("$PF86\Intel\Driver and Support Assistant", "$PD\Intel\DSA"))
  'Intel Computing Improvement'= @('^Intel\(R\) Computing Improvement Program', @("$PF\Intel\SUR", "$PD\Intel\SUR"))
  'Player Location Check'      = @('^Player Location Check$', @("$PF86\GeoComply", "$PF\GeoComply", "$LA\GeoComply", "$PD\GeoComply"))
  'Brother PowerENGAGE'        = @('^Brother PowerENGAGE$', @("$PF86\Brother\PowerENGAGE", "$PF\Brother\PowerENGAGE", "$PF86\PowerENGAGE"))
  'Nightwater Demo'            = @('^Nightwater Demo$', @("$PF86\Steam\steamapps\common\Nightwater Demo"))
  'Guitar Rig 5'               = @('^Native Instruments Guitar Rig 5$', @("$PF\Native Instruments\Guitar Rig 5", "$PD\Native Instruments\Guitar Rig 5", 'C:\Users\USER\Documents\Native Instruments\Guitar Rig 5'))
  'Kontakt (old)'              = @('^Native Instruments Kontakt$', @("$PF\Native Instruments\Kontakt"))
  'MySQL Workbench (dup)'      = @('^MySQL Workbench$', @())
  'NVIDIA Nsight'              = @('^NVIDIA Nsight (Compute|Systems|Visual Studio Edition)', @("$PF\NVIDIA Corporation\Nsight Compute*", "$PF\NVIDIA Corporation\Nsight Systems*", "$PF\NVIDIA Corporation\Nsight Visual Studio Edition*"))
  'Windows SDK 22621'          = @('^Windows Software Development Kit - Windows 10\.0\.22621', @())   # shared Windows Kits folder: never moved
  'Oracle VirtualBox'          = @('^Oracle VirtualBox', @("$PF\Oracle\VirtualBox"))
  'IIS Express + modules'      = @('^(IIS 10\.0 Express|IIS URL Rewrite Module 2|Microsoft Application Request Routing 3\.0)$', @("$PF\IIS Express", "$PF86\IIS Express"))
  'Python 3.10'                = @('^Python 3\.10\.', @("$LA\Programs\Python\Python310", "$PF\Python310"))
  'LM Studio (app only)'       = @('^LM Studio', @("$LA\Programs\LM Studio", "$LA\Programs\lm-studio", "$RA\LM Studio"))
  'Bionic'                     = @('^Bionic ', @("$LA\Programs\Bionic", "$LA\Programs\bionic"))
  'Epic Games'                 = @('^(Epic Games Launcher|Epic Online Services)$', @("$PF86\Epic Games", "$PF\Epic Games", "$LA\EpicGamesLauncher", "$PD\Epic"))
  'GOG Galaxy'                 = @('^GOG GALAXY$', @("$PF86\GOG Galaxy", "$PD\GOG.com"))
  'Cinema 4D'                  = @('^Maxon Cinema 4D', @("$PF\Maxon Cinema 4D 2025", "$RA\Maxon"))
  'IDA Classroom'              = @('^IDA Classroom', @("$PF\IDA Classroom 9.2", "$PF\IDA Classroom Free 9.2", "$RA\Hex-Rays"))
  'Kofax PaperPort'            = @('^(Kofax PaperPort 14|Kofax PDF Viewer SE|PaperPort Image Printer)', @("$PF86\Kofax\PaperPort", "$PF\Kofax\PaperPort", "$PD\Kofax"))
  'Creality Slicer'            = @('^Creality Slicer', @("$PF\Creality Slicer 4.8.2", "$PF\Creality Slicer", "$RA\Creality Slicer"))
}
$never = @('C:\Users\USER\.lmstudio', "$PF\Native Instruments\Kontakt 7", "$PF\Native Instruments\Guitar Rig 7",
           "$PF\MySQL\MySQL Workbench 8.0 CE", "$PF\NVIDIA GPU Computing Toolkit", "$PF86\Windows Kits")
$keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'Registry::HKEY_USERS\*\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
$installed = (Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object DisplayName).DisplayName

$counts = @{ REMOVED = 0; LEFTOVERS = 0; 'STILL THERE' = 0 }
foreach ($name in $targets.Keys) {
  $rx, $paths = $targets[$name]
  $still = @($installed | Where-Object { $_ -match $rx })
  if ($still) { Say ("STILL THERE  {0}: {1}" -f $name, ($still -join '; ')); $counts['STILL THERE']++; continue }
  $found = foreach ($p in $paths) { Get-Item -Path $p -Force -ErrorAction SilentlyContinue }
  $found = @($found | Where-Object { $f = $_.FullName; -not ($never | Where-Object { $f -like "$_*" }) })
  if (-not $found) { Say "REMOVED      $name"; $counts.REMOVED++; continue }
  Say "LEFTOVERS    $name"; $counts.LEFTOVERS++
  foreach ($f in $found) {
    $dest = Join-Path $bin ($f.FullName -replace '^[A-Z]:\\', '' -replace '\\', '__')
    try { New-Item -ItemType Directory -Force $bin | Out-Null; Move-Item -LiteralPath $f.FullName -Destination $dest -ErrorAction Stop; Say "               moved $($f.FullName) -> $dest" }
    catch { Say "               could not move $($f.FullName): $($_.Exception.Message)" }
  }
}
if (Test-Path 'D:\archive\retired-2026-11-04\PHP') { Say 'C:\PHP: moved to D:\archive\retired-2026-11-04\PHP (by finish-setup)' }
elseif (Test-Path 'C:\PHP') { Say 'C:\PHP: still at C:\PHP' }
Say ("SUMMARY  removed {0}, leftovers moved {1}, still installed {2}. Leftover folders: {3}" -f $counts.REMOVED, $counts.LEFTOVERS, $counts['STILL THERE'], $bin)
