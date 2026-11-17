# =============================================================================
#  auth-setup.ps1 - generate the session signing secret.
#
#      .\auth-setup.ps1          generate the signing secret + write the list
#      .\auth-setup.ps1 -Show    what is configured right now
#
#  WHAT IT WRITES TO .env
#      AUTH_SECRET=...            signs the session cookie
#      AUTH_ALLOWED_EMAILS=...    seeds the account list on a first run only
#
#  IT DOES NOT SET ANYBODY'S PASSWORD. Accounts and password hashes live in
#  .\site-data\auth\people.json and are managed at /admin/people. A password
#  typed into a terminal is a password sitting in your shell history.
# =============================================================================

param([switch]$Show)

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot
$EnvFile = Join-Path $PSScriptRoot ".env"

function Say($m)  { Write-Host $m }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Bad($m)  { Write-Host $m -ForegroundColor Red }
function Good($m) { Write-Host $m -ForegroundColor Green }
function Head($m) { Write-Host ""; Write-Host $m -ForegroundColor Cyan; Write-Host ("-" * 62) -ForegroundColor DarkGray }

function Set-EnvValue {
    param([string]$Key, [string]$Value)
    if (-not (Test-Path $EnvFile)) {
        [System.IO.File]::WriteAllText($EnvFile, "", (New-Object System.Text.UTF8Encoding $false))
    }
    $lines = @(Get-Content $EnvFile -Encoding UTF8)
    $line = "$Key=$Value"; $found = $false
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($l in $lines) {
        if ($l -match "^\s*$([regex]::Escape($Key))\s*=") {
            if (-not $found) { $out.Add($line); $found = $true }
            else { $out.Add("# removed duplicate by auth-setup.ps1: $l"); Warn "  commented out a duplicate $Key" }
        } else { $out.Add($l) }
    }
    if (-not $found) { $out.Add($line) }
    # No BOM. Windows PowerShell 5.1's Set-Content -Encoding UTF8 writes one,
    # and a BOM at the top of .env makes the first variable unreadable.
    [System.IO.File]::WriteAllText($EnvFile, ($out -join "`r`n") + "`r`n",
        (New-Object System.Text.UTF8Encoding $false))
}

function Get-EnvValue {
    param([string]$Key)
    if (-not (Test-Path $EnvFile)) { return $null }
    $hit = Get-Content $EnvFile -Encoding UTF8 |
           Where-Object { $_ -match "^\s*$([regex]::Escape($Key))\s*=" } |
           Select-Object -Last 1
    if (-not $hit) { return $null }
    ($hit -split "=", 2)[1].Trim().Trim("'").Trim('"')
}

if ($Show) {
    Head "Sign-in configuration"
    $sec = Get-EnvValue "AUTH_SECRET"
    $who = Get-EnvValue "AUTH_ALLOWED_EMAILS"

    if (-not $sec)            { Bad  "  secret      NOT SET - auth will refuse to start" }
    elseif ($sec.Length -lt 32){ Bad  "  secret      too short ($($sec.Length) chars, need 32+)" }
    else                       { Good "  secret      set ($($sec.Length) characters)" }

    if (-not $who) { Warn "  seed        empty - only matters on a first run" }
    else { Good "  seed        $who" }

    Say ""
    Say "  Accounts and passwords live in .\site-data\auth\people.json and are"
    Say "  managed at /admin/people, not here. This script only handles the"
    Say "  signing secret."
    exit 0
}

Head "Set up sign-in"

# --- the signing secret ------------------------------------------------------
$existing = Get-EnvValue "AUTH_SECRET"
if ($existing -and $existing.Length -ge 32) {
    Warn "  A signing secret already exists."
    Say  "  Replacing it signs everyone out immediately - every session cookie"
    Say  "  ever issued becomes invalid, because it was signed with the old key."
    $a = Read-Host "  Replace it? (y/n)"
    if ($a -notmatch '^[Yy]') { Say "  Kept the existing secret." }
    else { $existing = $null }
}
if (-not $existing -or $existing.Length -lt 32) {
    $bytes = New-Object byte[] 48
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    # Crypto-random, not Get-Random: this key is the only thing standing
    # between a stranger and a forged six-month session.
    $secret = [Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', ''
    Set-EnvValue "AUTH_SECRET" "'$secret'"
    Good "  signing secret generated and written to .env"
}

# --- who is allowed ----------------------------------------------------------
Say ""
Say "  Who should the account list start with? This SEEDS the list on the very"
Say "  first run only, and the accounts it creates have NO password - they show"
Say "  up at /admin/people marked as unable to sign in until you set one there."
Say ""
Say "  After that first run the file is the truth and this line is ignored, so"
Say "  removing somebody in the admin cannot be undone by a stale value here."
Say ""
$default = "user@example.com,user@example.com,user@example.com"
Say "  Default: $default"
$who = Read-Host "  Press Enter to accept, or type a comma separated list"
if ([string]::IsNullOrWhiteSpace($who)) { $who = $default }
$who = ($who -split ',' | ForEach-Object { $_.Trim().ToLower() } | Where-Object { $_ }) -join ','
Set-EnvValue "AUTH_ALLOWED_EMAILS" $who
Good "  seed: $who"

Head "Next"
Say "  1. Rebuild so the setting takes:"
Say "       .\websites.cmd"
Say ""
Say "  2. Go to /admin/people and give each person a password."
Say "     You choose it and hand it to them however you like - a text, a phone"
Say "     call, a piece of paper. They are asked to pick their own the first"
Say "     time they sign in, so the one you said out loud stops being the"
Say "     password as soon as it has done its job."
Say ""
Say "  There is no reset email, because there is no mail service. If somebody"
Say "  forgets theirs, you set a new one on that same page."
Write-Host ""
