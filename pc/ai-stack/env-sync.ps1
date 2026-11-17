<#
    Add new settings to .env without touching anything you've already filled in.

    THE PROBLEM THIS SOLVES
    =======================
    `.env.example` is the template; `.env` is yours. Every time a new setting is
    added to the stack it appears in the template, and your .env doesn't have
    it. The obvious move -- copy the template over -- destroys every key,
    token and preference you typed.

    So this goes the other way. It reads the template, finds settings your .env
    is MISSING, and appends only those, with the explanatory comments that go
    with them. Anything already in your .env is left exactly as it is, blank
    values included, because a deliberately blank setting is a decision.

    It never edits, reorders or reformats a line you already have. It only adds.

    A timestamped backup is written first regardless.
#>

param(
    [switch]$DryRun,
    [switch]$NoPause   # for update.ps1, which calls this and can't wait on a prompt
)

function Pause-Unless { if (-not $NoPause) { Read-Host "  Press Enter to close" } }

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Ok   { param($m,$d="") Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") Write-Host "  [FAIL] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

Write-Host ""
Write-Host "  Syncing .env with new settings" -ForegroundColor Cyan
Write-Host "  -----------------------------" -ForegroundColor DarkGray
Write-Host "  Adds what's missing. Changes nothing you've already set." -ForegroundColor DarkGray

if (-not (Test-Path ".env"))         { Bad ".env doesn't exist yet" "run setup.cmd first"; Pause-Unless; exit 1 }
if (-not (Test-Path ".env.example")) { Bad ".env.example is missing"; Pause-Unless; exit 1 }

$mineLines = Get-Content ".env"
$tmplLines = Get-Content ".env.example"

# Which settings do I already have? Blank counts as present -- you chose that.
$have = @{}
$seenAt = @{}
$lineNo = 0
foreach ($l in $mineLines) {
    $lineNo++
    if ($l -match '^\s*([A-Z_][A-Z0-9_]*)\s*=') {
        $k = $Matches[1]
        $have[$k] = $true
        if (-not $seenAt.ContainsKey($k)) { $seenAt[$k] = @() }
        $seenAt[$k] += $lineNo
    }
}

# --- duplicate settings ----------------------------------------------------
# A setting listed TWICE in .env is silently dangerous, and this script is a
# prime cause of it: it appends to the END of the file, so if you later add
# the same setting by hand near the TOP, you end up with two.
#
# Docker Compose reads top to bottom and the LAST one wins. So the copy you
# edited -- the one you can see, near your other settings -- is the one being
# ignored, while a forgotten line at the bottom of a 300-line file decides
# what actually runs. Nothing errors. It just quietly uses the wrong value,
# and no amount of restarting or recreating changes it.
#
# That exact thing happened with UI_USERNAME: an appended `admin` at the
# bottom overrode the real username 300 lines above it.
#
# Reported, never auto-fixed. Deleting lines from a file full of API keys and
# tokens on a guess is not a trade worth making -- but you cannot fix what you
# have not been told about.
$dupes = @($seenAt.Keys | Where-Object { $seenAt[$_].Count -gt 1 } | Sort-Object)
if ($dupes.Count) {
    Head "duplicate settings in .env"
    Warn "$($dupes.Count) setting$(if($dupes.Count -ne 1){'s'}) appear$(if($dupes.Count -eq 1){'s'}) more than once."
    Write-Host "         Docker Compose uses the LAST one. The others do nothing." -ForegroundColor DarkGray
    Write-Host ""
    foreach ($k in $dupes) {
        $lines = $seenAt[$k]
        $winner = $lines[-1]
        $losers = $lines[0..($lines.Count - 2)] -join ", "
        Write-Host ("         {0}" -f $k) -ForegroundColor Yellow
        Write-Host ("             line {0} is used" -f $winner) -ForegroundColor Green
        Write-Host ("             line {0} ignored" -f $losers) -ForegroundColor DarkGray
    }
    Write-Host ""
    Write-Host "         Fix: delete whichever copies you don't want, keeping one." -ForegroundColor DarkGray
    Write-Host "         Then:  docker compose up -d --force-recreate" -ForegroundColor DarkGray
}

# Walk the template. For each setting I don't have, take it AND the comment
# block immediately above it -- the comments are the documentation, and a bare
# variable name with no explanation is worse than useless six months later.
$additions = @()
$pending = @()
$added = @()

foreach ($l in $tmplLines) {
    if ($l -match '^\s*#' -or $l -match '^\s*$') {
        $pending += $l
        continue
    }
    if ($l -match '^\s*([A-Z_][A-Z0-9_]*)\s*=') {
        $name = $Matches[1]
        if (-not $have[$name]) {
            # Keep the comment block, but drop leading blank lines so the
            # appended section doesn't grow a gap every run.
            while ($pending.Count -gt 0 -and $pending[0] -match '^\s*$') {
                $pending = $pending[1..($pending.Count - 1)]
            }
            if ($pending.Count -gt 0) { $additions += ""; $additions += $pending }
            else { $additions += "" }
            $additions += $l
            $added += $name
        }
    }
    $pending = @()
}

Head "Result"

if ($added.Count -eq 0) {
    Ok "nothing to add - your .env already has every setting"
    Write-Host ""
    Pause-Unless
    exit 0
}

foreach ($n in $added) { Ok "will add  $n" }

if ($DryRun) {
    Write-Host ""
    Warn "-DryRun: nothing written"
    Write-Host ""
    Pause-Unless
    exit 0
}

# A blank *_SECRET_KEY is not a setting, it's an unfinished one. Fill them with
# real randomness on the way in, so nobody has to remember to do it -- and so
# an app never falls back to generating a throwaway key per restart, which logs
# every user out on every update and looks like an app bug.
function New-Secret {
    $b = New-Object byte[] 32
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($b) } finally { $rng.Dispose() }
    $hex = ([System.BitConverter]::ToString($b) -replace '-', '').ToLower()
    if ($hex -notmatch '[1-9a-f]') { throw "no entropy - refusing to write a predictable key" }
    return $hex
}
for ($i = 0; $i -lt $additions.Count; $i++) {
    # Also matches *_ENCRYPTION_KEY and *_DB_PASSWORD -- n8n's credential
    # store key and Postgres' password want the same treatment as every
    # *_SECRET_KEY: real randomness, generated once, never regenerated out
    # from under whatever it's protecting.
    #
    # "generated once" matters more for the DB password than for any of the
    # others: Postgres reads it only when it first creates its data
    # directory. Regenerate it later and the database keeps the OLD one,
    # so the only thing that changes is that LiteLLM can no longer log in.
    # [A-Z0-9_] and not [A-Z_]: the character class used to exclude digits,
    # which meant N8N_ENCRYPTION_KEY -- the one variable the comment above
    # specifically named -- never actually matched, and a blank one was left
    # blank. n8n then generates a throwaway key per restart, and every stored
    # credential silently stops decrypting. Found by testing the pattern
    # against the names it claims to cover.
    if ($additions[$i] -match '^([A-Z0-9_]*(SECRET_KEY|ENCRYPTION_KEY|DB_PASSWORD))=\s*$') {
        $additions[$i] = "$($Matches[1])=$(New-Secret)"
        Ok "generated a random $($Matches[1])"
    }
}

