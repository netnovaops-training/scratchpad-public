<#
    What runs on its own, and what still needs you.

    THE SHORT VERSION
    Most of this stack is already automatic and has been all along. Every
    container is set `restart: unless-stopped`, which means Docker brings it
    back after a crash, after a reboot, after anything -- with one condition:
    Docker Desktop itself has to be running. That single condition is the
    thing most likely to be switched off, and it is the reason a PC can come
    back from a power cut with the phone line dead.

    This script checks what is genuinely automatic, tells you plainly what
    is not, and offers to fix the one recurring job that isn't scheduled yet.

    It changes nothing without asking.
#>

param(
    [switch]$Install,     # actually register the scheduled tasks
    [switch]$Uninstall    # remove them again
)

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Ok   { param($m,$d="") Write-Host "  [ok]   $m" -ForegroundColor Green;  if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") Write-Host "  [FAIL] $m" -ForegroundColor Red;    if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") Write-Host "  [warn] $m" -ForegroundColor Yellow; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Head { param($m) Write-Host ""; Write-Host "== $m" -ForegroundColor Cyan }

Write-Host ""
Write-Host "  What runs automatically" -ForegroundColor Cyan
Write-Host "  -----------------------" -ForegroundColor DarkGray

# --- 1. the containers ----------------------------------------------------
Head "1. The stack itself"
$policies = & docker inspect --format "{{.Name}} {{.HostConfig.RestartPolicy.Name}}" $(docker ps -aq) 2>$null
$notAuto = @()
foreach ($line in $policies) {
    if (-not $line) { continue }
    $parts = $line -split ' '
    $name = $parts[0].TrimStart('/')
    $pol  = $parts[1]
    # ollama_init is a one-shot by design: it pulls models and exits 0.
    if ($name -eq "ollama_init") { continue }
    if ($pol -ne "unless-stopped" -and $pol -ne "always") { $notAuto += "$name ($pol)" }
}
if ($notAuto.Count -eq 0) {
    Ok "every container restarts by itself" "after a crash, and after a reboot"
} else {
    Warn "these will NOT come back on their own" ($notAuto -join ", ")
}

# --- 2. the one condition -------------------------------------------------
Head "2. Docker Desktop starting with Windows"
Write-Host "  This is the single point of failure for everything above." -ForegroundColor DarkGray
Write-Host "  If Docker Desktop does not start, nothing restarts - including" -ForegroundColor DarkGray
Write-Host "  the phone line." -ForegroundColor DarkGray
Write-Host ""

$autoStart = $false
$runKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
try {
    $v = Get-ItemProperty -Path $runKey -ErrorAction Stop
    if ($v.PSObject.Properties.Name -match "Docker Desktop") { $autoStart = $true }
} catch { }

# Docker Desktop also keeps its own setting; check both rather than trusting one.
$settings = Join-Path $env:APPDATA "Docker\settings-store.json"
if (-not (Test-Path $settings)) { $settings = Join-Path $env:APPDATA "Docker\settings.json" }
if (Test-Path $settings) {
    try {
        $j = Get-Content $settings -Raw | ConvertFrom-Json
        if ($j.PSObject.Properties.Name -contains "OpenUIOnStartupDisabled" -or
            $j.PSObject.Properties.Name -contains "AutoStart") {
            if ($j.AutoStart -eq $true) { $autoStart = $true }
        }
        if ($j.PSObject.Properties.Name -contains "StartOnLogin" -and $j.StartOnLogin -eq $true) {
            $autoStart = $true
        }
    } catch { }
}

if ($autoStart) {
    Ok "Docker Desktop is set to start when you log in"
} else {
    Warn "could not confirm Docker Desktop starts on login" "check it by hand - it matters more than anything else here"
    Write-Host ""
    Write-Host "    Docker Desktop -> Settings (gear) -> General" -ForegroundColor Gray
    Write-Host "    tick 'Start Docker Desktop when you log in'" -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Not changed automatically on purpose: it is Docker's own setting," -ForegroundColor DarkGray
    Write-Host "  and editing its config file behind its back tends to get" -ForegroundColor DarkGray
    Write-Host "  overwritten the next time the app saves." -ForegroundColor DarkGray
}

# --- 3. things already on a timer ----------------------------------------
Head "3. Already on a schedule"
Ok "n8n heartbeat" "every 5 minutes - texts you only when something changes"
Ok "certificate renewal" "Caddy renews on its own, continuously. No cron, no certbot."
Ok "log rotation" "Docker caps each container's logs at 10MB x 3"

# --- 4. the recurring job that is NOT scheduled ---------------------------
Head "4. Weekly certificate check"
$TASK = "ai-stack certwatch"
$existing = Get-ScheduledTask -TaskName $TASK -ErrorAction SilentlyContinue

if ($Uninstall) {
    if ($existing) {
        & "$PSScriptRoot\certwatch.ps1" -Uninstall
    } else { Ok "nothing to remove" }
}
elseif ($existing) {
    $next = (Get-ScheduledTaskInfo -TaskName $TASK -ErrorAction SilentlyContinue).NextRunTime
    Ok "installed" "next run: $next"
}
elseif ($Install) {
    Write-Host "  installing..." -ForegroundColor DarkGray
    & "$PSScriptRoot\certwatch.ps1" -Install
}
else {
    Warn "not installed" "Caddy renews certificates by itself, so this is a safety net, not the renewal"
    Write-Host "         it warns you on Telegram if a certificate is somehow NOT renewing" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host "    To install it:   autostart.cmd -Install   (as Administrator)" -ForegroundColor Gray
}

# --- 5. deliberately manual ----------------------------------------------
Head "5. Deliberately NOT automatic"
Write-Host @"
  update.cmd      Rebuilds images and recreates containers. Manual on purpose.
                  Watchtower used to do this unattended and was removed after
                  crash-looping 1,286 times. An unattended update that breaks
                  your mum's phone line at 3am is a bad trade for a patch you
                  could apply on a Tuesday afternoon.

  websites.cmd    Stages certificate issuance. Let's Encrypt allows 5 real
                  certificates per domain per week and FAILURES COUNT, so this
                  must never run on a loop.

  backups         Not scheduled. Worth doing by hand before big changes:
                  docker run --rm -v ai-stack_caddy_data:/d ``
                    -v "C:\Jasons New Site\www\ai-stack:/b" ``
                    alpine tar czf /b/caddy-certs-backup.tgz -C /d .
"@ -ForegroundColor Gray

Head "Summary"
Write-Host "  After a reboot, everything comes back BY ITSELF -- provided" -ForegroundColor Gray
Write-Host "  Docker Desktop starts with Windows. That is the only switch" -ForegroundColor Gray
Write-Host "  that really matters. Check section 2 above." -ForegroundColor Gray

Write-Host ""
Read-Host "  Press Enter to close"
