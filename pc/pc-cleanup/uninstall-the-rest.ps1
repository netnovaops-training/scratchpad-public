<#
  uninstall-the-rest.ps1 -- the 12 programs whose silent uninstall failed in finish-setup (2026-11-04).
  Jason: "one-time OK and a click-through that accepts the terms and removes the data too".
  ONE elevated run, no clicking: each uninstaller is started with the quiet switch its installer type
  understands (MSI /qn, Inno /VERYSILENT, NSIS /S, Python bundle /quiet). Then verify-uninstalls.ps1
  runs, which moves each app's leftover program AND data folders to
  C:\_To-delete-uninstall-leftovers-2026-11-04 (moved, not deleted -- Jason empties it).
  Never touched: C:\Users\USER\.lmstudio (the models). Log: pc-cleanup\uninstall-the-rest.log
#>
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $here 'uninstall-the-rest.log'
. (Join-Path $PSScriptRoot 'autoclick.ps1'); Disable-QuickEdit
function Say($m) { $l = "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $m; Add-Content $log $l -Encoding utf8; Write-Host $l }
$names = 'TeamViewer', 'Player Location Check', 'Nightwater Demo', 'Native Instruments Guitar Rig 5',
         'Native Instruments Kontakt', 'MySQL Workbench', 'Microsoft Application Request Routing 3.0',
         'IIS URL Rewrite Module 2', 'Python 3.10.11 (64-bit)', 'LM Studio*', 'Maxon Cinema 4D 2025',
         'IDA Classroom 9.2', 'Creality Slicer 4.8.2'
$keys = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'Registry::HKEY_USERS\S-1-5-21-3991538369-352823140-2599934387-1001\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'

function Split-Cmd($s) {   # '"C:\x y\unins000.exe" /foo' -> exe, args
  if ($s -match '^\s*"([^"]+)"\s*(.*)$') { return $Matches[1], $Matches[2] }
  if ($s -match '^\s*(\S+\.exe)\s*(.*)$') { return $Matches[1], $Matches[2] }
  return $s, ''
}
Say "=== start"
foreach ($n in $names) {
  $all = @(Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like $n })
  if (-not $all) { Say "gone already: $n"; continue }
  foreach ($a in $all) {
    $guid = if ($a.PSChildName -match '^\{[0-9A-Fa-f-]+\}$') { $a.PSChildName } else { $null }
    $u = "$($a.UninstallString)"
    if ($guid -and ($a.WindowsInstaller -eq 1 -or $u -match 'msiexec')) {
      $mlog = Join-Path $env:TEMP "uninst-$guid.log"
      $r = Start-Clicked msiexec.exe "/x $guid /qn /norestart /l*v `"$mlog`""
      Say "msi    $($a.DisplayName): exit $($r.Exit)$(if ($r.Exit -eq 1603) { "  (failed; details in $mlog)" })"
      continue
    }
    if ($a.QuietUninstallString) { $exe, $args0 = Split-Cmd $a.QuietUninstallString; $extra = ''; $leaf = Split-Path $exe -Leaf }
    else {
      $exe, $args0 = Split-Cmd $u
      $leaf = Split-Path $exe -Leaf
      $extra = switch -Regex ($leaf) {
        '^unins\d+\.exe$'                 { '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART'; break }   # Inno Setup
        '(?i)python.*\.exe$|\.exe$' { if ($args0 -match '/uninstall') { '/quiet' } else { '/S' } }  # Python bundle / NSIS & most others
      }
    }
    if ($u -match 'steam://') { Say "SKIP   $($a.DisplayName): a Steam game; games-off.ps1 removes Steam and its games"; continue }
    if (-not (Test-Path $exe)) { Say "SKIP   $($a.DisplayName): uninstaller not found at $exe"; continue }
    $r = Start-Clicked $exe "$args0 $extra".Trim()   # silent switch first; autoclick handles any window it still shows
    Say "exe    $($a.DisplayName): exit $($r.Exit)  ($leaf $extra)$(if ($r.Clicked) { "  clicked: $($r.Clicked)" })"
  }
}
Get-Process -Name 'Un_A', 'Au_' -ErrorAction SilentlyContinue | Wait-Process -Timeout 300 -ErrorAction SilentlyContinue
Say "=== uninstalls done; sweeping leftover program and data folders"
& (Join-Path $here 'verify-uninstalls.ps1')
Say "=== finished. Leftovers: C:\_To-delete-uninstall-leftovers-2026-11-04"
if (-not $env:CHAINED) { Read-Host 'Done. Press Enter to close' }
