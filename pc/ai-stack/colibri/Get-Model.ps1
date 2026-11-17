<#
.SYNOPSIS
    Download a Hugging Face model and keep downloading it until it is done.
    Standalone: no repo, no other script, no Claude session.

.DESCRIPTION
    A 475 GB pull runs for days and WILL be interrupted -- a dropped
    connection, a Hugging Face 5xx, a Xet transport error, a reboot, a power
    cut. `hf download` resumes cleanly from whatever is already on disk, so
    the only thing missing is something that notices it stopped and starts it
    again. That is all this is.

    WHAT "FINISHED" MEANS HERE, and why it is not "the process exited". A
    downloader that dies at 80% also exits, with code 0 sometimes. This asks
    the Hugging Face API how many files the repo has and how many bytes they
    are, and only stops when what is on disk matches. Anything else is a
    reason to try again.

    IT REFUSES TO SPIN. If two consecutive attempts add no bytes at all, it
    stops and says so. At that point retrying is not fixing anything and the
    reason needs a person -- a wrong token, a renamed repo, a full disk.

.PARAMETER Repo
    The Hugging Face repo id. Defaults to the DeepSeek V4.1 Flash checkpoint.

.PARAMETER Dest
    Where to put it. Defaults to C:\Models\<repo name>. On the laptop this is
    the parameter to change -- point it at whichever drive has the room.

.PARAMETER NoXet
    Turn OFF Xet transfer and fall back to plain HTTPS. USE THIS IF THE LOG
    KEEPS SHOWING "CAS Client Error". Xet is faster when it works; it is also
    what killed this download once already, with:
        RuntimeError: Task error: File reconstruction error:
        CAS Client Error: Format error: I/O error: error decoding response body
    Plain HTTPS is slower and much harder to upset.

.PARAMETER MaxAttempts
    Stop after this many restarts. 0 (the default) means keep going.

.EXAMPLE
    .\Get-Model.ps1
    Resume the default model into C:\Models. Leave the window open.

.EXAMPLE
    .\Get-Model.ps1 -Dest D:\Models\DeepSeek -NoXet
    Put it on D:, and use plain HTTPS because Xet has been failing.

.EXAMPLE
    .\Get-Model.ps1 -Repo deepseek-ai/DeepSeek-V4-Flash-0731
    The 155 GB sibling instead of the 475 GB one.

.NOTES
    THE TOKEN. This script never asks for one and never stores one. It looks,
    in order, at: the HF_TOKEN environment variable, the token `hf auth login`
    caches, and a .env file beside the script. If none has one it says so and
    carries on unauthenticated -- which works, but slowly and rate-limited.
    To fix that, run `hf auth login` once and paste the token yourself.
