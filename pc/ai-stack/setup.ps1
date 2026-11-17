<#
    Local AI Stack - one-time setup for Docker Desktop on Windows.

    Don't run this directly. Double-click setup.cmd, which handles the
    PowerShell execution policy for you.

    What it does:
      1. checks Docker Desktop is installed and actually running
      2. checks whether your NVIDIA GPU is visible to containers
      3. writes .env with a freshly generated master key
      4. creates the folders the stack bind-mounts
      5. builds the three local images
      6. starts the stack and downloads the models
      7. opens Open WebUI in your browser

    Safe to run more than once. It won't overwrite an existing .env.
#>

[CmdletBinding()]
param(
    [switch]$SkipModels,   # set up everything but don't download models yet
    [switch]$Cpu,          # force CPU mode even if a GPU is detected
    [switch]$Rebuild       # rebuild images from scratch
)

# NOT "Stop", and this is worth explaining because the reason is unobvious and
# it cost an evening.
#
# Docker writes almost everything to stderr, not stdout -- pull progress, build
# progress, "Unable to find image locally". That is normal, healthy output; it
# is not an error. But when a native command writes to stderr while
# ErrorActionPreference is "Stop", PowerShell wraps it in a NativeCommandError
# and terminates the script. So `docker run`, `docker build` and
# `docker compose up` would each kill setup partway through, and the message you
# get names the line number rather than the cause.
#
# Every docker call below already checks $LASTEXITCODE, which is the correct way
# to test whether a native command failed. This just stops PowerShell from
# guessing on our behalf.
$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

# --- pretty output -----------------------------------------------------------

function Say    { param($m) Write-Host "  $m" }
function Step   { param($m) Write-Host ""; Write-Host "==> $m" -ForegroundColor Cyan }
function Good   { param($m) Write-Host "  [ok] $m" -ForegroundColor Green }
function Warn   { param($m) Write-Host "  [!]  $m" -ForegroundColor Yellow }
function Fail   { param($m) Write-Host ""; Write-Host "  [x]  $m" -ForegroundColor Red }

Write-Host ""
Write-Host "  Local AI Stack - setup" -ForegroundColor Cyan
Write-Host "  ----------------------" -ForegroundColor DarkGray
Say "folder: $PSScriptRoot"

# --- 1. Docker ---------------------------------------------------------------

Step "Checking Docker Desktop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Docker isn't on your PATH."
    Say "Install Docker Desktop from https://www.docker.com/products/docker-desktop/"
    Say "then run this again."
    exit 1
}

try {
    docker info 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "daemon not responding" }
} catch {
    Fail "Docker Desktop isn't running."
    Say "Start it from the Start menu, wait for the whale icon to stop animating,"
    Say "then run this again."
    exit 1
}
Good "Docker Desktop is running"

# Compose v2 ships inside Docker Desktop. Confirm anyway.
docker compose version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Fail "'docker compose' isn't available. Update Docker Desktop to a current version."
    exit 1
}
Good "Docker Compose v2 available"

# --- 2. GPU ------------------------------------------------------------------

Step "Checking your GPU"

$useGpu = $true

