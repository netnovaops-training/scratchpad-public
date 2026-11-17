<#
    Why can't a container see the 4070?

    Windows seeing the card and a container seeing the card are different
    questions with different answers, and the chain between them has four links:

        NVIDIA driver on Windows
              -> WSL kernel exposes /dev/dxg
              -> Docker Desktop's WSL distro has the CUDA shim libraries
              -> the container is started with --gpus all

    Any one of them breaks and you get the same useless symptom: "falling back
    to CPU". This walks the chain and says which link is missing.

    Run it, paste the whole output. Changes nothing.
#>

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Ok   { param($m,$d="") Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") Write-Host "  [FAIL] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }
function Dump { param($t) if($t){ ($t -split "`n" | Select-Object -First 12) | ForEach-Object { Write-Host "         $_" -ForegroundColor DarkGray } } }

Write-Host ""
Write-Host "  GPU passthrough - walking the chain" -ForegroundColor Cyan
Write-Host "  -----------------------------------" -ForegroundColor DarkGray

# --- link 1: the driver ------------------------------------------------------
Head "1. The NVIDIA driver, on Windows"
if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    $gpu = & nvidia-smi --query-gpu=name,driver_version --format=csv,noheader 2>&1
    Ok "nvidia-smi works" "$gpu"
} else {
    Bad "nvidia-smi not found" "the driver isn't installed - nothing downstream can work"
}

# --- link 2: WSL -------------------------------------------------------------
Head "2. WSL, and whether the GPU device reaches it"
$wslVer = (wsl --version 2>&1 | Out-String).Trim()
if ($wslVer -match "WSL version") { Ok "modern WSL" ; Dump $wslVer }
else { Bad "old inbox WSL" "run: wsl --update" ; Dump $wslVer }

# /dev/dxg is THE device node. No dxg, no GPU in any container, full stop.
$dxg = (wsl -d docker-desktop -e ls -l /dev/dxg 2>&1 | Out-String).Trim()
if ($dxg -match "dxg" -and $dxg -notmatch "No such|cannot access|not found") {
    Ok "/dev/dxg exists inside Docker's WSL distro" "$dxg"
} else {
    Bad "/dev/dxg is MISSING inside Docker's WSL distro" "this is the break"
    Dump $dxg
}

# The CUDA shim. Present but empty is a real and confusing state.
$wslLib = (wsl -d docker-desktop -e sh -c "ls /usr/lib/wsl/lib 2>&1" | Out-String).Trim()
if ($wslLib -match "libcuda") { Ok "CUDA shim libraries are mounted" "$($wslLib -replace '\s+',' ')" }
else { Warn "no libcuda in /usr/lib/wsl/lib" ; Dump $wslLib }

# --- link 3: Docker ----------------------------------------------------------
Head "3. Docker Desktop"
$dv = (docker version --format '{{.Server.Version}}' 2>&1 | Out-String).Trim()
Ok "Docker server version" "$dv"

$info = (docker info 2>&1 | Out-String)
$osType = ($info -split "`n" | Where-Object { $_ -match "^\s*(Operating System|OSType|Kernel Version|Default Runtime|Runtimes):" })
Dump ($osType -join "`n")

if ($info -match "nvidia") { Ok "docker info mentions an nvidia runtime" }
else { Warn "no nvidia runtime listed" "normal on Docker Desktop/WSL2 - it uses the WSL shim, not the toolkit runtime" }

# --- link 4: the actual test -------------------------------------------------
Head "4. The test that matters"

$img = "ollama/ollama:latest"
if (-not (docker image inspect $img 2>$null)) {
    Write-Host "  pulling $img ..." -ForegroundColor DarkGray
    docker pull $img | Out-Null
}

# --entrypoint is NOT optional here. This image's ENTRYPOINT is `ollama`, so
# `docker run ollama/ollama nvidia-smi -L` runs `ollama nvidia-smi -L` and
# Ollama answers "unknown command nvidia-smi for ollama" -- which looks
# identical to a GPU failure and is nothing of the sort.
Write-Host "  test A: docker run --gpus all --entrypoint nvidia-smi $img -L" -ForegroundColor DarkGray
$a = (docker run --rm --gpus all --entrypoint nvidia-smi $img -L 2>&1 | Out-String).Trim()
if ($a -match "GPU 0") { Ok "--gpus all WORKS" "$a" }
else { Warn "nvidia-smi didn't report a GPU" ; Dump $a }