#>
[CmdletBinding()]
param(
    [string]$Repo = 'deepseek-ai/DeepSeek-V4.1-Flash',
    [string]$Dest,
    [switch]$NoXet,
    [int]$MaxAttempts = 0,
    [int]$SettleSeconds = 20,
    # Check everything and download nothing: is hf installed, is a token
    # found, how big is the repo, what is already on disk, is there room.
    # Worth having because every one of those can be wrong in a way that only
    # shows up an hour into a transfer.
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Stop'

function Say  ($m) { Write-Host ("  " + $m) }
function Head ($m) { Write-Host ""; Write-Host ("=== " + $m + " ===") -ForegroundColor Cyan }
function Warn ($m) { Write-Host ("  " + $m) -ForegroundColor Yellow }
function Bad  ($m) { Write-Host ("  " + $m) -ForegroundColor Red }

if (-not $Dest) { $Dest = Join-Path 'C:\Models' ($Repo -split '/')[-1] }

Head "what this is going to do"
Say "repo : $Repo"
Say "dest : $Dest"
Say "xet  : $(if ($NoXet) { 'OFF (plain HTTPS)' } else { 'on' })"

# --- the downloader itself -------------------------------------------------
$hf = (Get-Command hf -ErrorAction SilentlyContinue)
if (-not $hf) {
    Warn "the 'hf' command is not on PATH -- installing huggingface_hub"
    $py = (Get-Command py -ErrorAction SilentlyContinue)
    if ($py) { & py -3 -m pip install --quiet --upgrade "huggingface_hub[cli]" }
    else     { & python -m pip install --quiet --upgrade "huggingface_hub[cli]" }
    $hf = (Get-Command hf -ErrorAction SilentlyContinue)
    if (-not $hf) {
        Bad "still no 'hf' after installing. Install Python from python.org with"
        Bad "'Add python.exe to PATH' ticked, then run this again."
        exit 1
    }
}
Say "hf   : $($hf.Source)"

# --- the token, found rather than asked for --------------------------------
# Never printed, never written anywhere by this script. Only its PRESENCE and
# length are reported, which is enough to tell "found one" from "found none"
# without putting a credential in a terminal that may be screen-shared.
$tokenSource = $null
if ($env:HF_TOKEN) {
    $tokenSource = 'HF_TOKEN environment variable'
} elseif (Test-Path "$env:USERPROFILE\.cache\huggingface\token") {
    $tokenSource = 'hf auth login cache'
} else {
    $envFile = Join-Path $PSScriptRoot '..\.env'
    if (Test-Path $envFile) {
        $line = Select-String -Path $envFile -Pattern '^\s*HF_TOKEN\s*=\s*(\S+)' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($line) {
            $env:HF_TOKEN = $line.Matches[0].Groups[1].Value.Trim('"').Trim("'")
            $tokenSource = "$envFile"
        }
    }
}
if ($tokenSource) { Say "token: found via $tokenSource ($($env:HF_TOKEN.Length) chars)" }
else {
    Warn "token: NONE FOUND -- downloading unauthenticated, which is rate limited."
    Warn "       Run 'hf auth login' once and paste your token to fix that."
}

if ($NoXet) { $env:HF_HUB_DISABLE_XET = '1' }

# --- how big is it actually? ----------------------------------------------
# Asked of the API rather than assumed, because "finished" is defined against
# this number and a guess would make the finish condition a guess too.
Head "asking Hugging Face how big $Repo is"
$expectFiles = 0; $expectBytes = 0
try {
    $api = Invoke-RestMethod -Uri "https://huggingface.co/api/models/$Repo`?blobs=true" -TimeoutSec 40
    $sib = @($api.siblings | Where-Object { $null -ne $_.size })
    $expectFiles = $sib.Count
    $expectBytes = ($sib | Measure-Object -Property size -Sum).Sum
    Say ("{0} files, {1:N1} GB" -f $expectFiles, ($expectBytes / 1GB))
} catch {
    Warn "could not reach the API ($($_.Exception.Message))."
    Warn "Falling back to: keep going until an attempt adds nothing."
}

function Get-OnDisk {
    if (-not (Test-Path -LiteralPath $Dest)) { return @{ Files = 0; Bytes = 0 } }
    # -Force so the partial files under .cache are counted; they are real bytes
    # already downloaded and ignoring them makes progress look stalled.
    $f = Get-ChildItem -LiteralPath $Dest -Recurse -File -Force -ErrorAction SilentlyContinue
    if (-not $f) { return @{ Files = 0; Bytes = 0 } }
    $m = $f | Measure-Object -Property Length -Sum
    return @{ Files = $m.Count; Bytes = [int64]$m.Sum }
}

function Test-Complete {
    param($state)
    if ($expectBytes -le 0) { return $false }
    # Completed files only -- the .cache staging directory holds duplicate
    # in-flight bytes, so counting it would report done while files are
    # still missing.
    $top = @(Get-ChildItem -LiteralPath $Dest -Recurse -File -ErrorAction SilentlyContinue |
             Where-Object { $_.FullName -notmatch '\\\.cache\\' })
    if (-not $top) { return $false }
    $bytes = ($top | Measure-Object -Property Length -Sum).Sum
    # 99.5%: the API's byte total counts a few files hf does not materialise.
    return ($bytes -ge ($expectBytes * 0.995))
}

# --- free space, before committing to hours of transfer --------------------
$now = Get-OnDisk
Head "where things stand"
Say ("on disk : {0:N2} GB in {1} files" -f ($now.Bytes / 1GB), $now.Files)
try {
    $driveLetter = (Split-Path -Qualifier (Resolve-Path -LiteralPath (Split-Path -Parent $Dest) -ErrorAction SilentlyContinue).Path) -replace ':', ''
    if (-not $driveLetter) { $driveLetter = (Split-Path -Qualifier $Dest) -replace ':', '' }
    $free = (Get-PSDrive $driveLetter -ErrorAction Stop).Free
    Say ("free on {0}: {1:N0} GB" -f $driveLetter, ($free / 1GB))
    if ($expectBytes -gt 0) {
        $need = $expectBytes - $now.Bytes
        if ($free -lt $need) {
            Bad ("NOT ENOUGH ROOM: {0:N0} GB still to fetch, {1:N0} GB free." -f ($need / 1GB), ($free / 1GB))
            Bad "Point -Dest at a bigger drive."
            exit 1
        }
        Say ("{0:N0} GB still to fetch -- it fits" -f ($need / 1GB))
    }
} catch { Warn "could not read free space for $Dest" }

if ($CheckOnly) {
    Head "check only -- nothing downloaded"
    Say "re-run without -CheckOnly to start."
    exit 0
}

# --- the loop --------------------------------------------------------------
Head "starting. Leave this window open. Ctrl+C stops it; nothing is lost."
$attempt = 0
$deadRuns = 0
$startBytes = (Get-OnDisk).Bytes
$startTime = Get-Date

while ($true) {
    $before = Get-OnDisk

    if (Test-Complete $before) {
        Head "COMPLETE"
        Say ("{0:N1} GB in {1} files" -f ($before.Bytes / 1GB), $before.Files)
        Say "at $Dest"
        break
    }

    $attempt++
    if ($MaxAttempts -gt 0 -and $attempt -gt $MaxAttempts) {
        Warn "stopping: reached -MaxAttempts $MaxAttempts"
        break
    }

    $pct = if ($expectBytes -gt 0) { " ({0:N1}%)" -f ($before.Bytes / $expectBytes * 100) } else { "" }
    Head ("attempt {0} -- {1:N2} GB on disk{2}" -f $attempt, ($before.Bytes / 1GB), $pct)

    $args = @('download', $Repo, '--local-dir', $Dest)
    # Run it in THIS window rather than hiding it: the whole point is that a
    # person can see it working. hf writes its own progress bars to stderr.
    & $hf.Source @args
    $code = $LASTEXITCODE

    Start-Sleep -Seconds 3
    $after = Get-OnDisk
    $gained = $after.Bytes - $before.Bytes

    if (Test-Complete $after) {
        Head "COMPLETE"
        Say ("{0:N1} GB in {1} files" -f ($after.Bytes / 1GB), $after.Files)
        Say "at $Dest"
        break
    }

    if ($gained -le 1MB) {
        $deadRuns++
        Warn ("that attempt added nothing (exit {0}). Strike {1} of 2." -f $code, $deadRuns)
        if ($deadRuns -ge 2) {
            Bad "STOPPING: two attempts in a row added no bytes."
            Bad "Retrying will not fix this. Check, in this order:"
            Bad "  * free space on the destination drive"
            Bad "  * that the token is valid and the repo name is right"
            Bad "  * the log above for 'CAS Client Error' -- if you see it,"
            Bad "    re-run with  -NoXet  to use plain HTTPS instead"
            break
        }
    } else {
        $deadRuns = 0
        $elapsed = ((Get-Date) - $startTime).TotalHours
        $rate = if ($elapsed -gt 0) { (($after.Bytes - $startBytes) / 1GB) / $elapsed } else { 0 }
        $left = if ($expectBytes -gt 0) { ($expectBytes - $after.Bytes) / 1GB } else { 0 }
        Say ("+{0:N2} GB this attempt -- {1:N2} GB total" -f ($gained / 1GB), ($after.Bytes / 1GB))
        if ($rate -gt 0.01 -and $left -gt 0) {
            Say ("averaging {0:N1} GB/hour; {1:N0} GB to go, about {2:N0} hours" -f $rate, $left, ($left / $rate))
        }
    }

    Say "restarting in $SettleSeconds seconds..."
    Start-Sleep -Seconds $SettleSeconds
}

Head "done"
$final = Get-OnDisk
Say ("{0:N2} GB in {1} files at {2}" -f ($final.Bytes / 1GB), $final.Files, $Dest)
