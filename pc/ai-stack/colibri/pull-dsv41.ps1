<#
.SYNOPSIS
    Download DeepSeek V4.1 Flash (552B, 510 GB) for colibrì.

.DESCRIPTION
    WHY THIS MODEL. Measured on this machine (unbuffered 8 MB random reads):
    C: reads 4.07 GB/s, D: 2.34 GB/s. On a streaming engine the token rate is
    arithmetic -- bytes read per token divided by disk speed -- so what
    matters is not parameter count but ACTIVE parameters:

        GLM-5.3-Flash   321B    4.8 GB/token    0.85 tok/s
        DeepSeek V4.1   552B    4.5 GB/token    0.90 tok/s   <- this one
        GLM-5.2         744B   12.7 GB/token    0.32 tok/s

    V4.1 Flash is lighter per token than GLM-5.2, a model 35% smaller than
    it. It also needs NO conversion (already fp8 dense / fp4 experts, where
    GLM-5.3 wants a 25-hour pass), and carries vision, tool calling and the
    DSpark draft head.

    WHY C: AND NOT D:. C is the Samsung 990 EVO Plus and reads 74% faster
    than the MSI M450 in D. G: and M: have more free space but are SPINNING
    SATA disks, and upstream is explicit that expert streaming is
    latency-bound and must not live on slow storage.

    SAFE TO INTERRUPT. `hf download` resumes; re-running this script picks up
    where it stopped. huggingface_hub 1.27 writes straight into --local-dir
    with no second copy in the cache, so 510 GB on disk means 510 GB, not a
    terabyte.

.EXAMPLE
    .\pull-dsv41.ps1
    .\pull-dsv41.ps1 -Status      # how far along
#>
[CmdletBinding()]
param(
    [string]$Dest  = 'C:\Models\DeepSeek-V4.1-Flash',
    [string]$Repo  = 'deepseek-ai/DeepSeek-V4.1-Flash',
    [string]$Log   = 'C:\Models\dsv41-download.log',
    [switch]$Status
)

$NeedGB = 510

if ($Status) {
    if (Test-Path -LiteralPath $Dest) {
        $f = @(Get-ChildItem -LiteralPath $Dest -Recurse -File -ErrorAction SilentlyContinue)
        $gb = ($f | Measure-Object -Property Length -Sum).Sum / 1GB
        "  {0:N0} files, {1:N1} GB of ~{2} GB  ({3:N1}%)" -f $f.Count, $gb, $NeedGB, ($gb / $NeedGB * 100)
        if (Test-Path -LiteralPath $Log) {
            "  log last written: {0}" -f (Get-Item -LiteralPath $Log).LastWriteTime
        }
    } else { "  nothing downloaded yet" }
    $running = @(Get-Process hf, huggingface-cli -ErrorAction SilentlyContinue).Count
    "  downloader running: $running"
    exit 0
}

# Refuse rather than fill the system drive. Leaving under 60 GB on C: is how
# Windows starts failing in ways that look like unrelated bugs.
$free = (Get-PSDrive C).Free / 1GB
if ($free -lt ($NeedGB + 60)) {
    Write-Host "  C: has only $([math]::Round($free)) GB free; need $NeedGB + 60 headroom." -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Force -Path (Split-Path $Dest -Parent) | Out-Null
Write-Host "  repo   : $Repo"
Write-Host "  dest   : $Dest"
Write-Host "  free   : $([math]::Round($free)) GB  ->  ~$([math]::Round($free - $NeedGB)) GB after"
Write-Host "  log    : $Log"
Write-Host ""
Write-Host "  starting. This runs for hours and is safe to interrupt." -ForegroundColor Cyan

# THE ACTUAL BOTTLENECK IS AUTHENTICATION, not the transfer library.
# The first run managed 4.8 GB/hour -- 107 hours for 510 GB -- and hf says
# why in its own warning:
#
#   "You are sending unauthenticated requests to the HF Hub. Please set a
#    HF_TOKEN to enable higher rate limits and faster downloads."
#
# Anonymous downloads are rate-limited. A free read-only token lifts that.
#
# TWO DEAD ENDS ALONG THE WAY, recorded so nobody retries them:
#   - hf_transfer was installed and does nothing: huggingface_hub 1.27 says
#     "HF_HUB_ENABLE_HF_TRANSFER is deprecated as 'hf_transfer' is not used
#     anymore", it moved to Xet.
#   - HF_XET_HIGH_PERFORMANCE is the current equivalent, but it speeds up a
#     connection that is not rate-limited; it does not lift a rate limit.
#
# The token is read from .env and never printed. Without one this still
# runs, just slowly -- which is better than not running.
# SINGLE quotes. This path was written double-quoted once and the \a in
# 'www\ai-stack' became a BELL character, leaving 'wwwi-stack' -- a path
# that does not exist. Test-Path was simply false, the whole block was
# skipped, and no token would ever have been read WITH NO MESSAGE SAYING
# SO. A silent skip in the one place that fixes a 20x slowdown.
$envFile = 'C:\Jasons New Site\www\ai-stack\.env'
if (-not (Test-Path -LiteralPath $envFile)) {
    Write-Host "  auth   : .env NOT FOUND at $envFile" -ForegroundColor Red
} else {
    $line = Select-String -Path $envFile -Pattern '^HF_TOKEN=' | Select-Object -First 1
    if ($line) {
        $env:HF_TOKEN = ($line.Line -split '=', 2)[1].Trim()
        Write-Host "  auth   : HF_TOKEN found (higher rate limit)" -ForegroundColor Green
    } else {
        Write-Host "  auth   : NONE -- rate-limited to roughly 5 GB/hour." -ForegroundColor Yellow
        Write-Host "           Add HF_TOKEN=<read-only token> to .env to lift it." -ForegroundColor DarkGray
    }
}
# Xet high-performance transfer: helps once the rate limit is not the cap.
$env:HF_XET_HIGH_PERFORMANCE = "1"

$outLog = "$Log"
$errLog = "$Log.err"
$p = Start-Process -FilePath "hf" `
        -ArgumentList @("download", $Repo, "--local-dir", $Dest) `
        -NoNewWindow -Wait -PassThru `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog
Write-Host ("  hf exited with code {0}" -f $p.ExitCode)
if ($p.ExitCode -ne 0 -and (Test-Path $errLog)) {
    Write-Host "  --- stderr tail ---"
    Get-Content $errLog -Tail 10 | ForEach-Object { Write-Host "    $_" }
}
