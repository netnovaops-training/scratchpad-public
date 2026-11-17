<#
    Your voices: what's here, what will actually be used, and what it sounds like.

    THE ONE THAT CATCHES EVERYONE
    =============================
    VOICE_ENGINE=kokoro CANNOT CLONE. Kokoro is small and fast and has fifty
    good preset voices, but it does not do voice cloning at all. Drop your own
    recording into voices\ while kokoro is selected and nothing happens -- no
    error, no warning, it just keeps using a preset. People lose an evening to
    this and conclude their recording was bad.

    Cloning needs VOICE_ENGINE=luxtts (or chatterbox).

        voices.cmd                 what's set up and what will be used
        voices.cmd -Preview jason  render a sample you can play
        voices.cmd -Say "some words to try"
#>

param(
    [string]$Preview = "",
    [string]$Say = "This is a test of how this voice sounds when it reads a full sentence out loud."
)

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Ok   { param($m,$d="") Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") Write-Host "  [FAIL] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Say2 { param($m) Write-Host "         $m" -ForegroundColor DarkGray }

$env_ = @{}
if (Test-Path ".env") {
    Get-Content ".env" | ForEach-Object {
        if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)$') { $env_[$Matches[1]] = $Matches[2].Trim() }
    }
}

Write-Host ""
Write-Host "  Voices" -ForegroundColor Yellow
Write-Host "  ------" -ForegroundColor DarkGray

# --- clips on disk -----------------------------------------------------------
Head "Clips in voices\"
$exts = @("*.wav","*.mp3","*.flac","*.ogg","*.m4a")
$clips = @()
foreach ($e in $exts) { $clips += Get-ChildItem "voices\$e" -ErrorAction SilentlyContinue }

if ($clips.Count -eq 0) {
    Warn "no recordings yet"
    Say2 "Drop a 10-30 second clip in the voices folder. The FILENAME becomes"
    Say2 "the voice name -- voices\jason.wav gives you a voice called 'jason'."
} else {
    foreach ($c in $clips) {
        $name = [IO.Path]::GetFileNameWithoutExtension($c.Name)
        $mb = [math]::Round($c.Length / 1MB, 2)
        Ok "$name" "$($c.Name), ${mb}MB"
    }
}

# --- the engine, which decides whether any of that matters -------------------
Head "Engine"
$engine = if ($env_["VOICE_ENGINE"]) { $env_["VOICE_ENGINE"].ToLower() } else { "luxtts" }

if ($engine -eq "kokoro") {
    if ($clips.Count -gt 0) {
        Bad "VOICE_ENGINE=kokoro -- your recordings are being IGNORED"
        Say2 "Kokoro has 50 preset voices and cannot clone. Your clips are"
        Say2 "sitting there doing nothing, silently."
        Write-Host ""
        Write-Host "      To use your own voices, set in .env:" -ForegroundColor Yellow
        Write-Host "          VOICE_ENGINE=luxtts" -ForegroundColor White
        Write-Host "      then:  docker compose up -d --force-recreate voice" -ForegroundColor DarkGray
        Write-Host ""
        Say2 "First start after switching downloads ~1GB of weights. Watch it"
        Say2 "with: logs.cmd voice"
    } else {
        Ok "kokoro" "preset voices, currently using '$($env_['KOKORO_VOICE'])'"
        Say2 "Fine until you want your own voice. Then: VOICE_ENGINE=luxtts"
    }
} else {
    Ok "$engine" "clones from your recordings"
    if ($clips.Count -eq 0) { Warn "but there are no recordings to clone from" }
}

$names = @($clips | ForEach-Object { [IO.Path]::GetFileNameWithoutExtension($_.Name) })