# nvidia-smi is injected by the container runtime, not baked into the image, so
# "not found" here means the plumbing is missing rather than the card. Asking
# the kernel directly avoids depending on it at all: /dev/dxg present inside
# the container is the real proof the GPU got through.
Write-Host ""
Write-Host "  test B: is /dev/dxg inside the container?" -ForegroundColor DarkGray
$b = (docker run --rm --gpus all --entrypoint sh $img -c "ls -l /dev/dxg 2>&1; ls /usr/lib/wsl/lib 2>/dev/null | head -5" 2>&1 | Out-String).Trim()
if ($b -match "/dev/dxg" -and $b -notmatch "No such|cannot access") { Ok "the GPU device IS in the container" "$b" }
else { Bad "no /dev/dxg inside the container" ; Dump $b }

# The one that actually matters: does Ollama itself find the card? This is the
# real question -- the other two are only proxies for it.
Write-Host ""
Write-Host "  test C: does Ollama itself see it?" -ForegroundColor DarkGray
$c = (docker run --rm --gpus all --entrypoint sh $img -c "ollama serve > /tmp/o.log 2>&1 & sleep 6; cat /tmp/o.log" 2>&1 | Out-String).Trim()
$cGood = ($c -match "library=cuda" -or $c -match "inference compute" -or $c -match "NVIDIA GeForce")
if ($cGood) {
    Ok "Ollama found the GPU"
    Dump (($c -split "`n" | Where-Object { $_ -match "library=|inference compute|total=|available=" }) -join "`n")
} else {
    Warn "Ollama didn't report a CUDA device"
    Dump (($c -split "`n" | Where-Object { $_ -match "library=|gpu|GPU|cuda|CUDA|error" }) -join "`n")
}

# --- verdict -----------------------------------------------------------------
Head "Verdict"
$deviceIn = ($b -match "/dev/dxg" -and $b -notmatch "No such|cannot access")

if ($cGood -or ($a -match "GPU 0")) {
    Write-Host "  The GPU is reachable from containers. You're clear." -ForegroundColor Green
    Write-Host "  Run setup.cmd." -ForegroundColor DarkGray
} elseif ($deviceIn) {
    # The device is there but Ollama didn't announce CUDA. Usually just needs
    # longer than the 6 seconds test C allows, on a cold image.
    Write-Host "  The GPU device IS reaching the container, so passthrough works." -ForegroundColor Yellow
    Write-Host "  Ollama didn't announce CUDA within the 6s this test allows," -ForegroundColor DarkGray
    Write-Host "  which is usually just a cold start rather than a problem." -ForegroundColor DarkGray
    Write-Host "  Run setup.cmd, then check the ollama container's Logs for" -ForegroundColor DarkGray
    Write-Host "  'inference compute' - that line names the card it found." -ForegroundColor DarkGray
} elseif ($dxg -notmatch "dxg") {
    Write-Host "  /dev/dxg never reaches WSL, so no container can see the card." -ForegroundColor Red
    Write-Host "  In an ADMIN PowerShell:  wsl --shutdown" -ForegroundColor DarkGray
    Write-Host "  then fully quit and restart Docker Desktop." -ForegroundColor DarkGray
    Write-Host "  Still missing after that on a Windows Insider build? That's the" -ForegroundColor DarkGray
    Write-Host "  likely cause - Insider builds break WSL GPU passthrough regularly." -ForegroundColor DarkGray
} else {
    Write-Host "  The device reaches WSL but not the container - Docker's GPU" -ForegroundColor Yellow
    Write-Host "  plumbing. Fully quit Docker Desktop (tray icon > Quit, not just" -ForegroundColor DarkGray
    Write-Host "  closing the window) and start it again. If that doesn't take:" -ForegroundColor DarkGray
    Write-Host "  Settings > Troubleshoot > Reset to factory defaults. You lose" -ForegroundColor DarkGray
    Write-Host "  images, not the ai-stack folder." -ForegroundColor DarkGray
}
Write-Host ""
Read-Host "  Press Enter to close"
