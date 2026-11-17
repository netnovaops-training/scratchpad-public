<#
    Run this BEFORE setup.cmd.

    It takes about ten seconds and pulls nothing. It exists because `setup.cmd`
    downloads roughly 14GB of models, and the four things most likely to make
    that fail are all invisible until you're twenty minutes in: not enough disk
    in Docker's virtual drive, not enough memory in the WSL virtual machine,
    a port already taken by something else, and the leftover model folder from
    an Ollama you uninstalled.

    None of those produce an error that says what they are. This does.

    Safe. Reads only. Changes nothing.
#>

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$pass = 0; $fail = 0; $warn = 0
function Ok   { param($m,$d="") $script:pass++; Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") $script:fail++; Write-Host "  [STOP] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") $script:warn++; Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

Write-Host ""
Write-Host "  Pre-flight -- the things that waste an evening" -ForegroundColor Cyan
Write-Host "  ---------------------------------------------" -ForegroundColor DarkGray
Write-Host "  Ten seconds. Downloads nothing. Changes nothing." -ForegroundColor DarkGray

# --- where am I --------------------------------------------------------------
Head "Location"
if ($PSScriptRoot -match 'ai-stack$') {
    Ok "running from $PSScriptRoot"
} else {
    Warn "this folder isn't named ai-stack" $PSScriptRoot
}
foreach ($f in "docker-compose.yml",".env.example","setup.cmd","litellm\config.yaml") {
    if (Test-Path $f) { Ok "found $f" } else { Bad "$f is missing" "the folder didn't extract completely" }
}

# --- Docker ------------------------------------------------------------------
Head "Docker Desktop"
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Bad "docker isn't on PATH" "install Docker Desktop, then reopen this window"
} else {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Bad "Docker Desktop isn't running" "start it, wait for the whale to stop animating, run this again"
    } else {
        Ok "Docker Desktop is running"

        # The WSL2 backend is what makes GPU passthrough possible at all. On the
        # Hyper-V backend the card is simply not there, and the failure appears
        # much later as "everything is inexplicably slow".
        $wslDistros = (wsl -l -q 2>$null) -join " "
        if ($wslDistros -match "docker-desktop") { Ok "using the WSL2 backend" "GPU passthrough is possible" }
        else { Warn "couldn't confirm the WSL2 backend" "Settings > General > 'Use the WSL 2 based engine'" }
    }
}

# --- disk --------------------------------------------------------------------
#
# The single most common way this fails. Docker keeps everything inside one
# virtual disk file, and when it fills you get "no space left on device" from
# whichever container happened to be writing -- which is never the one at fault.
Head "Disk"

$need = 60   # GB: ~14 models + ~8 images + Rust build cache + room to breathe

$dockerRoot = "$env:LOCALAPPDATA\Docker\wsl"
$drive = if (Test-Path $dockerRoot) { (Get-Item $dockerRoot).PSDrive.Name } else { $env:SystemDrive.TrimEnd(':') }
$vol = Get-Volume -DriveLetter $drive -ErrorAction SilentlyContinue
if ($vol) {
    $freeGb = [math]::Round($vol.SizeRemaining / 1GB, 1)
    if ($freeGb -ge $need) { Ok "drive $drive has ${freeGb}GB free" "need about ${need}GB" }
    elseif ($freeGb -ge 35) { Warn "drive $drive has only ${freeGb}GB free" "it'll fit, but tightly. ${need}GB is comfortable." }
    else { Bad "drive $drive has ${freeGb}GB free" "the models alone are ~14GB and Docker needs room to unpack. Free up space first." }
} else {
    Warn "couldn't read free space on drive $drive"
}

# How much the Docker VM will actually let itself grow to.
$wslConfig = "$env:USERPROFILE\.wslconfig"
if (Test-Path $wslConfig) {
    $wc = Get-Content $wslConfig -Raw
    # Written out longhand rather than as an inline `$x = if (...) {...}` with a
    # $() subexpression inside a string. That form is valid in PowerShell 7 and
    # misparses in Windows PowerShell 5.1, which is what the .cmd files launch.
    # Not worth being clever about a one-line label.
    $limit = ""
    if ($wc -match 'memory\s*=\s*(\S+)') { $limit = "memory limit: " + $Matches[1] }
    Ok ".wslconfig exists" "$wslConfig  $limit"
} else {
    Warn "no .wslconfig" "WSL will take up to half your RAM by default - usually fine, but if containers start dying with no reason, copy .wslconfig.example to $env:USERPROFILE\.wslconfig"
}

# --- memory ------------------------------------------------------------------
Head "Memory"
$ramGb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 0)
if ($ramGb -ge 32)     { Ok "${ramGb}GB RAM" "plenty" }
elseif ($ramGb -ge 16) { Ok "${ramGb}GB RAM" "fine - the models live in VRAM, not here" }
else                   { Warn "${ramGb}GB RAM" "workable, but close Chrome while models load" }