# --- the config file ---------------------------------------------------------
Head "config\voices.yaml"
$cfgPath = "config\voices.yaml"
$siteVoices = @{}
if (Test-Path $cfgPath) {
    # Deliberately a few regexes rather than a YAML parser: PowerShell has no
    # YAML support built in, this file is small and flat, and adding a module
    # dependency to read six lines would be worse than the regex.
    $cur = ""
    foreach ($line in Get-Content $cfgPath) {
        if ($line -match '^\s{2}([a-z0-9.\-]+\.(?:org|net)):\s*$') { $cur = $Matches[1]; continue }
        if ($cur -and $line -match '^\s{4}voice:\s*(\S+)') { $siteVoices[$cur] = $Matches[1]; $cur = "" }
    }
    if ($siteVoices.Count) {
        foreach ($k in $siteVoices.Keys | Sort-Object) {
            $v = $siteVoices[$k]
            if ($names -contains $v) { Ok "$k -> $v" }
            else { Bad "$k -> $v" "no voices\$v.* on disk yet" }
        }
    } else { Warn "no site voices found in $cfgPath" }
} else {
    Warn "$cfgPath is missing" "site voices are defined there"
}

# --- who speaks where --------------------------------------------------------
Head "Which voice each surface uses"
$fallback = if ($env_["VOICE_DEFAULT"]) { $env_["VOICE_DEFAULT"] } else { "dee" }

$surfaces = @(
    @{ what = "the phone line"; var = "PHONE_VOICE";    why = "your mum's business" },
    @{ what = "Hermes CLI";     var = "HERMES_VOICE";   why = "/speak" },
    @{ what = "Telegram";       var = "TELEGRAM_VOICE"; why = "/voice" }
)
foreach ($s in $surfaces) {
    $v = $env_[$s.var]
    $src = "$($s.var)"
    if (-not $v) { $v = $fallback; $src = "VOICE_DEFAULT (nothing set for this surface)" }

    if ($engine -eq "kokoro") {
        Write-Host ("  {0,-16} {1,-12} {2}" -f $s.what, "(kokoro preset)", $s.why) -ForegroundColor DarkGray
    } elseif ($names -contains $v) {
        Ok "$($s.what) -> $v" "$src"
    } else {
        Bad "$($s.what) -> $v" "no voices\$v.* on disk - this surface will fail to speak"
    }
}

# --- preview -----------------------------------------------------------------
if ($Preview) {
    Head "Preview: $Preview"
    try {
        $body = @{ text = $Say; voice = $Preview } | ConvertTo-Json
        $out = "voices\preview-$Preview.wav"
        Invoke-WebRequest -Uri "http://localhost:8091/preview" -Method Post `
            -ContentType "application/json" -Body $body -OutFile $out -TimeoutSec 120 -ErrorAction Stop
        Ok "written to $out"
        Say2 "Opening it now..."
        Start-Process (Resolve-Path $out)
    } catch {
        Bad "couldn't render it" $_.Exception.Message
        Say2 "Is the voice container up?   logs.cmd voice"
        Say2 "First run after switching engines downloads weights - give it a few minutes."
    }
}

# --- how to record -----------------------------------------------------------
Head "Recording a good clip"
$tips = @(
    "  10-30 seconds. Longer is not better, it just costs time.",
    "  ONE speaker. No interviewer, no crosstalk, no music.",
    "  Read a paragraph rather than saying isolated words -- connected speech",
    "  carries the rhythm, and rhythm is most of what makes a voice recognisable.",
    "  Same microphone and same distance throughout.",
    "  A phone voice memo in a quiet room works well.",
    "",
    "  Then save it as  voices\<name>.wav  and the name is the voice.",
    "",
    "  One thing worth saying plainly: clone voices you have the right to clone.",
    "  Your own is yours. Someone else's needs them to have agreed -- and if the",
    "  person has died, that is a decision for the family to make together, not a",
    "  setting to flip."
)
foreach ($t in $tips) { Write-Host $t -ForegroundColor DarkGray }

Write-Host ""
Read-Host "  Press Enter to close"
