<#
================================================================================
  Everything the desktop has to be running, in one script.

      .\Start-TechHelp.ps1              run it now, watch it work
      .\Start-TechHelp.ps1 -Quiet       no console output (used at startup)
      .\Start-TechHelp.ps1 -Once        start things and exit, do not supervise
      .\Start-TechHelp.ps1 -Status      report what is running and stop

  To have Windows run it at every startup:

      .\Install-Startup.ps1

  WHAT IT LOOKS AFTER

      Ollama            the three models, on 0.0.0.0 so the Pi can reach it
      Whisper           speech to text on :9000
      Ticket listener   receives closed tickets from the Pi and files them

  WHY IT SUPERVISES RATHER THAN JUST LAUNCHING
  --------------------------------------------
  Starting three things at boot is easy. Keeping them up is the part that
  matters, because the failure is silent: Ollama's tray app can be closed by
  accident, Whisper can die on a bad audio file, and neither announces itself.
  Your mother finds out instead, from Nevada, when nothing answers.

  So after starting everything this stays running and checks every minute,
  restarting anything that has gone. It costs nothing while things are healthy.

  THE ONE SETTING THAT MATTERS
  ----------------------------
  OLLAMA_HOST must be 0.0.0.0:11434. On 127.0.0.1 -- the default -- Ollama
  answers only its own machine, the Pi cannot reach it, and every question your
  mother asks fails at the last step with no clue why. This script sets it and
  verifies it actually took effect, rather than trusting that it did.
================================================================================
#>
[CmdletBinding()]
param(
  [switch]$Quiet,
  [switch]$Once,
  [switch]$Status,
  [int]   $CheckSeconds = 60,

  # Point these at wherever yours live. Blank means "skip this one".
  [string]$WhisperScript = "$env:USERPROFILE\ai-backend\whisper-server.py",
  [string]$ListenerScript = "C:\Jasons New Site\www\Mom\desktop-listener.py",
  [string]$TicketDir     = "C:\TechHelp",

  [string]$Reason = "hermes3:8b",
  [string]$Vision = "qwen3-vl:2b",
  [string]$Embed  = "embeddinggemma:300m",
  [int]   $NumCtx = 16384
)

$ErrorActionPreference = 'Continue'
$ProgressPreference    = 'SilentlyContinue'

$LogDir  = Join-Path $env:LOCALAPPDATA 'TechHelp'
$LogFile = Join-Path $LogDir 'startup.log'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Say {
  param([string]$Text, [string]$Colour = 'Gray')
  $line = "[{0:HH:mm:ss}] {1}" -f (Get-Date), $Text
  Add-Content -Path $LogFile -Value $line -ErrorAction SilentlyContinue
  if (-not $Quiet) { Write-Host $line -ForegroundColor $Colour }
}
function Ok($t)  { Say "  ok    $t" 'Green' }
function Warn($t){ Say "  warn  $t" 'Yellow' }
function Bad($t) { Say "  FAIL  $t" 'Red' }
function Head($t){ Say "" ; Say $t 'Cyan' }

# --- is a thing alive? --------------------------------------------------------
function Test-Url {
  param([string]$Url, [int]$TimeoutSec = 4)
  try { Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSec | Out-Null; return $true }
  catch { return $false }
}
function Get-ProcByCmdline {
  param([string]$Match)
  try {
    Get-CimInstance Win32_Process -Filter "Name like 'python%'" -ErrorAction Stop |
      Where-Object { $_.CommandLine -and $_.CommandLine -like "*$Match*" } |
      Select-Object -First 1
  } catch { $null }
}

# ============================================================ 1. environment ==
function Set-OllamaEnvironment {
  $want = [ordered]@{
    'OLLAMA_HOST'              = '0.0.0.0:11434'
    'OLLAMA_MAX_LOADED_MODELS' = '3'
    'OLLAMA_NUM_PARALLEL'      = '1'
    'OLLAMA_FLASH_ATTENTION'   = '1'
    'OLLAMA_KV_CACHE_TYPE'     = 'q8_0'
    'OLLAMA_CONTEXT_LENGTH'    = "$NumCtx"
    'OLLAMA_KEEP_ALIVE'        = '-1'
  }
  $changed = @()
  foreach ($k in $want.Keys) {
    if ([Environment]::GetEnvironmentVariable($k, 'User') -ne $want[$k]) {
      [Environment]::SetEnvironmentVariable($k, $want[$k], 'User')
      $changed += $k
    }
    Set-Item -Path "Env:$k" -Value $want[$k]
  }
  return $changed
}

