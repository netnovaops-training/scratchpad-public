<#
    Apply changes to a stack that's already running.

    Safe to run any time, as many times as you like. It doesn't reinstall
    anything, doesn't re-download models, and doesn't touch .env values you've
    set. On a stack that's already current it does almost nothing and says so.

    What it does, in order:

      1. adds any NEW settings to .env      (env-sync - never overwrites yours)
      2. retires Open WebUI                 (container removed, history KEPT)
      3. rebuilds images whose code changed (cached - usually seconds)
      4. recreates the containers that read .env
      5. checks the result actually works

    Step 4 is the one people skip and shouldn't. A container captures its
    environment when it is CREATED, not when it starts -- so if .env changed,
    `restart` gets you the old values and a confusing 401. `--force-recreate`
    is what actually applies them.
#>

param(
    [switch]$IncludePhone,     # also recreate the telephony containers
    [switch]$IncludeWeb,       # also rebuild/restart Caddy and the websites
    [switch]$KeepWebUI         # leave Open WebUI alone
)

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$pass = 0; $fail = 0; $warn = 0
function Ok   { param($m,$d="") $script:pass++; Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") $script:fail++; Write-Host "  [FAIL] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") $script:warn++; Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Say  { param($m) Write-Host "         $m" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "  Applying updates to the running stack" -ForegroundColor Cyan
Write-Host "  -------------------------------------" -ForegroundColor DarkGray

# --- 0. Docker ---------------------------------------------------------------
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Bad "Docker Desktop isn't running" "start it and run this again"
    Write-Host ""; Read-Host "  Press Enter to close"; exit 1
}
Ok "Docker Desktop is running"

$compose = @("compose", "-f", "docker-compose.yml")
if ($IncludeWeb) { $compose += @("-f", "docker-compose.web.yml") }

# --- 1. new settings in .env -------------------------------------------------
Head "1. Settings"
if (-not (Test-Path ".env")) {
    Bad ".env is missing" "run setup.cmd first"
    Write-Host ""; Read-Host "  Press Enter to close"; exit 1
}

function Get-Keys([string]$file) {
    $keys = @()
    foreach ($l in Get-Content $file) {
        if ($l -match '^\s*([A-Z_][A-Z0-9_]*)\s*=') { $keys += $Matches[1] }
    }
    return $keys
}

$missing = @(Get-Keys ".env.example" | Where-Object { (Get-Keys ".env") -notcontains $_ } | Select-Object -Unique)
if ($missing.Count -gt 0) {
    Say "new settings: $($missing -join ', ')"
    # -NoPause: this is being called, not double-clicked, and it would otherwise
    # sit waiting for an Enter nobody is there to press.
    & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\env-sync.ps1" -NoPause | Out-Null
    Ok "added $($missing.Count) setting(s) to .env" "your existing values were not touched"
} else {
    Ok ".env already has every setting"
}

# --- 2. Open WebUI -----------------------------------------------------------
Head "2. Open WebUI"
if ($KeepWebUI) {
    Warn "left alone (-KeepWebUI)"
} else {
    $existing = (docker ps -a --filter "name=^open_webui$" --format "{{.Names}}" 2>$null | Out-String).Trim()
    if ($existing) {
        docker stop open_webui 2>&1 | Out-Null
        docker rm   open_webui 2>&1 | Out-Null
        Ok "removed the open_webui container" "port 3000 and ~500MB of RAM back"
    } else {
        Ok "already gone"
    }
    # The VOLUME stays. That's every conversation you had in the browser, and
    # deleting it to save a few megabytes would be a poor trade.
    $vol = (docker volume ls --filter "name=open_webui_data" --format "{{.Name}}" 2>$null | Out-String).Trim()
    if ($vol) { Say "chat history kept in the '$vol' volume - nothing was deleted" }
    Say "to bring it back:  docker compose --profile webui up -d open-webui"
}

# --- 3. rebuild --------------------------------------------------------------
Head "3. Rebuilding images"
Say "cached - only layers whose source changed are rebuilt"
$targets = @("hermes", "telegram", "booking", "n8n", "metrics")
if ($IncludePhone) { $targets += @("telephony", "stt") }
if ($IncludeWeb)   { $targets += @("landing", "casey", "mom", "teabook") }

