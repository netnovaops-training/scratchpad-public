<#
.SYNOPSIS
    Keep the DeepSeek V4.1 Flash download going until it is actually finished.

.DESCRIPTION
    A 510 GB pull runs for hours and will be interrupted -- by a dropped
    connection, a Hugging Face 5xx, a reboot, or another power cut like the
    one tonight. `hf download` resumes cleanly, so the only thing missing is
    something to notice it stopped and start it again.

    WHAT "FINISHED" MEANS, and why it is not "the process exited". A
    downloader that dies at 80% also exits. This watches the BYTES ON DISK
    and only declares victory when growth stops AND the total is within
    tolerance of the expected size -- so a stall at 400 GB keeps retrying
    instead of being mistaken for success.

    It also refuses to spin: if two consecutive restarts produce no new
    bytes at all, it stops and says so, because at that point retrying is
    not fixing anything and the reason needs a human.

.EXAMPLE
    .\resume-dsv41.ps1                 # watch and restart until complete
    .\resume-dsv41.ps1 -Once           # one restart if stopped, then exit
#>
[CmdletBinding()]
param(
    [string]$Dest        = 'C:\Models\DeepSeek-V4.1-Flash',
    [string]$Puller      = 'C:\Jasons New Site\www\ai-stack\colibri\pull-dsv41.ps1',
    [string]$StateLog    = 'C:\Models\dsv41-resume.log',
    [int]   $ExpectedGB  = 510,
    [int]   $CheckEvery  = 180,      # seconds between checks
    [switch]$Once
)

function Size-GB {
    if (-not (Test-Path -LiteralPath $Dest)) { return 0.0 }
    $f = Get-ChildItem -LiteralPath $Dest -Recurse -File -ErrorAction SilentlyContinue
    if (-not $f) { return 0.0 }
    return (($f | Measure-Object -Property Length -Sum).Sum / 1GB)
}

function Downloader-Running {
    return @(Get-Process hf, huggingface-cli -ErrorAction SilentlyContinue).Count -gt 0
}

function Note($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host "  $line"
    Add-Content -LiteralPath $StateLog -Value $line -ErrorAction SilentlyContinue
}

New-Item -ItemType Directory -Force -Path (Split-Path $StateLog -Parent) | Out-Null
Note "watchdog started (expecting ~$ExpectedGB GB)"

$lastSize    = Size-GB
$deadRetries = 0

while ($true) {
    Start-Sleep -Seconds $CheckEvery
    $now = Size-GB
    $grew = $now - $lastSize

    # Within 1% of expected and no longer growing: done.
    if ($now -ge ($ExpectedGB * 0.99) -and $grew -lt 0.01) {
        Note ("complete: {0:N1} GB" -f $now)
        break
    }

    if (Downloader-Running) {
        if ($grew -gt 0.01) {
            Note ("{0:N1} GB (+{1:N1} GB)" -f $now, $grew)
            $deadRetries = 0
        } else {
            # Running but not growing. Say so rather than assume progress --
            # a wedged transfer looks identical to a slow one from outside.
            Note ("{0:N1} GB -- running but no growth this interval" -f $now)
        }
        $lastSize = $now
        continue
    }

    # Not running, not finished.
    if ($grew -le 0.01) {
        $deadRetries++
        if ($deadRetries -ge 2) {
            Note ("STOPPING: two restarts produced no new bytes (stuck at {0:N1} GB). " -f $now +
                  "Retrying further will not fix this -- check $Dest and the network.")
            break
        }
    } else {
        $deadRetries = 0
    }

    # A downloader that was killed mid-write leaves .lock files behind, and
    # the next one then sits forever on "Still waiting to acquire lock"
    # while looking perfectly alive. Clear them -- but only when nothing is
    # running, or we would be yanking a lock out from under a live transfer.
    if (-not (Downloader-Running)) {
        $locks = @(Get-ChildItem (Join-Path $Dest '.cache') -Recurse -Filter *.lock -ErrorAction SilentlyContinue)
        if ($locks.Count -gt 0) {
            $locks | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
            Note ("cleared {0} stale lock(s) from the previous run" -f $locks.Count)
        }
    }

    Note ("stopped at {0:N1} GB -- restarting" -f $now)
    Start-Process powershell -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', $Puller -WindowStyle Hidden
    $lastSize = $now

    if ($Once) { Note "one restart issued (-Once); exiting"; break }
}