# Back up before touching it. Cheap, and the one time it matters it really does.
$stamp  = Get-Date -Format "yyyyMMdd-HHmmss"
# 2026-11-12: backups go to D:\ai-stack-archive\env-backups, out of the repo folder (Jason: keep
# ai-stack tidy). Falls back to the old place beside .env if D: is not there.
$bakDir = "D:\ai-stack-archive\env-backups"
if (-not (Test-Path "D:\")) { $bakDir = "." }
New-Item -ItemType Directory -Force $bakDir | Out-Null
$backup = Join-Path $bakDir ".env.backup-$stamp"
Copy-Item ".env" $backup
Ok "backed up to $backup"

$header = @(
    ""
    "# ============================================================================="
    "#  Added by env-sync on $stamp."
    "#  Your existing settings above were not touched."
    "# ============================================================================="
)

$out = @($mineLines) + $header + $additions

# UTF8 with NO byte-order mark. A BOM makes Docker Compose read the first
# variable name as "?LITELLM_MASTER_KEY" and nothing works, with no clue why.
[System.IO.File]::WriteAllLines(
    (Join-Path $PSScriptRoot ".env"), $out,
    (New-Object System.Text.UTF8Encoding $false))

Ok "added $($added.Count) setting(s) to .env"
Write-Host ""
Write-Host "  New settings are appended at the bottom with their comments." -ForegroundColor DarkGray
Write-Host "  Most have working defaults; open .env to see if any need a value." -ForegroundColor DarkGray
Write-Host ""
Pause-Unless