& docker @($compose + @("build") + $targets)
if ($LASTEXITCODE -ne 0) {
    Bad "build failed" "scroll up - the failing step names itself"
    Write-Host ""; Read-Host "  Press Enter to close"; exit 1
}
Ok "images built" ($targets -join ", ")

# --- 4. recreate -------------------------------------------------------------
Head "4. Applying the new environment"
Say "--force-recreate, not restart: containers read .env when CREATED"

# n8n and metrics are here for the same reason as the rest: n8n reads
# settings like NODES_EXCLUDE only when the container is CREATED, so a
# docker-compose.yml change to it does nothing until a force-recreate. That
# caught us once already -- the monitoring workflows failed with
# "Unrecognized node type: executeCommand" purely because the container
# predated the setting that re-enables it.
$recreate = @("litellm", "hermes", "voice", "booking", "n8n", "metrics", "netdata")

# Only bring up Telegram if it's actually configured; otherwise it exits and
# looks like a failure.
$envText = Get-Content ".env" -Raw
$hasTg = ($envText -match '(?m)^TELEGRAM_BOT_TOKEN=\S') -and ($envText -match '(?m)^TELEGRAM_ALLOWED_USER_IDS=\S')
if ($hasTg) { $recreate += "telegram" } else { Warn "skipping telegram" "no token/allowlist in .env yet" }

if ($IncludePhone) { $recreate += @("telephony", "stt") }

& docker @($compose + @("up", "-d", "--force-recreate") + $recreate)
if ($LASTEXITCODE -ne 0) { Bad "some containers didn't come up" "check Docker Desktop for the red one" }
else { Ok "recreated" ($recreate -join ", ") }

if ($IncludeWeb) {
    # Caddy reads its config from a mounted file, so it only needs a reload.
    & docker @($compose + @("up", "-d", "--force-recreate", "landing", "casey", "mom", "teabook")) | Out-Null
    & docker @($compose + @("restart", "caddy")) | Out-Null
    Ok "websites restarted" "Caddy reloaded its config"
}

# --- 5. did it work ----------------------------------------------------------
Head "5. Checking"
Start-Sleep -Seconds 6

$key = if ($envText -match '(?m)^LITELLM_MASTER_KEY=(.+)$') { $Matches[1].Trim() } else { "" }
try {
    $models = Invoke-RestMethod -Uri "http://localhost:4000/v1/models" -Headers @{ Authorization = "Bearer $key" } -TimeoutSec 20 -ErrorAction Stop
    Ok "the router answers" "$(($models.data | ForEach-Object { $_.id }) -join ', ')"
} catch {
    Warn "router not answering yet" "it can take a minute after a recreate; then: logs.cmd litellm"
}

$hj = & docker @($compose + @("exec","-T","hermes","python","-c",
      "from shared.router import Router; print(','.join(Router().available_jobs()))")) 2>&1
if ("$hj" -match ",") { Ok "Hermes can reach the router" }
else { Bad "Hermes can't reach the router" "if this says 401, the key still differs: docker compose up -d --force-recreate litellm hermes" }

if ($hasTg) {
    $ag = & docker @($compose + @("exec","-T","telegram","python","-c",
          "from hermes_agent import tools; print('agent ok', len(tools.SCHEMA))")) 2>&1
    if ("$ag" -match "agent ok") { Ok "Telegram is running the Hermes agent" "$(("$ag" -split ' ')[-1]) tools" }
    else { Bad "Telegram didn't get the agent" "docker compose build --no-cache telegram" }
}

# --- summary -----------------------------------------------------------------
Write-Host ""
Write-Host ("  " + ("-" * 52)) -ForegroundColor DarkGray
Write-Host "  $pass ok" -NoNewline -ForegroundColor Green
Write-Host "   $warn warnings" -NoNewline -ForegroundColor Yellow
Write-Host "   $fail problems" -ForegroundColor Red
Write-Host ("  " + ("-" * 52)) -ForegroundColor DarkGray
Write-Host ""
if ($fail -eq 0) {
    Write-Host "  Done. Open it with:  hermes.cmd" -ForegroundColor Green
    if ($hasTg) { Write-Host "  On your phone: message the bot /start" -ForegroundColor DarkGray }
} else {
    Write-Host "  Fix the red lines, then run this again - it's safe to repeat." -ForegroundColor Red
}
Write-Host ""
Read-Host "  Press Enter to close"