# --- GPU ---------------------------------------------------------------------
#
# Only the Windows-side check here. setup.cmd does the real one -- launching a
# container and asking it whether it can see the card -- because that's the
# question that matters and it needs an image pull.
Head "GPU"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpu = & nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader 2>$null
    if ($gpu) {
        Ok "Windows sees your card" "$gpu"
        # Driver strings vary in shape; a failed parse shouldn't stop the check.
        try {
            $drv = ($gpu -split ",")[2].Trim()
            $major = [int](($drv -split '\.')[0])
            if ($major -lt 535) {
                Warn "driver $drv is old" "container GPU passthrough wants 535 or newer. Update via GeForce Experience."
            } else {
                Ok "driver $drv is current enough" "passthrough needs 535+"
            }
        } catch { }
    } else { Warn "nvidia-smi ran but said nothing useful" }
} else {
    Warn "nvidia-smi not found" "NVIDIA driver may not be installed. Everything still works on CPU, just slowly."
}

# --- ports -------------------------------------------------------------------
#
# Every one of these binds to 127.0.0.1. If something else already holds one,
# the container fails to start with "port is already allocated" and the rest of
# the stack comes up around the hole -- so you get a half-working system rather
# than an obvious failure.
Head "Ports the stack needs"
$ports = @{
    11434 = "ollama"; 4000 = "the router"; 3000 = "Open WebUI"; 8080 = "booking"
    8091  = "voice";  8092 = "speech-to-text"; 8093 = "the phone line"; 17493 = "voice studio"
}
$clash = $false
foreach ($p in $ports.Keys | Sort-Object) {
    $c = Get-NetTCPConnection -LocalPort $p -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) {
        $proc = (Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue).ProcessName
        if ($proc -match "docker|com\.docker|vpnkit|wslrelay") {
            Ok "$p ($($ports[$p]))" "already Docker's - the stack may be partly up"
        } else {
            Bad "$p ($($ports[$p])) is taken by '$proc'" "that container won't start until this releases the port"
            $clash = $true
        }
    } else {
        Ok "$p free ($($ports[$p]))"
    }
}
if ($clash) {
    Write-Host "         Common culprits: a native Ollama (11434), Grafana (3000)," -ForegroundColor DarkGray
    Write-Host "         or another dev server. Stop it, or change the left-hand" -ForegroundColor DarkGray
    Write-Host "         number in docker-compose.yml's ports: line." -ForegroundColor DarkGray
}

# --- leftover Ollama ---------------------------------------------------------
#
# Uninstalling Ollama on Windows does NOT delete its model folder. If you had
# models pulled, they're still sitting there -- which is either 20GB you can
# reclaim, or 14GB of download you can skip. Both are worth knowing about.
Head "Leftovers from the Ollama you uninstalled"
$oldOllama = "$env:USERPROFILE\.ollama"
if (Test-Path $oldOllama) {
    $sizeGb = [math]::Round(((Get-ChildItem $oldOllama -Recurse -File -ErrorAction SilentlyContinue |
        Measure-Object Length -Sum).Sum) / 1GB, 1)
    $blobs = "$oldOllama\models\blobs"
    if ((Test-Path $blobs) -and $sizeGb -gt 1) {
        Warn "$oldOllama still holds ${sizeGb}GB of models"
        Write-Host ""
        Write-Host "         Two options, and the first one is worth it:" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "         REUSE THEM - skips most of the 14GB download." -ForegroundColor Yellow
        Write-Host "         After setup.cmd has created the volume, run:" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "           docker run --rm -v ai-stack_ollama_data:/dest ``" -ForegroundColor DarkGray
        Write-Host "             -v `"$oldOllama\models:/src:ro`" alpine ``" -ForegroundColor DarkGray
        Write-Host "             sh -c `"cp -rn /src/. /dest/models/`"" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "         Then re-run models.cmd - it pulls only what's missing." -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "         OR DELETE IT - reclaims ${sizeGb}GB:" -ForegroundColor Yellow
        Write-Host "           Remove-Item -Recurse -Force `"$oldOllama`"" -ForegroundColor DarkGray
        Write-Host ""
    } elseif ($sizeGb -gt 0.1) {
        Ok "$oldOllama is only ${sizeGb}GB" "config leftovers, not models. Harmless."
    } else {
        Ok "$oldOllama is empty" "nothing to reclaim"
    }
} else {
    Ok "no leftover Ollama folder" "clean start"
}

# --- summary -----------------------------------------------------------------
Write-Host ""
Write-Host ("  " + ("-" * 52)) -ForegroundColor DarkGray
Write-Host "  $pass ok" -NoNewline -ForegroundColor Green
Write-Host "   $warn warnings" -NoNewline -ForegroundColor Yellow
Write-Host "   $fail blockers" -ForegroundColor Red
Write-Host ("  " + ("-" * 52)) -ForegroundColor DarkGray
Write-Host ""
if ($fail -eq 0) {
    Write-Host "  Clear to go. Next: double-click setup.cmd" -ForegroundColor Green
    Write-Host "  Budget 20-40 minutes, nearly all of it downloading. Leave it open." -ForegroundColor DarkGray
} else {
    Write-Host "  Fix the red lines first. Each one says what to do." -ForegroundColor Red
}
Write-Host ""
Read-Host "  Press Enter to close"
