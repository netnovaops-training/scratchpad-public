<#
    Open Hermes, starting whatever needs starting first.

    This is what the desktop shortcut runs. The point of it is that clicking an
    icon should just work, from any state the machine happens to be in:

        Docker Desktop not running    -> starts it, waits for the engine
        the container stopped         -> starts it
        the router not ready yet      -> waits, and says what it's waiting for
        something actually wrong      -> says what, and stays open so you can read it

    The last one matters most. A shortcut that flashes a black window and
    vanishes tells you nothing.
#>

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$Host.UI.RawUI.WindowTitle = "Hermes"

function Info { param($m) Write-Host "  $m" -ForegroundColor DarkGray }
function Good { param($m) Write-Host "  $m" -ForegroundColor Green }
function Warn { param($m) Write-Host "  $m" -ForegroundColor Yellow }
function Bad  { param($m) Write-Host "  $m" -ForegroundColor Red }

function Die {
    param($msg, $hint = "")
    Write-Host ""
    Bad $msg
    if ($hint) { Write-Host "" ; Info $hint }
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 1
}

Write-Host ""
Write-Host "  Hermes" -ForegroundColor Yellow

# --- 1. Docker engine --------------------------------------------------------
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Warn "Docker Desktop isn't running - starting it"

    $exe = @(
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
        "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe",
        "$env:LOCALAPPDATA\Docker\Docker Desktop.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if (-not $exe) {
        Die "Couldn't find Docker Desktop to start it." `
            "Start it from the Start menu, wait for the whale icon in the tray to stop animating, then try again."
    }

    Start-Process -FilePath $exe | Out-Null

    # Cold start on a spinning disk is genuinely slow. Two minutes is patient
    # without being infinite.
    $deadline = (Get-Date).AddSeconds(120)
    Write-Host "  waiting for the engine" -NoNewline -ForegroundColor DarkGray
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 3
        Write-Host "." -NoNewline -ForegroundColor DarkGray
        docker info 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { break }
    }
    Write-Host ""

    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Die "Docker Desktop didn't come up within two minutes." `
            "It may still be starting. Watch the tray icon, then run this again."
    }
    Good "Docker is up"
}

# --- 2. the container --------------------------------------------------------
$state = (docker inspect -f "{{.State.Running}}" hermes 2>$null | Out-String).Trim()
if ($state -ne "true") {
    Info "starting the agent..."
    docker compose up -d hermes 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Die "The hermes container wouldn't start." `
            "It waits for the router, which waits for Ollama - so this usually means models are still downloading. Run verify.cmd to see."
    }
}

# --- 3. is the router actually answering -------------------------------------
# The container can be up while the router behind it isn't ready. Checking here
# turns a confusing error on your first question into a short wait.
$key = ""
if (Test-Path ".env") {
    $m = Select-String -Path ".env" -Pattern '^LITELLM_MASTER_KEY=(.+)$' | Select-Object -First 1
    if ($m) { $key = $m.Matches[0].Groups[1].Value.Trim() }
}

# Two failures look identical from here and are completely different problems,
# so they get told apart rather than both being called "not ready":
#
#   connection refused  -> LiteLLM is still starting. Wait; it'll come.
#   401 / 403           -> LiteLLM is up and REFUSING US. The key in .env
#                          doesn't match the one its container was created
#                          with. Waiting will never fix that, and pretending
#                          to wait just burns 90 seconds before a confusing
#                          error. Say so immediately.
$ready = $false
$authFailed = $false
$lastErr = ""
$deadline = (Get-Date).AddSeconds(90)
$announced = $false

while ((Get-Date) -lt $deadline) {
    try {
        Invoke-RestMethod -Uri "http://127.0.0.1:4000/v1/models" `
            -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 5 -ErrorAction Stop | Out-Null
        $ready = $true
        break
    } catch {
        $code = $null
        if ($_.Exception.Response) {
            try { $code = [int]$_.Exception.Response.StatusCode } catch { }
        }
        $lastErr = $_.Exception.Message

        if ($code -eq 401 -or $code -eq 403) { $authFailed = $true; break }

        if (-not $announced) {
            Info "waiting for the router to finish starting..."
            $announced = $true
        }
        Start-Sleep -Seconds 3
    }
}

if ($authFailed) {
    Write-Host ""
    Bad "The router is running but rejecting our key (HTTP 401)."
    Write-Host ""
    Info "A container captures .env when it is CREATED, not when it starts."
    Info "So litellm is still using whatever key it was built with, while"
    Info ".env now holds a different one. Restarting won't help; recreating will."
    Write-Host ""
    Write-Host "  Fix it with either of these:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "      docker compose up -d --force-recreate litellm hermes telegram" -ForegroundColor White
    Write-Host "      update.cmd" -ForegroundColor White
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 1
}

if (-not $ready) {
    Warn "the router still isn't answering - opening anyway"
    if ($lastErr) { Info $lastErr }
    Info "if questions fail, run verify.cmd - it names the missing piece"
}

# --- 4. go -------------------------------------------------------------------
Write-Host ""
docker compose exec hermes hermes

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Bad "Hermes exited with an error - the reason should be above."
    Write-Host ""
    Info "401 or 'authentication'? The router and the agent disagree about the"
    Info "master key. Fix it with:   update.cmd"
    Write-Host ""
    Read-Host "  Press Enter to close"
}