# ================================================================ 2. Ollama ===
function Start-Ollama {
  if (Test-Url 'http://127.0.0.1:11434/api/tags') { return $true }
  $exe = (Get-Command ollama -ErrorAction SilentlyContinue)
  if (-not $exe) { Bad "ollama is not installed"; return $false }

  # `ollama serve` rather than the tray app: it inherits this process's
  # environment, so OLLAMA_HOST is guaranteed to apply. The tray app reads the
  # environment only when it is launched, which is the single most common way
  # this ends up bound to localhost without anyone realising.
  Say "  starting ollama serve"
  Start-Process -FilePath $exe.Source -ArgumentList 'serve' `
                -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Url 'http://127.0.0.1:11434/api/tags') { return $true }
  }
  return $false
}

function Test-OllamaReachable {
  # Bound to 0.0.0.0, or only to itself? Ask via a real LAN address, because
  # 127.0.0.1 answers either way and tells you nothing.
  $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
         Where-Object { $_.IPAddress -notlike '169.*' -and $_.IPAddress -ne '127.0.0.1' } |
         Select-Object -First 1).IPAddress
  if (-not $ip) { return $null }
  return @{ Ip = $ip; Reachable = (Test-Url "http://${ip}:11434/api/tags") }
}

function Start-Models {
  foreach ($m in @($Reason, $Vision)) {
    $body = @{ model = $m; prompt = 'hi'; stream = $false
               options = @{ num_predict = 1 } } | ConvertTo-Json -Depth 4
    try {
      Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/generate' -Method Post `
        -Body $body -ContentType 'application/json' -TimeoutSec 600 | Out-Null
      Ok "$m loaded"
    } catch { Warn "$m did not load: $($_.Exception.Message)" }
  }
  try {
    $b = @{ model = $Embed; input = 'warm' } | ConvertTo-Json
    Invoke-RestMethod -Uri 'http://127.0.0.1:11434/api/embed' -Method Post `
      -Body $b -ContentType 'application/json' -TimeoutSec 300 | Out-Null
    Ok "$Embed loaded"
  } catch { Warn "$Embed did not load" }
}

# =============================================================== 3. Whisper ===
function Start-Whisper {
  if (Test-Url 'http://127.0.0.1:9000/health') { return $true }
  if (-not $WhisperScript -or -not (Test-Path $WhisperScript)) {
    Warn "Whisper script not found at $WhisperScript -- skipping"
    Say  "        pass -WhisperScript with the right path if this is wrong"
    return $false
  }
  if (Get-ProcByCmdline ([IO.Path]::GetFileName($WhisperScript))) { return $true }
  Say "  starting Whisper"
  Start-Process -FilePath 'pythonw.exe' -ArgumentList "`"$WhisperScript`"" `
                -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
  for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 2
    if (Test-Url 'http://127.0.0.1:9000/health') { return $true }
  }
  return $false
}

