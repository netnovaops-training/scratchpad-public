<#
.SYNOPSIS
  Copy-Verified.ps1 -- fast unattended copy (robocopy) followed by a hash check of what was copied.
  2026-11-14, Claude, for Jason: "copying and automation, especially network copies, faster and verified".

.DESCRIPTION
  1. robocopy, two passes so each file size gets the right mode:
       pass 1  files under 1 GB : /MT:<Threads>             (many small files: threads matter)
       pass 2  files 1 GB and up: /MT:4 /J (unbuffered I/O) (Microsoft Learn: /J is for large files and
               hurts with lots of tiny ones -- hence the split)
     Both: /E /COPY:DAT /DCOPY:DAT /R:2 /W:5 /XJ /NP /FP, plus /COMPRESS when the destination is a
     network path (asks for SMB compression "if applicable"; a share that does not support it just
     ignores it).
  2. Verify: every file robocopy copied this run is hashed on BOTH sides with `rclone check --one-way`
     (rclone 1.76 from winget). -Verify All hashes the whole tree instead (slow on big trees).
     No rclone -> falls back to Get-FileHash SHA256.
  3. -Mirror: a final `robocopy /MIR` pass deletes files at the DESTINATION that are no longer in the
     source. Off unless you pass it. The source is never changed by this script.

  Exit codes: 0 = copied and verified, 1 = verify found a difference, 2 = robocopy reported failures
  (robocopy exit 8 or higher). One JSON line per run goes to D:\joblog\events.jsonl.
  Logs per run: D:\joblog\copy\<time>-<name>\ (robocopy logs, the copied-file list, rclone report).

.EXAMPLE
  .\Copy-Verified.ps1 'D:\Garden for all' '\\192.168.1.68\Computer backup\Garden-for-all'
  .\Copy-Verified.ps1 D:\src G:\dst -WhatIf          # list what would copy, change nothing
  .\Copy-Verified.ps1 D:\src G:\dst -Verify All      # hash every file on both sides
  .\Copy-Verified.ps1 D:\src G:\dst -Verify All -Repair   # ...and recopy any file that differs
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory, Position = 0)][string]$Source,
  [Parameter(Mandatory, Position = 1)][string]$Dest,
  [switch]$Mirror,
  [switch]$WhatIf,
  [switch]$Repair,
  [ValidateSet('Copied', 'All', 'None')][string]$Verify = 'Copied',
  [string[]]$ExcludeDir = @(),
  [string[]]$ExcludeFile = @(),
  [int]$Threads = 16,
  [string]$Name = '',
  [string]$LogRoot = 'D:\joblog\copy'
)
$ErrorActionPreference = 'Continue'   # robocopy/rclone report through exit codes, checked below

