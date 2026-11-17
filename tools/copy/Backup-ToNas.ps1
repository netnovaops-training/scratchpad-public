<#
.SYNOPSIS
  Backup-ToNas.ps1 -- the nightly NAS backup: runs every enabled job in backup-jobs.json through
  Copy-Verified.ps1. 2026-11-14, Claude. Supersedes the one-time D:\nuc-work\archive\archive_to_nas.ps1
  for ongoing copies (that script made the single 11-13 archive and stays as the record of it).

.DESCRIPTION
  - Mon-Sat: verify only the files copied that night (fast).
  - Sunday (or -FullVerify): hash EVERY file on both sides and recopy any that differ (-Repair).
    Why weekly: a file damaged on the NAS keeps its size and date, so robocopy never recopies it;
    tested 2026-11-14 on a local copy, only the full hash check found it.
  - Never deletes on the NAS unless a job has "mirror": true.
  - NAS login comes from Windows Credential Manager (set once with cmdkey, see README.md). If the
    NAS is not reachable the job is logged as FAIL with the reason and the others still run.
  - Summary line per job: D:\joblog\events.jsonl (job copy_verified) + one line job backup_to_nas.

.EXAMPLE
  .\Backup-ToNas.ps1                 # all enabled jobs
  .\Backup-ToNas.ps1 -Only www       # one job
  .\Backup-ToNas.ps1 -WhatIf         # list what would copy, change nothing
#>
param([string[]]$Only = @(), [switch]$FullVerify, [switch]$WhatIf)
$ErrorActionPreference = 'Continue'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$cfg = Get-Content -Raw -LiteralPath (Join-Path $here 'backup-jobs.json') | ConvertFrom-Json
$copy = Join-Path $here 'Copy-Verified.ps1'
$full = $FullVerify -or ((Get-Date).DayOfWeek -eq 'Sunday')
$results = @()

foreach ($j in $cfg.jobs) {
  if (-not $j.enabled) { continue }
  if ($Only.Count -and $j.name -notin $Only) { continue }
  # Reachability: the share root of the destination (\\server\share) must answer.
  $shareRoot = ($j.dest -split '\\' | Select-Object -First 4) -join '\'
  if (-not (Test-Path -LiteralPath $shareRoot)) {
    $msg = "NAS share not reachable: $shareRoot (NAS off, or no saved login: see README.md, cmdkey step)"
    Write-Host "FAIL $($j.name): $msg"
    Add-Content 'D:\joblog\events.jsonl' (@{ ts = (Get-Date).ToString('s'); job = 'copy_verified'; name = $j.name; ok = $false; detail = $msg } | ConvertTo-Json -Compress)
    $results += "$($j.name)=unreachable"; continue
  }
  if (-not (Test-Path -LiteralPath $j.source)) {
    Write-Host "FAIL $($j.name): source missing $($j.source)"; $results += "$($j.name)=no-source"; continue
  }
  $a = @{ Source = $j.source; Dest = $j.dest; Name = $j.name; ExcludeDir = @($j.excludeDir); ExcludeFile = @($j.excludeFile) }
  if ($j.mirror) { $a.Mirror = $true }
  if ($WhatIf) { $a.WhatIf = $true }
  if ($full) { $a.Verify = 'All'; $a.Repair = $true }
  & $copy @a
  $results += "$($j.name)=$LASTEXITCODE"
}
$ok = -not ($results | Where-Object { $_ -notmatch '=0$' })
if (-not $WhatIf) {
  Add-Content 'D:\joblog\events.jsonl' (@{ ts = (Get-Date).ToString('s'); job = 'backup_to_nas'; ok = $ok; detail = ($results -join ' ') + $(if ($full) { ' (full verify)' } else { '' }) } | ConvertTo-Json -Compress)
}
Write-Host "$(if ($ok) {'PASS'} else {'FAIL'}) backup_to_nas: $($results -join ' ')"
if (-not $ok) { exit 1 }
