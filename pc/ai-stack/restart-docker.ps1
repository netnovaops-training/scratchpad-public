# restart-docker.ps1 -- unfreeze Docker Desktop and keep the GPU free.
#
# WHY THIS EXISTS. Docker Desktop's Linux VM on this PC freezes: `docker ps`
# answers "500 Internal Server Error" and the website goes down. Quitting
# from the tray icon often hangs, because the app is waiting on the very VM
# that is frozen. The cure that works (measured 2026-10-02) is the hard one:
# end the Docker processes, shut WSL down (that is what frees the 10+ GB the
# AI model held on the graphics card), and start Docker Desktop again.
#
# After the engine is back it stops ai-tagger, the photo-tagging job that
# pins the vision model on the GPU around the clock. A stopped container
# stays stopped across restarts until someone runs `docker start ai-tagger`.
#
# Run it:  right-click > Run with PowerShell   (no admin needed)
# Log:     ai-stack\logs\restart-docker.log

$ErrorActionPreference = 'Continue'
$exe = Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\Docker Desktop.exe'
if (-not (Test-Path $exe)) { $exe = 'C:\Program Files\Docker\Docker\Docker Desktop.exe' }
$logDir = Join-Path $PSScriptRoot 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir 'restart-docker.log'

# Pause only when a person double-clicked it; Claude runs it with no console input.
function Pause-IfPerson { if (-not [Console]::IsInputRedirected) { Read-Host 'Press Enter to close' | Out-Null } }

function Say($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    Write-Host $line
    Add-Content -Path $log -Value $line -Encoding utf8
}

function Engine-Ok {
    # docker.exe can hang on a frozen engine, so ask from a job with a timeout.
    $j = Start-Job { docker info --format '{{.ServerVersion}}' 2>&1 }
    $done = Wait-Job $j -Timeout 20
    if (-not $done) { Stop-Job $j; Remove-Job $j -Force; return $false }
    $out = Receive-Job $j; Remove-Job $j -Force
    return ($LASTEXITCODE -eq 0 -and "$out" -match '^\d+\.\d+')
}

function Gpu-Used {
    $v = & nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>$null
    if ($v) { return "$($v.Trim()) MiB" } else { return 'unknown' }
}

Say "start. GPU memory in use: $(Gpu-Used)"

# 1. End every Docker Desktop process. Quit from the tray hangs when frozen.
$names = 'Docker Desktop', 'com.docker.backend', 'com.docker.build', 'docker-agent'
$procs = Get-Process -Name $names -ErrorAction SilentlyContinue
Say "ending $(@($procs).Count) Docker process(es)"
$procs | Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 5

# 2. Shut down WSL. This is the step that releases the GPU memory.
Say 'wsl --shutdown'
wsl --shutdown
Start-Sleep -Seconds 8
Say "GPU memory after shutdown: $(Gpu-Used)"

# 3. Start Docker Desktop and wait up to 5 minutes for the engine.
Say "starting $exe"
Start-Process -FilePath $exe
$deadline = (Get-Date).AddMinutes(5)
$up = $false
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 15
    if (Engine-Ok) { $up = $true; break }
}
if (-not $up) {
    Say 'FAILED: engine did not come back within 5 minutes. Reboot the PC.'
    Pause-IfPerson
    exit 1
}
Say 'engine is up'

# 4. Keep the photo tagger off the GPU.
$state = docker inspect -f '{{.State.Status}}' ai-tagger 2>$null
if ($state -eq 'running') {
    docker stop -t 20 ai-tagger | Out-Null
    Say 'stopped ai-tagger (start it again with: docker start ai-tagger)'
} else {
    Say "ai-tagger is '$state' - left alone"
}

# 5. Report.
$running = @(docker ps -q).Count
Say "done. $running container(s) running. GPU memory in use: $(Gpu-Used)"
Say 'Tip: keep the Docker Desktop WINDOW closed; its stats polling slows the engine.'
Pause-IfPerson