$Source = $Source.TrimEnd('\'); $Dest = $Dest.TrimEnd('\')
if ($Source -match '^[A-Za-z]:$') { $Source += '\' }   # "D:" alone means the drive root
if (-not $Name) { $Name = (Split-Path $Source -Leaf) -replace '[^\w.-]', '_'; if (-not $Name) { $Name = 'root' } }
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $LogRoot "$stamp-$Name"
New-Item -ItemType Directory -Force $runDir | Out-Null
function Say($m) { $l = '{0}  {1}' -f (Get-Date -Format 'HH:mm:ss'), $m; Add-Content -LiteralPath "$runDir\run.txt" $l; Write-Host $l }

if (-not (Test-Path -LiteralPath $Source)) { Say "FAIL source not found: $Source"; exit 2 }

# A destination is "network" when it is a UNC path or a mapped drive (DriveType 4).
$isNet = $Dest -like '\\*'
if (-not $isNet -and $Dest -match '^([A-Za-z]):') {
  $isNet = [bool](Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$($Matches[1]):'" -ErrorAction SilentlyContinue | Where-Object DriveType -eq 4)
}

# Always-skipped folders: rebuildable caches and system folders. Same list archive_to_nas.ps1 used.
$xd = @('System Volume Information', '$RECYCLE.BIN', 'node_modules', '__pycache__', '.pytest_cache') + $ExcludeDir
$xf = @('pagefile.sys', 'hiberfil.sys', 'swapfile.sys') + $ExcludeFile
$common = @('/E', '/COPY:DAT', '/DCOPY:DAT', '/R:2', '/W:5', '/XJ', '/NP', '/FP', '/BYTES', '/XD') + $xd + @('/XF') + $xf
if ($isNet) { $common += '/COMPRESS' }
if ($WhatIf) { $common += '/L' }

$codes = @()
$passes = @(
  @{ tag = 'small'; extra = @("/MT:$Threads", '/MAX:1073741823') },
  @{ tag = 'large'; extra = @('/MT:4', '/J', '/MIN:1073741824') }
)
foreach ($p in $passes) {
  $log = "$runDir\robocopy-$($p.tag).log"
  Say "robocopy pass $($p.tag): $Source -> $Dest"
  & robocopy $Source $Dest @common @($p.extra) "/UNILOG:$log" | Out-Null
  $codes += $LASTEXITCODE
  Say "  exit $LASTEXITCODE"
}
if ($Mirror) {
  # Separate pass on purpose: /MIR inside the size-split passes would purge the files the OTHER pass
  # handles (each pass only "sees" its own size range). After both passes the trees match, so this
  # pass copies nothing new and only removes destination extras.
  $log = "$runDir\robocopy-mirror.log"
  Say 'robocopy mirror pass (removes destination files missing from the source)'
  & robocopy $Source $Dest /MIR /COPY:DAT /DCOPY:DAT /R:2 /W:5 /XJ /NP /FP /BYTES "/MT:$Threads" /XD @xd /XF @xf "/UNILOG:$log" @(if ($WhatIf) { '/L' }) | Out-Null
  $codes += $LASTEXITCODE
  Say "  exit $LASTEXITCODE"
}
# robocopy exit codes are bit flags: 1 copied, 2 extra files at dest, 4 mismatched, 8 FAILED copies,
# 16 fatal. 0-7 = success. Example from 2026-11-13: archive_to_nas got exit 9 = 8 + 1 (files copied,
# and 4 failed with ERROR 2 "file not found": pipeline checkpoint .db files deleted mid-copy).
$copyFailed = @($codes | Where-Object { $_ -ge 8 }).Count -gt 0

# ---- the list of files robocopy copied this run -------------------------------------------------
# With /FP and without /V the log lists only files it acted on: "New File", "Newer", "Older",
# "Changed", plus "*EXTRA File" lines (those carry the DESTINATION path, so the prefix filter below
# drops them) and "ERROR n ... Copying File <src>" lines (dropped explicitly).
$copied = New-Object System.Collections.Generic.List[string]
$srcPrefix = if ($Source.EndsWith('\')) { $Source } else { "$Source\" }
foreach ($lg in Get-ChildItem "$runDir\robocopy-*.log" -ErrorAction SilentlyContinue) {
  foreach ($line in Get-Content -LiteralPath $lg.FullName -Encoding Unicode) {
    if ($line -match 'ERROR \d+|EXTRA') { continue }
    $path = ($line -split "`t" | Where-Object { $_.Trim() } | Select-Object -Last 1)
    if ($path) { $path = $path.Trim() }
    if ($path -and $path.StartsWith($srcPrefix, [StringComparison]::OrdinalIgnoreCase) -and -not $path.EndsWith('\')) {
      $copied.Add($path.Substring($srcPrefix.Length))
    }
  }
}
$copied = @($copied | Sort-Object -Unique)
Say "files copied this run: $($copied.Count)"

# ---- verify ---------------------------------------------------------------------------------------
$bad = 0; $checked = 0; $how = 'none'
if ($WhatIf -or $Verify -eq 'None') {
  Say 'verify skipped'
} elseif ($Verify -eq 'Copied' -and $copied.Count -eq 0) {
  Say 'verify: nothing new to check'
} else {
  $rclone = (Get-Command rclone -ErrorAction SilentlyContinue).Source
  if (-not $rclone) {
    $rclone = Get-ChildItem "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\Rclone.Rclone*\*\rclone.exe" -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty FullName
  }
  if ($rclone) {
    $how = 'rclone'
    $report = "$runDir\verify-report.txt"
    $args2 = @('check', $Source, $Dest, '--one-way', '--checkers', '16', '--combined', $report,
               '--skip-links', '--log-file', "$runDir\rclone.log", '--log-level', 'NOTICE')
    if ($Verify -eq 'Copied') {
      # rclone wants forward slashes, UTF-8, paths relative to the source root
      $list = "$runDir\copied-files.txt"
      [IO.File]::WriteAllLines($list, [string[]]($copied | ForEach-Object { $_ -replace '\\', '/' }), (New-Object Text.UTF8Encoding $false))
      $args2 += @('--files-from', $list)
    } else {
      foreach ($d in $xd) { $args2 += @('--exclude', "$d/**") }
      foreach ($f in $xf) { $args2 += @('--exclude', $f) }
    }
    Say "verify ($Verify) with rclone check"
    & $rclone @args2 2>$null
    # Count from the report, not rclone's exit code: '=' same, '*' different, '+' missing at the
    # destination, '!' could not read/hash. ('-' = only at the destination: not a copy problem.)
    $lines = @(Get-Content -LiteralPath $report -ErrorAction SilentlyContinue)
    $checked = @($lines | Where-Object { $_ -match '^[=*+!] ' }).Count
    $badLines = @($lines | Where-Object { $_ -match '^[*+!] ' })
    $bad = $badLines.Count
    $badLines | Select-Object -First 20 | ForEach-Object { Say "  MISMATCH $_" }
    if ($bad -and $Repair) {
      # Recopy only the bad files, then hash-check just those again. Tested 2026-11-14: a byte changed
      # at the destination with size+timestamp kept is NOT recopied by a normal robocopy run (only the
      # hash check caught it), and `robocopy <file> /IS /IT` ALSO skipped it ("Skipped 1") on this
      # Windows 11 build. So: .NET copy to a temp name next to the target, then swap it in, so a failed
      # copy never leaves the destination with neither version.
      $badRel = @($badLines | ForEach-Object { $_.Substring(2) -replace '/', '\' })
      foreach ($rel in $badRel) {
        $src = Join-Path $Source $rel; $dst = Join-Path $Dest $rel; $tmp = "$dst.copyverified-tmp"
        try {
          New-Item -ItemType Directory -Force (Split-Path $dst -Parent) | Out-Null
          [IO.File]::Copy($src, $tmp, $true)
          Move-Item -LiteralPath $tmp -Destination $dst -Force
          Say "  recopied $rel"
        } catch { Say "  repair FAILED $rel : $($_.Exception.Message)"; Remove-Item -LiteralPath $tmp -ErrorAction SilentlyContinue }
      }
      $list2 = "$runDir\repair-files.txt"
      [IO.File]::WriteAllLines($list2, [string[]]($badRel | ForEach-Object { $_ -replace '\\', '/' }), (New-Object Text.UTF8Encoding $false))
      & $rclone check $Source $Dest --one-way --files-from $list2 --combined "$runDir\repair-report.txt" --log-file "$runDir\rclone.log" 2>$null
      $still = @(Get-Content "$runDir\repair-report.txt" -ErrorAction SilentlyContinue | Where-Object { $_ -match '^[*+!] ' }).Count
      Say "repair: recopied $($badRel.Count), still bad $still"
      $bad = $still
    }
  } else {
    $how = 'Get-FileHash'
    $files = if ($Verify -eq 'Copied') { $copied } else {
      Get-ChildItem -LiteralPath $Source -Recurse -File -Force | ForEach-Object { $_.FullName.Substring($srcPrefix.Length) }
    }
    Say "verify ($Verify) with Get-FileHash SHA256 (rclone not found)"
    foreach ($rel in $files) {
      $checked++
      $a = Get-FileHash -LiteralPath (Join-Path $Source $rel) -Algorithm SHA256 -ErrorAction SilentlyContinue
      $b = Get-FileHash -LiteralPath (Join-Path $Dest $rel) -Algorithm SHA256 -ErrorAction SilentlyContinue
      if (-not $a -or -not $b -or $a.Hash -ne $b.Hash) { $bad++; Say "  MISMATCH $rel" }
    }
  }
  Say "verify: $checked checked, $bad bad"
}

$exit = if ($copyFailed) { 2 } elseif ($bad) { 1 } else { 0 }
$status = @('PASS', 'FAIL verify', 'FAIL copy')[$exit]
$evt = [ordered]@{ ts = (Get-Date).ToString('s'); job = 'copy_verified'; name = $Name; ok = ($exit -eq 0); src = $Source; dst = $Dest
  robocopy = ($codes -join ','); copied = $copied.Count; verify = $how; checked = $checked; bad = $bad; whatif = [bool]$WhatIf; log = $runDir }
if (-not $WhatIf) { Add-Content 'D:\joblog\events.jsonl' ($evt | ConvertTo-Json -Compress) }
Say "$status $Name : robocopy $($codes -join ','); copied $($copied.Count); verified $checked, bad $bad. Log: $runDir"
exit $exit
