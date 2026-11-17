<#
.SYNOPSIS
    Watch a model download progress, live. Read-only: it downloads nothing
    and touches nothing, so it is safe to run in as many windows as you like.

.DESCRIPTION
    Get-Model.ps1 shows hf's own progress bars, which scroll and are hard to
    read as "how far along am I". This answers that one question and keeps
    answering it: GB on disk, percent, rate over the last interval, and an
    ETA computed from the rate you are ACTUALLY getting rather than a
    remembered one.

    Run it in a second window beside the download. Ctrl+C stops the watching,
    never the downloading -- they are separate processes and this one only
    reads the directory.

.PARAMETER Dest
    The model directory to watch. Defaults to the same place Get-Model.ps1
    writes to.

.PARAMETER Every
    Seconds between samples. 30 is a reasonable default: short enough to feel
    live, long enough that the rate is not dominated by one file finishing.

.EXAMPLE
    .\Watch-Model.ps1
.EXAMPLE
    .\Watch-Model.ps1 -Dest D:\Models\DeepSeek -Every 60
#>
[CmdletBinding()]
param(
    [string]$Repo = 'deepseek-ai/DeepSeek-V4.1-Flash',
    [string]$Dest,
    [int]$Every = 30
)

if (-not $Dest) { $Dest = Join-Path 'C:\Models' ($Repo -split '/')[-1] }

# Total size asked of the API, not assumed -- the percentage is meaningless
# if the denominator is a guess.
$total = 0
try {
    $api = Invoke-RestMethod -Uri "https://huggingface.co/api/models/$Repo`?blobs=true" -TimeoutSec 30
    $total = (($api.siblings | Where-Object { $null -ne $_.size }) | Measure-Object -Property size -Sum).Sum
} catch { }

function Size {
    if (-not (Test-Path -LiteralPath $Dest)) { return 0 }
    $f = Get-ChildItem -LiteralPath $Dest -Recurse -File -Force -ErrorAction SilentlyContinue
    if (-not $f) { return 0 }
    return [int64](($f | Measure-Object -Property Length -Sum).Sum)
}

Write-Host ""
Write-Host "  watching $Dest" -ForegroundColor Cyan
if ($total -gt 0) { Write-Host ("  target {0:N1} GB" -f ($total / 1GB)) -ForegroundColor Cyan }
Write-Host "  Ctrl+C stops WATCHING, not downloading." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  time      on disk      done    this interval   rate      eta"
Write-Host "  ---------------------------------------------------------------------"

$prev = Size
$prevAt = Get-Date
$startedAt = $prevAt
$startedAtBytes = $prev

while ($true) {
    Start-Sleep -Seconds $Every
    $now = Size
    $at = Get-Date

    $dBytes = $now - $prev
    $dSecs = ($at - $prevAt).TotalSeconds
    $mbps = if ($dSecs -gt 0) { ($dBytes / 1MB) / $dSecs } else { 0 }

    # ETA from the average since this watcher started, not from the last
    # interval -- a single slow or fast 30 seconds should not swing the
    # estimate by hours.
    $avgSecs = ($at - $startedAt).TotalSeconds
    $avgBps = if ($avgSecs -gt 0) { ($now - $startedAtBytes) / $avgSecs } else { 0 }

    $pct = if ($total -gt 0) { "{0,5:N1}%" -f ($now / $total * 100) } else { "    ?" }
    $eta = "    ?"
    if ($total -gt 0 -and $avgBps -gt 0) {
        $left = ($total - $now) / $avgBps
        $eta = if ($left -gt 86400) { "{0,5:N1}d" -f ($left / 86400) }
               elseif ($left -gt 3600) { "{0,5:N1}h" -f ($left / 3600) }
               else { "{0,5:N0}m" -f ($left / 60) }
    }

    # Colour is the fastest way to see "is it moving" without reading numbers.
    $colour = if ($dBytes -gt 1MB) { 'Green' } elseif ($dBytes -gt 0) { 'Yellow' } else { 'Red' }
    $line = "  {0}  {1,8:N2} GB  {2}  {3,9:N2} GB  {4,6:N1} MB/s  {5}" -f `
        $at.ToString('HH:mm:ss'), ($now / 1GB), $pct, ($dBytes / 1GB), $mbps, $eta
    Write-Host $line -ForegroundColor $colour

    if ($dBytes -le 0) {
        Write-Host "           ^ nothing arrived this interval -- is Get-Model.ps1 still running?" -ForegroundColor DarkYellow
    }

    if ($total -gt 0 -and $now -ge ($total * 0.995)) {
        Write-Host ""
        Write-Host ("  COMPLETE -- {0:N1} GB" -f ($now / 1GB)) -ForegroundColor Green
        break
    }

    $prev = $now
    $prevAt = $at
}
