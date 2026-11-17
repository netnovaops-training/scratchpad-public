<#
    Which model answers which job -- proved, not assumed.

    "It's configured" and "it works" are different claims, and the gap between
    them is where an evening goes. A route can be listed in config.yaml, appear
    in /jobs, and still fail the moment you use it, because the model behind it
    was never pulled. That only shows up on use.

    So this uses everything:

        1. reads litellm/config.yaml         -- what the mapping is SUPPOSED to be
        2. asks Ollama what it has pulled    -- whether the model even exists here
        3. asks the router each job a real question, and times it
        4. asks Ollama what's resident in VRAM afterwards

    Then it prints one table where every column is a fact, and tells you which
    specific thing is wrong rather than that something is.

    Reads only. Changes nothing. Takes a minute or two the first time, because
    each model has to load into VRAM once.
#>

param([switch]$Quick)   # skip the live questions; just show config vs pulled

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Info { param($m) Write-Host "  $m" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "  The routing table" -ForegroundColor Yellow
Write-Host "  -----------------" -ForegroundColor DarkGray
Write-Host "  Nothing in this stack names a model. Every surface asks for a JOB," -ForegroundColor DarkGray
Write-Host "  and litellm\config.yaml decides what answers it." -ForegroundColor DarkGray

# --- 1. what config.yaml says ------------------------------------------------
$cfgPath = "litellm\config.yaml"
if (-not (Test-Path $cfgPath)) {
    Write-Host "  [FAIL] $cfgPath is missing" -ForegroundColor Red
    Read-Host "  Press Enter to close"; exit 1
}

# Small, predictable file -- a couple of regexes beat pulling in a YAML module.
# Commented-out blocks (the cloud routes) start with '#' and are skipped.
$routes = [ordered]@{}
$order  = @()
$current = $null
foreach ($line in Get-Content $cfgPath) {
    if ($line -match '^\s*#') { continue }
    if ($line -match '^\s*-\s*model_name:\s*(\S+)') {
        $current = $Matches[1]
        $order += $current
        $routes[$current] = [ordered]@{ model = ""; ctx = "" }
        continue
    }
    if ($current -and $line -match '^\s+model:\s*(\S+)')  { $routes[$current].model = $Matches[1] }
    if ($current -and $line -match '^\s+num_ctx:\s*(\S+)') { $routes[$current].ctx   = $Matches[1] }
}
Info "$($order.Count) routes defined"

# --- 2. what Ollama actually has ---------------------------------------------
Head "Models on disk"
$pulled = @()
$list = & docker compose exec -T ollama ollama list 2>$null | Out-String
foreach ($l in ($list -split "`n")) {
    if ($l -match '^(\S+)\s+\S+\s+(\S+\s\S+)\s') { $pulled += $Matches[1] }
}
if ($pulled.Count -eq 0) {
    Write-Host "  [warn] couldn't read 'ollama list'" -ForegroundColor Yellow
    Info "is the ollama container running?"
} else {
    Info "$($pulled.Count) pulled: $($pulled -join ', ')"
}

function Is-Pulled($modelRef) {
    # config says  ollama_chat/qwen2.5:7b  -- Ollama calls it  qwen2.5:7b
    $bare = ($modelRef -replace '^[a-z_]+/', '')
    foreach ($p in $pulled) {
        if ($p -eq $bare) { return $true }
        if ($p -eq "${bare}:latest") { return $true }
        if ($bare -notmatch ':' -and $p -like "$bare*") { return $true }
    }
    return $false
}

# --- 3. ask each one a real question -----------------------------------------
$key = ""
if (Test-Path ".env") {
    $m = Select-String -Path ".env" -Pattern '^LITELLM_MASTER_KEY=(.+)$' | Select-Object -First 1
    if ($m) { $key = $m.Matches[0].Groups[1].Value.Trim() }
}
$auth = @{ Authorization = "Bearer $key" }

$live = @{}
try {
    $ids = (Invoke-RestMethod -Uri "http://127.0.0.1:4000/v1/models" -Headers $auth -TimeoutSec 10 -ErrorAction Stop).data |
           ForEach-Object { $_.id }
} catch {
    $ids = @()
    Write-Host ""
    Write-Host "  [FAIL] the router isn't answering" -ForegroundColor Red
    Info "401 means litellm has a different key than .env:"
    Info "    docker compose up -d --force-recreate litellm hermes telegram"
    Read-Host "  Press Enter to close"; exit 1
}

if (-not $Quick) {
    Head "Asking each job a real question"
    Info "first use of a model loads it into VRAM, so the first one is slow"
    foreach ($job in $order) {
        if ($job -eq "embed") { continue }        # not a chat route
        if ($ids -notcontains $job) { $live[$job] = @{ ok=$false; secs=0; note="not offered" }; continue }

        $body = @{
            model = $job
            messages = @(@{ role = "user"; content = "Reply with exactly one word: ok" })
            max_tokens = 5
        } | ConvertTo-Json -Depth 5

        try {
            $sw = [Diagnostics.Stopwatch]::StartNew()
            $r = Invoke-RestMethod -Uri "http://127.0.0.1:4000/v1/chat/completions" -Method Post `
                    -Headers $auth -ContentType "application/json" -Body $body -TimeoutSec 180 -ErrorAction Stop
            $sw.Stop()
            $said = ($r.choices[0].message.content -replace '\s+',' ').Trim()
            # LiteLLM echoes which deployment actually served it -- the only
            # runtime proof that the mapping is what the file claims.
            $served = if ($r.model) { $r.model } else { "" }
            $live[$job] = @{ ok=$true; secs=[math]::Round($sw.Elapsed.TotalSeconds,1); said=$said; served=$served }
            Write-Host ("  [ok]   {0,-13} {1,6}s  ""{2}""" -f $job, $live[$job].secs, $said) -ForegroundColor Green
        } catch {
            $live[$job] = @{ ok=$false; secs=0; note=$_.Exception.Message }
            Write-Host ("  [FAIL] {0,-13} {1}" -f $job, $_.Exception.Message) -ForegroundColor Red
        }
    }
}

# --- 4. the table ------------------------------------------------------------
Head "job  ->  model"
Write-Host ""
Write-Host ("  {0,-13} {1,-26} {2,-8} {3,-9} {4}" -f "JOB","MODEL","PULLED","ANSWERED","CONTEXT") -ForegroundColor Cyan
Write-Host ("  " + ("-" * 74)) -ForegroundColor DarkGray

foreach ($job in $order) {
    $model = $routes[$job].model
    $ctx   = if ($routes[$job].ctx) { $routes[$job].ctx } else { "default" }
    $has   = Is-Pulled $model

    $pulledTxt = if ($has) { "yes" } else { "NO" }
    $pulledCol = if ($has) { "Green" } else { "Red" }

    if ($Quick -or $job -eq "embed") { $ansTxt = "-"; $ansCol = "DarkGray" }
    elseif ($live[$job].ok)          { $ansTxt = "$($live[$job].secs)s"; $ansCol = "Green" }
    else                             { $ansTxt = "no"; $ansCol = "Red" }

    Write-Host ("  {0,-13} {1,-26} " -f $job, ($model -replace '^[a-z_]+/','')) -NoNewline
    Write-Host ("{0,-8} " -f $pulledTxt) -NoNewline -ForegroundColor $pulledCol
    Write-Host ("{0,-9} " -f $ansTxt)    -NoNewline -ForegroundColor $ansCol
    Write-Host $ctx -ForegroundColor DarkGray
}

# --- 5. what's actually in VRAM ----------------------------------------------
Head "Resident in VRAM right now"
Info "Ollama keeps OLLAMA_MAX_LOADED_MODELS in memory and evicts the rest"
$ps = & docker compose exec -T ollama ollama ps 2>$null | Out-String
if ($ps.Trim()) {
    foreach ($l in ($ps -split "`n")) { if ($l.Trim()) { Write-Host "  $l" -ForegroundColor DarkGray } }
} else {
    Info "(nothing loaded - they load on first use and unload after OLLAMA_KEEP_ALIVE)"
}

# --- 6. what to do about it --------------------------------------------------
$missing = @($order | Where-Object { -not (Is-Pulled $routes[$_].model) })
Write-Host ""
if ($missing.Count -gt 0) {
    Write-Host "  Not pulled: $($missing -join ', ')" -ForegroundColor Yellow
    foreach ($j in $missing) {
        $bare = ($routes[$j].model -replace '^[a-z_]+/','')
        Write-Host "      docker compose exec ollama ollama pull $bare" -ForegroundColor DarkGray
    }
    Info "or just run models.cmd, which pulls everything in PULL_MODELS"
} else {
    Write-Host "  Every route has its model, and the router answers on all of them." -ForegroundColor Green
}

Write-Host ""
Write-Host "  To change what handles a job: edit litellm\config.yaml, then" -ForegroundColor DarkGray
Write-Host "      docker compose restart litellm" -ForegroundColor DarkGray
Write-Host "  Every surface follows - CLI, Telegram, the phone line. No rebuild." -ForegroundColor DarkGray
Write-Host ""
Read-Host "  Press Enter to close"