if ($Cpu) {
    Warn "-Cpu was passed; skipping GPU detection"
    $useGpu = $false
} else {
    $smi = Get-Command nvidia-smi -ErrorAction SilentlyContinue
    if ($smi) {
        $info = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>$null
        if ($LASTEXITCODE -eq 0 -and $info) {
            Good "Windows sees: $($info -join '; ')"
        } else {
            Warn "nvidia-smi is present but returned nothing useful"
        }
    } else {
        Warn "nvidia-smi not found on Windows - NVIDIA driver may not be installed"
    }

    # The real test: can a container actually see the card? Windows seeing it
    # and a container seeing it are different questions, and only the second one
    # decides whether this is a fast stack or a very slow one.
    #
    # Pull the image first, as its own visible step. It's the ollama image --
    # about 1.5GB, needed either way, and downloading it silently under the
    # word "testing" makes a normal two-minute wait look like a hang.
    $haveOllamaImage = (docker image inspect ollama/ollama:latest 2>$null | Out-String).Trim()
    if (-not $haveOllamaImage) {
        Say "downloading the ollama image (~1.5GB, needed anyway)..."
        docker pull ollama/ollama:latest
        if ($LASTEXITCODE -ne 0) {
            Fail "couldn't download the ollama image."
            Say "Usually no internet, or Docker Desktop is out of disk."
            Say "Check: Settings > Resources > Virtual disk limit (you want 80GB)."
            exit 1
        }
        Good "ollama image downloaded"
    } else {
        Good "ollama image already present"
    }

    # NOTE --entrypoint. The ollama image's ENTRYPOINT is `ollama`, so
    # `docker run ollama/ollama nvidia-smi -L` does NOT run nvidia-smi -- it
    # runs `ollama nvidia-smi -L` and Ollama replies "unknown command
    # nvidia-smi for ollama". Which looks exactly like a GPU failure and isn't
    # one. --entrypoint replaces the command instead of appending to it.
    Say "testing GPU passthrough into a container..."
    $gpuTest = docker run --rm --gpus all --entrypoint nvidia-smi ollama/ollama:latest -L 2>&1

    # Fallback: nvidia-smi is injected by the runtime, not baked into the
    # image, so on some Docker Desktop versions it simply isn't there even
    # though the GPU is. Ask the kernel directly instead -- /dev/dxg present
    # inside the container is the real proof.
    if ($gpuTest -notmatch "GPU 0") {
        $dxgTest = docker run --rm --gpus all --entrypoint sh ollama/ollama:latest -c "ls /dev/dxg" 2>&1
        if ($dxgTest -match "/dev/dxg") {
            $gpuTest = "GPU 0: confirmed via /dev/dxg (nvidia-smi isn't in the image)"
        }
    }

    if ($gpuTest -match "GPU 0") {
        Good "containers can reach the GPU"
        Say (($gpuTest | Where-Object { $_ -match "GPU 0" } | Select-Object -First 1))
    } else {
        Warn "containers CANNOT reach your GPU"
        Write-Host ""
        Say "Docker's actual words, which are the useful part:"
        Write-Host ""
        ($gpuTest | Select-Object -First 8) | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
        Write-Host ""

        Say "Run gpu-check.cmd - it walks the whole chain and names the break."
        Write-Host ""
        Write-Host "      Quick things to try first, in order:" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "      1. Fully quit Docker Desktop - tray icon > Quit, not just" -ForegroundColor Yellow
        Write-Host "         closing the window - then start it again." -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "      2. In an ADMIN PowerShell:" -ForegroundColor Yellow
        Write-Host "             wsl --update" -ForegroundColor White
        Write-Host "             wsl --shutdown" -ForegroundColor White
        Write-Host ""
        Write-Host "      3. Confirm the device reaches Docker's WSL distro:" -ForegroundColor Yellow
        Write-Host "             wsl -d docker-desktop -e ls /dev/dxg" -ForegroundColor White
        Write-Host "         'No such file' means passthrough isn't there at all." -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "      4. Docker Desktop > Settings > General:" -ForegroundColor Yellow
        Write-Host "         'Use the WSL 2 based engine' must be ticked." -ForegroundColor DarkGray
        Write-Host ""

        # Do NOT quietly continue. On CPU an 8B model answers in minutes, not
        # seconds -- the phone line would time out and the web UI would feel
        # broken. Better to stop here than to spend an hour installing a stack
        # that can't do its job.
        Warn "On CPU an 8B model takes MINUTES per reply. The phone line won't work."
        Write-Host ""
        $go = Read-Host "  Continue anyway in CPU mode? (y/N)"
        if ($go -ne "y") {
            Write-Host ""
            Say "Stopped. Nothing was changed. Try 'wsl --update' and run setup.cmd again."
            Write-Host ""
            exit 1
        }
        Warn "continuing in CPU mode at your request"
        $useGpu = $false
    }
}

# NOTE the leading "compose". These get splatted as `& docker @composeArgs`,
# so without it every call becomes `docker -f ...` -- and Docker replies
# "unknown shorthand flag: 'f' in -f", which reads like a broken compose file
# rather than a missing subcommand.
$composeArgs = @("compose", "-f", "docker-compose.yml")
if (-not $useGpu) { $composeArgs += @("-f", "docker-compose.cpu.yml") }

# --- 3. .env -----------------------------------------------------------------

Step "Configuration"

# 24 random bytes -> hex. Never leaves this machine.
#
# Uses RandomNumberGenerator::Create(), NOT ::Fill(). `Fill` is .NET Core only,
# so on Windows PowerShell 5.1 -- which is what setup.cmd runs -- it throws
# MethodNotFound. The old code then carried on with an all-zero buffer and wrote
# a key of `sk-000000...` while printing "wrote .env with a generated master
# key". A predictable secret that reports success is worse than a crash, which
# is why this now checks its own work and refuses to continue if the bytes
# didn't actually come out random.
function New-Key([string]$prefix) {
    $b = New-Object byte[] 24
    $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $rng.GetBytes($b) } finally { $rng.Dispose() }
    $hex = ([System.BitConverter]::ToString($b) -replace '-', '').ToLower()
    if ($hex -notmatch '[1-9a-f]') {
        throw "key generation produced no entropy - refusing to write a predictable secret"
    }
    return $prefix + $hex
}

