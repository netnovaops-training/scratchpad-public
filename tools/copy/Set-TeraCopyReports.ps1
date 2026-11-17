<#
.SYNOPSIS
  Set-TeraCopyReports.ps1 -- turns on TeraCopy's "save a report when a copy has errors", saved under
  D:\joblog\teracopy. 2026-11-14, Claude. Run it with TeraCopy CLOSED (it rewrites Options.ini on exit,
  which would undo the change); the script refuses while TeraCopy is running.

  Keys changed in %APPDATA%\TeraCopy\Options.ini (a dated backup copy is made next to it first):
    [OnFinish] SaveReport.Enabled=1, SaveReport.ErrorsOnly=1     [Report] Path=D:\joblog\teracopy
  Not changed: SaveReport.Destination (currently 1). TeraCopy's docs do not say what 1 means, so after
  running this open TeraCopy > Preferences > Reports and check the folder shown is D:\joblog\teracopy.
  Already on and left alone: TestAlways=1 (verify every copy), HashName=xxHash3-64.
#>
param([string]$Ini = (Join-Path $env:APPDATA 'TeraCopy\Options.ini'), [switch]$TestCopy)  # -TestCopy: for testing on a copy of the file while TeraCopy runs
$ini = $Ini
if (-not $TestCopy -and (Get-Process TeraCopy -ErrorAction SilentlyContinue)) { Write-Output 'TeraCopy is running: close it (also from the tray), then run this again.'; exit 1 }
Copy-Item -LiteralPath $ini "$ini.bak-$(Get-Date -Format yyyyMMdd-HHmmss)"
New-Item -ItemType Directory -Force 'D:\joblog\teracopy' | Out-Null
$want = @{ 'SaveReport.Enabled' = '1'; 'SaveReport.ErrorsOnly' = '1'; 'Path' = 'D:\joblog\teracopy' }
$section = ''
$out = foreach ($line in Get-Content -LiteralPath $ini) {
  if ($line -match '^\[(.+)\]$') { $section = $Matches[1]; $line; continue }
  $k = ($line -split '=', 2)[0]
  if (($section -eq 'OnFinish' -and $k -like 'SaveReport.*' -and $want.ContainsKey($k)) -or ($section -eq 'Report' -and $k -eq 'Path')) { "$k=$($want[$k])" } else { $line }
}
Set-Content -LiteralPath $ini $out -Encoding ascii
Write-Output 'TeraCopy will now save a report for copies that hit errors, in D:\joblog\teracopy.'