# ============================================================== 4. listener ===
function Start-Listener {
  if (-not (Test-Path $ListenerScript)) {
    Warn "ticket listener not found at $ListenerScript -- skipping"
    return $false
  }
  $secret = [Environment]::GetEnvironmentVariable('MOM_WEBHOOK_SECRET', 'User')
  if (-not $secret) {
    Warn "MOM_WEBHOOK_SECRET is not set -- the listener would refuse to start"
    Say  "        generate one, and put the SAME value in the Pi's .env:"
    Say  "        `$b=New-Object byte[] 32"
    Say  "        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes(`$b)"
    Say  "        [Environment]::SetEnvironmentVariable('MOM_WEBHOOK_SECRET',"
    Say  "          ((`$b|%{`$_.ToString('x2')}) -join ''),'User')"
    return $false
  }
  $env:MOM_WEBHOOK_SECRET = $secret
  if (Test-Url 'http://127.0.0.1:8787/') { return $true }
  if (Get-ProcByCmdline 'desktop-listener.py') { return $true }
  Say "  starting the ticket listener"
  Start-Process -FilePath 'pythonw.exe' `
    -ArgumentList "`"$ListenerScript`" --dir `"$TicketDir`"" `
    -WindowStyle Hidden -ErrorAction SilentlyContinue | Out-Null
  Start-Sleep -Seconds 3
  return (Test-Url 'http://127.0.0.1:8787/')
}

# ================================================================== status ====
function Show-Status {
  Head "What is running"
  $o = Test-Url 'http://127.0.0.1:11434/api/tags'
  $w = Test-Url 'http://127.0.0.1:9000/health'
  $l = Test-Url 'http://127.0.0.1:8787/'
  if ($o) { Ok "Ollama    answering on 11434" } else { Bad "Ollama    not answering" }
  if ($w) { Ok "Whisper   answering on 9000"  } else { Bad "Whisper   not answering" }
  if ($l) { Ok "Listener  answering on 8787"  } else { Warn "Listener  not running" }

  $lan = Test-OllamaReachable
  if ($lan -and $lan.Ip) {
    if ($lan.Reachable) {
      Ok "Ollama is reachable from the network at $($lan.Ip):11434"
      Say "        the Pi can use it"
    } else {
      Bad "Ollama answers itself but NOT $($lan.Ip):11434"
      Say "        it is bound to localhost, so the Pi cannot reach it."
      Say "        Nothing your mother asks will get an answer."
      Say "        Quit Ollama completely, then re-run this script."
    }
  }
  if ($o) {
    try {
      $ps = (& ollama ps 2>&1 | Out-String).Trim()
      if ($ps) { Say ""; ($ps -split "`n") | ForEach-Object { Say "        $_" } }
    } catch { }
  }
}

# ==================================================================== main ====
if ($Status) { Show-Status; return }

Say "================================================================" 'White'
Say " Tech Help - desktop services" 'White'
Say "================================================================" 'White'

Head "Settings"
$changed = Set-OllamaEnvironment
if ($changed.Count) { Ok "$($changed.Count) setting(s) updated: $($changed -join ', ')" }
else { Ok "already correct" }

Head "Ollama"
if (Start-Ollama) {
  Ok "answering on 11434"
  $lan = Test-OllamaReachable
  if ($lan -and $lan.Ip -and -not $lan.Reachable) {
    Bad "bound to localhost -- the Pi cannot reach it at $($lan.Ip):11434"
    Say "        An already-running tray instance is holding the old binding."
    Say "        Right-click the Ollama tray icon, Quit, then run this again."
  } elseif ($lan -and $lan.Reachable) {
    Ok "reachable from the network at $($lan.Ip):11434"
  }
  Head "Models"
  Start-Models
} else {
  Bad "Ollama did not come up"
}

Head "Whisper"
if (Start-Whisper) { Ok "answering on 9000" } else { Warn "not running" }

Head "Ticket listener"
if (Start-Listener) { Ok "answering on 8787 -- tickets will be filed to $TicketDir" }
else { Warn "not running" }

if ($Once) { Show-Status; return }

# --- supervise ----------------------------------------------------------------
Head "Watching"
Say "  checking every $CheckSeconds seconds. Close this window to stop."
Say "  log: $LogFile"
while ($true) {
  Start-Sleep -Seconds $CheckSeconds
  if (-not (Test-Url 'http://127.0.0.1:11434/api/tags')) {
    Warn "Ollama went away -- restarting"
    if (Start-Ollama) { Ok "Ollama back"; Start-Models }
  }
  if ($WhisperScript -and (Test-Path $WhisperScript) -and
      -not (Test-Url 'http://127.0.0.1:9000/health')) {
    Warn "Whisper went away -- restarting"
    if (Start-Whisper) { Ok "Whisper back" }
  }
  if ((Test-Path $ListenerScript) -and -not (Test-Url 'http://127.0.0.1:8787/')) {
    Warn "listener went away -- restarting"
    if (Start-Listener) { Ok "listener back" }
  }
}
