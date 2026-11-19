<#
    Check that everything actually works. Double-click verify.cmd.

    Written because "it's running" and "it works" are different things, and the
    gap between them is where an evening goes. This talks to every service the
    way a real user would, and tells you which specific thing is wrong rather
    than that something is.

    Safe to run any time. Reads nothing sensitive, changes nothing.
#>

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$pass = 0; $fail = 0; $warn = 0
function Ok   { param($m,$d="") $script:pass++; Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") $script:fail++; Write-Host "  [FAIL] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") $script:warn++; Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

function Get-Env {
    $cfg = @{}
    if (Test-Path ".env") {
        Get-Content ".env" | ForEach-Object {
            if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') { $cfg[$Matches[1]] = $Matches[2].Trim() }
        }
    }
    return $cfg
}

function Try-Get {
    param($Url, $Headers = @{}, $TimeoutSec = 10)
    try { return Invoke-RestMethod -Uri $Url -Headers $Headers -TimeoutSec $TimeoutSec -ErrorAction Stop }
    catch { return $null }
}

Write-Host ""
Write-Host "  Verifying the stack" -ForegroundColor Cyan
Write-Host "  -------------------" -ForegroundColor DarkGray

$cfg = Get-Env
# The leading "compose" matters: these are splatted as `& docker @compose ...`,
# so without it every call is `docker -f ...` and Docker answers "unknown
# shorthand flag: 'f' in -f" -- which reads like a bad compose file rather than
# a missing subcommand.
$compose = @("compose","-f","docker-compose.yml","-f","docker-compose.web.yml")

# --- Docker ------------------------------------------------------------------
Head "Docker"
docker info 2>&1 | Out-Null
if ($LASTEXITCODE -eq 0) { Ok "Docker Desktop is running" }
else { Bad "Docker Desktop isn't running"; Write-Host ""; Write-Host "Nothing else can be checked. Start Docker and re-run." -ForegroundColor Red; exit 1 }

if (-not (Test-Path ".env")) { Bad ".env is missing" "run setup.cmd" } else { Ok ".env exists" }

# --- containers --------------------------------------------------------------
Head "Containers"
$running = @(& docker @compose ps --status running --services 2>$null)
$expected = @{
    "ollama"  = "the model runtime"
    "litellm" = "the router"
    "hermes"  = "the CLI agent - your interface"
    "booking" = "calendar bookings"
    "voice"   = "speech"
}
foreach ($k in $expected.Keys | Sort-Object) {
    if ($running -contains $k) { Ok "$k" } else { Bad "$k is not running" $expected[$k] }
}
# telephony and tunnel removed 2026-11-19 (archived in D:\ai-stack-archive\outdated\telephony\).
foreach ($k in @("stt")) {
    if ($running -contains $k) { Ok "$k" } else { Warn "$k not running" "speech-to-text (runs on the NUC)" }
}
foreach ($k in @("caddy","landing","casey","mom","watchtower")) {  # teabook removed 2026-11-19
    if ($running -contains $k) { Ok "$k" } else { Warn "$k not running" "only needed for the websites" }
}
if ($running -contains "php") {
    Warn "php is running" "that's the rollback container - fine, but the Rust landing page is what Caddy uses"
}

# --- the router and every model ---------------------------------------------
Head "The router and its models"
$key = $cfg["LITELLM_MASTER_KEY"]
$auth = @{ "Authorization" = "Bearer $key" }
$models = Try-Get "http://localhost:4000/v1/models" $auth
if (-not $models) {
    Bad "the router isn't answering" "docker compose logs litellm"
} else {
    $ids = @($models.data | ForEach-Object { $_.id })
    Ok "router is up" "$($ids.Count) routes: $($ids -join ', ')"

    # Actually ask each one a question. A route can be listed and still fail
    # because its model was never pulled -- that only shows up on use.
    foreach ($job in @("fast","chat","coding","reasoning","phone")) {
        if ($ids -notcontains $job) { Warn "route '$job' isn't configured"; continue }
        $body = @{ model = $job; messages = @(@{ role="user"; content="Reply with the single word: ok" }); max_tokens = 5 } | ConvertTo-Json -Depth 5
        try {
            $sw = [Diagnostics.Stopwatch]::StartNew()
            $r = Invoke-RestMethod -Uri "http://localhost:4000/v1/chat/completions" -Method Post `
                    -Headers $auth -ContentType "application/json" -Body $body -TimeoutSec 120 -ErrorAction Stop
            $sw.Stop()
            $txt = ($r.choices[0].message.content -replace '\s+',' ').Trim()
            Ok "model '$job' answered" "$([math]::Round($sw.Elapsed.TotalSeconds,1))s - `"$txt`""
        } catch {
            Bad "model '$job' failed" "probably not pulled: docker compose --profile init up ollama-init"
        }
    }
}

# --- Hermes ------------------------------------------------------------------
Head "Hermes CLI"
if ($running -contains "hermes") {
    $v = & docker @compose exec -T hermes sh -c "command -v hermes" 2>$null
    if ($v) { Ok "hermes is on PATH" $v.Trim() } else { Bad "hermes command not found in the container" }

    $imp = & docker @compose exec -T hermes python -c "import app.cli, shared.router, shared.voice; print('imports ok')" 2>&1
    if ($imp -match "imports ok") { Ok "Hermes imports cleanly" } else { Bad "Hermes fails to import" "$imp" }

    $jobs = & docker @compose exec -T hermes python -c "from shared.router import Router; print(','.join(Router().available_jobs()))" 2>&1
    if ($jobs -match ",") { Ok "Hermes can see the router" "$($jobs.Trim())" }
    else { Bad "Hermes can't reach the router" "$jobs" }
    Write-Host "         start it with:  hermes.cmd" -ForegroundColor DarkGray
} else {
    Bad "hermes container isn't running"
}

# --- voice -------------------------------------------------------------------
Head "Voice"
$vh = Try-Get "http://localhost:8091/health"
if (-not $vh) { Warn "voice service isn't answering" "it may still be downloading models on first start" }
else {
    Ok "voice is up" "engine: $($vh.engine), device: $($vh.device)"
    if ($vh.clones_voices) {
        if ($vh.voices -and $vh.voices.Count -gt 0) { Ok "voice samples found" "$($vh.voices -join ', ')" }
        else { Warn "no voice sample" "put a 10-30s clip at voices\dee.wav, or set VOICE_ENGINE=kokoro" }
    } else { Ok "using a preset voice" "$($vh.default_voice) - Kokoro can't clone, that's expected" }
}

# --- booking -----------------------------------------------------------------
Head "Booking"
$bh = Try-Get "http://localhost:8080/health"
if (-not $bh) { Bad "booking service isn't answering" }
else {
    Ok "booking is up" "services: $($bh.services -join ', ')"
    if ($bh.dry_run) { Warn "DRY RUN - nothing is written to Google Calendar" "follow SETUP-GOOGLE.md when ready" }
    else { Ok "connected to Google Calendar" "$($bh.calendar)" }
    if ($bh.timezone -ne "America/Los_Angeles") { Warn "timezone is $($bh.timezone)" "Nevada should be America/Los_Angeles" }
}

# --- telephony ---------------------------------------------------------------
Head "Phone line"
if ($running -contains "telephony") {
    $th = Try-Get "http://localhost:8093/health"
    if (-not $th) { Bad "telephony isn't answering" }
    else {
        Ok "telephony is up" "$($th.known_callers) known callers"
        foreach ($dep in @("stt","voice","booking")) {
            if ($th.$dep -eq "ok") { Ok "  can reach $dep" } else { Bad "  cannot reach $dep" "$($th.$dep)" }
        }
        if (-not $cfg["PUBLIC_HOST"]) { Warn "PUBLIC_HOST not set" "Twilio has nowhere to stream audio" }
        else { Ok "PUBLIC_HOST set" $cfg["PUBLIC_HOST"] }
        if ($cfg["SMS_ENABLED"] -eq "true") { Ok "outbound SMS enabled" } else { Warn "outbound SMS off" "fine until A2P 10DLC is approved" }
    }
} else { Warn "phone line not running" "start with: docker compose --profile phone up -d" }

# --- telegram ----------------------------------------------------------------
Head "Telegram"
if (-not $cfg["TELEGRAM_BOT_TOKEN"]) { Warn "no bot token" "optional - see START-HERE.md" }
elseif (-not $cfg["TELEGRAM_ALLOWED_USER_IDS"]) { Bad "token set but no allowed user ids" "the bot refuses to start without them" }
else {
    try {
        $me = Invoke-RestMethod -Uri "https://api.telegram.org/bot$($cfg['TELEGRAM_BOT_TOKEN'])/getMe" -TimeoutSec 15 -ErrorAction Stop
        if ($me.ok) { Ok "Telegram token is valid" "@$($me.result.username)" } else { Bad "Telegram rejected the token" }
    } catch { Bad "couldn't reach Telegram" "check the token in .env" }
    if ($running -contains "telegram") {
        $lg = & docker @compose logs --tail 40 telegram 2>&1 | Out-String
        if ($lg -match "bridge up")      { Ok "the bridge is connected" }
        elseif ($lg -match "Unauthorized") { Bad "the bot token is wrong" }
        else { Warn "bridge running, no confirmation in the logs yet" }

        # The bridge now runs the Hermes agent, not a plain chat relay. If the
        # tool belt didn't make it into the image this is where it shows.
        $ag = & docker @compose exec -T telegram python -c "from hermes_agent import tools; from hermes_agent.agent import Agent; print('agent ok', len(tools.SCHEMA))" 2>&1
        if ("$ag" -match "agent ok") { Ok "Telegram is running the Hermes agent" "$(("$ag" -split ' ')[-1]) tools" }
        else { Bad "Telegram can't load the agent" "rebuild it: docker compose build telegram" }
    } else { Warn "telegram container not running" "docker compose up -d telegram" }
}

# --- websites ----------------------------------------------------------------
Head "Websites"
if ($running -notcontains "caddy") {
    Warn "Caddy isn't running" "start with websites.cmd"
} else {
    Ok "Caddy is running"
    if (-not $cfg["ACME_EMAIL"]) { Bad "ACME_EMAIL not set" "Let's Encrypt requires it" }

    # Ask Caddy itself, from inside the network, whether it can see each app.
    # Testing from Windows would only prove the port bindings, which is a
    # different question and not the one that breaks.
    foreach ($pair in @(
        @{ n="landing"; p=8010 },
        @{ n="casey";     p=8000 },
        @{ n="mom";     p=8001 })) {
        $c = & docker @compose exec -T caddy sh -c "wget -q -O /dev/null -T 5 http://$($pair.n):$($pair.p)/healthz 2>&1; echo `$?" 2>$null
        if ($c -match "^0") { Ok "Caddy can reach $($pair.n)" } else { Warn "Caddy can't reach $($pair.n) yet" "check: logs.cmd $($pair.n)" }
    }

    # The landing page, end to end, through Caddy's own routing. Worth its own
    # check because it's the page anyone typing your domain lands on.
    $landing = & docker @compose exec -T caddy sh -c "wget -qO- -T 5 --header='Host: example.org' http://127.0.0.1/ 2>/dev/null" 2>$null | Out-String
    if ($landing -match "Built and hosted") {
        Ok "the landing page renders"
        if ($landing -match 'href="/mama/"') { Ok "  the Mama card points at /mama/" }
        elseif ($landing -match 'href="/mom/"') { Bad "  the Mama card still points at /mom/" "that's a 404 - rebuild: docker compose build landing" }
    } else {
        Warn "couldn't fetch the landing page through Caddy" "logs.cmd landing"
    }

    $lg = & docker @compose logs --tail 200 caddy 2>&1 | Out-String
    if ($lg -match "certificate obtained successfully|certificate obtained") { Ok "TLS certificates obtained" }
    elseif ($lg -match "could not get certificate|no such host|timeout") {
        Bad "certificate issuing failed" "usually ports 80/443 aren't reaching this PC - see WEBSITES.md"
    } else { Warn "no certificate result in the logs yet" "give it a minute: logs.cmd caddy" }

    foreach ($h in @("example.org","teewithmsdee.example.org","peeweelegacy.ddns.net")) {
        try {
            $r = Invoke-WebRequest -Uri "https://$h/healthz" -TimeoutSec 12 -ErrorAction Stop
            if ($r.StatusCode -eq 200) { Ok "$h is live over HTTPS" }
        } catch {
            Warn "$h not reachable from here" "normal if your router hairpins badly - test from mobile data"
        }
    }
}

# --- summary -----------------------------------------------------------------
Write-Host ""
Write-Host ("  " + ("-" * 52)) -ForegroundColor DarkGray
Write-Host "  $pass passed" -NoNewline -ForegroundColor Green
Write-Host "   $warn warnings" -NoNewline -ForegroundColor Yellow
Write-Host "   $fail failed" -ForegroundColor Red
Write-Host ("  " + ("-" * 52)) -ForegroundColor DarkGray
if ($fail -eq 0 -and $warn -eq 0) { Write-Host "  Everything's working." -ForegroundColor Green }
elseif ($fail -eq 0) { Write-Host "  Working. The warnings are things not set up yet, not breakage." -ForegroundColor Yellow }
else { Write-Host "  Fix the failures above. Each line says where to look." -ForegroundColor Red }
Write-Host ""