# A key that was written by the broken version above. Recognisable on sight.
function Test-WeakKey([string]$value) {
    if (-not $value) { return $true }
    $body = $value -replace '^(sk|bk)-', ''
    return ($body -match '^0+$') -or ($body -match 'changeme') -or ($body.Length -lt 16)
}

if (Test-Path ".env") {
    # Repair a .env written by the version of this script that silently failed
    # to generate keys, rather than leaving a predictable secret in place.
    $existing = Get-Content ".env" -Raw
    $curKey  = if ($existing -match '(?m)^LITELLM_MASTER_KEY=(.*)$') { $Matches[1].Trim() } else { "" }
    $curBook = if ($existing -match '(?m)^BOOKING_API_KEY=(.*)$')   { $Matches[1].Trim() } else { "" }

    $repaired = $false
    if (Test-WeakKey $curKey) {
        Warn "LITELLM_MASTER_KEY was not random - regenerating it"
        $existing = $existing -replace '(?m)^LITELLM_MASTER_KEY=.*$', "LITELLM_MASTER_KEY=$(New-Key 'sk-')"
        $repaired = $true
    }
    if (Test-WeakKey $curBook) {
        Warn "BOOKING_API_KEY was not random - regenerating it"
        $existing = $existing -replace '(?m)^BOOKING_API_KEY=.*$', "BOOKING_API_KEY=$(New-Key 'bk-')"
        $repaired = $true
    }

    if ($repaired) {
        [System.IO.File]::WriteAllText(
            (Join-Path $PSScriptRoot ".env"), $existing,
            (New-Object System.Text.UTF8Encoding $false))
        Good "repaired .env with real random keys"
        Say "If any container was already running, it needs a restart to pick them up."
    } else {
        Good ".env already exists - leaving it alone"
    }
} else {
    if (-not (Test-Path ".env.example")) {
        Fail ".env.example is missing. Re-extract the zip."
        exit 1
    }

    $key = New-Key "sk-"
    $bookingKey = New-Key "bk-"

    $env_ = Get-Content ".env.example" -Raw
    $env_ = $env_ -replace 'LITELLM_MASTER_KEY=.*', "LITELLM_MASTER_KEY=$key"
    $env_ = $env_ -replace 'BOOKING_API_KEY=.*', "BOOKING_API_KEY=$bookingKey"
    if (-not $useGpu) {
        $env_ = $env_ -replace 'TTS_DEVICE=cuda.*', 'TTS_DEVICE=cpu'
    }
    # -NoNewline + explicit UTF8 without BOM: a BOM in .env breaks the first
    # variable name in Docker Compose.
    [System.IO.File]::WriteAllText(
        (Join-Path $PSScriptRoot ".env"), $env_,
        (New-Object System.Text.UTF8Encoding $false))
    Good "wrote .env with a generated master key"
}

foreach ($d in @("workspace", "transcripts", "voices", "config", "secrets")) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
}
Good "folders ready"

# The booking service needs its business rules before it will start. Seed them
# from the example so it boots; your mom edits this file, not .env.
if (-not (Test-Path "config\services.yaml")) {
    if (Test-Path "config\services.example.yaml") {
        Copy-Item "config\services.example.yaml" "config\services.yaml"
        Good "created config\services.yaml (edit this for her hours and services)"
    }
} else {
    Good "config\services.yaml already exists - leaving it alone"
}

# --- 4. Build ----------------------------------------------------------------

Step "Building images (first time takes a while - the voice image is large)"

$buildArgs = $composeArgs + @("build")
if ($Rebuild) { $buildArgs += "--no-cache" }
& docker @buildArgs
if ($LASTEXITCODE -ne 0) {
    Fail "Build failed. Scroll up for the reason."
    Say "Most common cause: no internet, or Docker Desktop ran out of disk."
    exit 1
}
Good "images built"

# --- 5. Start ----------------------------------------------------------------

Step "Starting the stack"

# No open-webui. Hermes is the interface -- in the terminal, and the same agent
# over Telegram. It's still defined in docker-compose.yml behind a profile, and
# its chat history is untouched in the open_webui_data volume, so it comes back
# with:  docker compose --profile webui up -d open-webui
& docker @($composeArgs + @("up", "-d", "ollama", "litellm", "hermes", "voice", "booking"))
if ($LASTEXITCODE -ne 0) {
    Fail "Something failed to start. Check Docker Desktop > Containers for the red one."
    exit 1
}
Good "containers up"

# --- 6. Models ---------------------------------------------------------------

if ($SkipModels) {
    Warn "skipping model download (-SkipModels)"
    Say "run this later:  docker compose --profile init up ollama-init"
} else {
    Step "Downloading models (~20GB, this is the slow part - leave it running)"
    Say "You can watch progress here, or in Docker Desktop > Containers > ollama_init > Logs"
    Write-Host ""
    & docker @($composeArgs + @("--profile", "init", "up", "ollama-init"))
    Write-Host ""
    Good "models downloaded"
}

