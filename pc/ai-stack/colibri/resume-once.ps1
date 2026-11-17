<#
    One check, then exit. Run by the Scheduled Task every 15 minutes.

    Deliberately stateless and short-lived: no loop to die, nothing to
    supervise. If the download is running, it does nothing and leaves. If it
    has stopped and is not finished, it clears any stale locks and starts it
    again.

    WHAT "FINISHED" MEANS. Not "the process exited" -- a downloader that
    dies at 80% also exits. It is bytes on disk within 1% of the expected
    510 GB. Until then, stopped means restart.
#>
[CmdletBinding()]
param(
    [string]$Dest       = 'C:\Models\DeepSeek-V4.1-Flash',
    [string]$Repo       = 'deepseek-ai/DeepSeek-V4.1-Flash',
    [string]$Log        = 'C:\Models\dsv41-download.log',
    [string]$StateLog   = 'C:\Models\dsv41-resume.log',
    [int]   $ExpectedGB = 510
)

function Note($m) {
    Add-Content -LiteralPath $StateLog -ErrorAction SilentlyContinue `
        -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m)
}

$gb = 0.0
if (Test-Path -LiteralPath $Dest) {
    $f = Get-ChildItem -LiteralPath $Dest -Recurse -File -ErrorAction SilentlyContinue
    if ($f) { $gb = ($f | Measure-Object -Property Length -Sum).Sum / 1GB }
}

if ($gb -ge ($ExpectedGB * 0.99)) {
    Note ("complete at {0:N1} GB -- nothing to do" -f $gb)
    return
}

if (@(Get-Process hf, huggingface-cli -ErrorAction SilentlyContinue).Count -gt 0) {
    Note ("{0:N1} GB, downloader alive" -f $gb)
    return
}

# Stopped and not finished. A killed downloader leaves .lock files, and the
# next one waits on them forever while looking perfectly healthy -- so clear
# them, but only here, where we have just confirmed nothing is running.
$locks = @(Get-ChildItem (Join-Path $Dest '.cache') -Recurse -Filter *.lock -ErrorAction SilentlyContinue)
if ($locks.Count -gt 0) {
    $locks | ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue }
    Note ("cleared {0} stale lock(s)" -f $locks.Count)
}

# A token lifts HuggingFace's anonymous rate limit, which is worth about 20x
# here: the line measures 85 Mbps with the download paused, and anonymous
# transfers were getting 1.9 GB/hour. Read from .env, never printed.
$envFile = 'C:\Jasons New Site\www\ai-stack\.env'
if (Test-Path -LiteralPath $envFile) {
    $line = Select-String -Path $envFile -Pattern '^HF_TOKEN=' | Select-Object -First 1
    if ($line) { $env:HF_TOKEN = ($line.Line -split '=', 2)[1].Trim() }
}
$env:HF_XET_HIGH_PERFORMANCE = '1'

Note ("stopped at {0:N1} GB -- restarting" -f $gb)

# Start-Process with OS-level redirection, NOT `2>&1 |`. PowerShell 5.1
# wraps a native executable's stderr in ErrorRecords, and hf writes progress
# there -- which aborted an earlier version of this on a mere warning.
Start-Process -FilePath 'hf' `
    -ArgumentList @('download', $Repo, '--local-dir', $Dest) `
    -WindowStyle Hidden `
    -RedirectStandardOutput $Log `
    -RedirectStandardError "$Log.err"