# --- 7. Hermes ---------------------------------------------------------------
#
# The container is up, but "up" and "works" are different things. Prove the
# command exists and that it can actually see the router, here, while you're
# still watching -- not the first time you go to use it.

Step "Checking the Hermes CLI"

$hermesPath = & docker @($composeArgs + @("exec","-T","hermes","sh","-c","command -v hermes")) 2>$null
if ($hermesPath) {
    Good "hermes is installed at $($hermesPath.Trim())"
    $hermesJobs = & docker @($composeArgs + @("exec","-T","hermes","python","-c",
        "from shared.router import Router; print(','.join(Router().available_jobs()))")) 2>&1
    if ("$hermesJobs" -match ",") {
        Good "hermes can reach the router - jobs: $("$hermesJobs".Trim())"
    } else {
        Warn "hermes is installed but can't reach the router yet"
        Say "usually just litellm still starting. Re-check with verify.cmd in a minute."
    }
    Say "Open it any time with  hermes.cmd"
} else {
    Fail "the hermes command isn't in the container"
    Say "docker compose logs hermes"
}

# --- 8. Telegram -------------------------------------------------------------
#
# Start it if it's configured, rather than leaving a note asking you to press a
# button in Docker Desktop later. A step you have to remember is a step that
# doesn't happen.

Step "Telegram bridge"

$envText = Get-Content ".env" -Raw
$tgToken = if ($envText -match '(?m)^TELEGRAM_BOT_TOKEN=(.+)$') { $Matches[1].Trim() } else { "" }
$tgIds   = if ($envText -match '(?m)^TELEGRAM_ALLOWED_USER_IDS=(.+)$') { $Matches[1].Trim() } else { "" }

if (-not $tgToken) {
    Warn "no bot token - skipping (entirely optional)"
    Say "1. Message @BotFather on Telegram, send /newbot, copy the token"
    Say "2. Message @userinfobot, it replies with your numeric id"
    Say "3. Put both in .env as TELEGRAM_BOT_TOKEN and TELEGRAM_ALLOWED_USER_IDS"
    Say "4. Re-run setup.cmd, or just:  docker compose up -d telegram"
} elseif (-not $tgIds) {
    # The bridge refuses to start without an allowlist, and it is right to.
    # A token with no allowlist is a bot anyone who finds it can talk to.
    Fail "TELEGRAM_BOT_TOKEN is set but TELEGRAM_ALLOWED_USER_IDS is empty"
    Say "The bridge won't start without it, on purpose - otherwise anyone who"
    Say "finds the bot can use your machine. Get your id from @userinfobot."
} else {
    # Check the token with Telegram before starting anything. A typo here
    # otherwise shows up as a container that restarts forever.
    try {
        $me = Invoke-RestMethod -Uri "https://api.telegram.org/bot$tgToken/getMe" -TimeoutSec 15 -ErrorAction Stop
        if ($me.ok) { Good "token is valid - the bot is @$($me.result.username)" }
    } catch {
        Fail "Telegram rejected that token"
        Say "check TELEGRAM_BOT_TOKEN in .env against what @BotFather gave you"
    }

    & docker @($composeArgs + @("up","-d","telegram"))
    if ($LASTEXITCODE -eq 0) {
        Start-Sleep -Seconds 4
        $tgLog = & docker @($composeArgs + @("logs","--tail","30","telegram")) 2>&1 | Out-String
        if ($tgLog -match "bridge up") { Good "the bridge is connected - message your bot" }
        elseif ($tgLog -match "Unauthorized") { Fail "Telegram refused the token" }
        else { Warn "started, no confirmation in the logs yet"; Say "docker compose logs -f telegram" }
    } else {
        Fail "the telegram container didn't start"
    }
}

# --- done --------------------------------------------------------------------

Write-Host ""
Write-Host "  Done." -ForegroundColor Green
Write-Host "  ----" -ForegroundColor DarkGray
Say "Hermes      double-click hermes.cmd    <- this is your interface"
Say "On a phone  message your Telegram bot - same agent, same workspace"
Say "Booking     http://localhost:8080/health"
Say "Voice       http://localhost:8091/health"
Say "Everything  Docker Desktop > Containers > ai-stack"
Write-Host ""
Say "Booking is in DRY RUN - it works, but writes nothing to Google Calendar."
Say "When you're ready to connect it for real, follow SETUP-GOOGLE.md."
if (-not $useGpu) {
    Write-Host ""
    Warn "Running in CPU mode. Replies will be slow."
    Say "Consider editing litellm\config.yaml down to llama3.2:3b for every route."
}
Write-Host ""
Say "Open Hermes now with:  hermes.cmd"
Write-Host ""
