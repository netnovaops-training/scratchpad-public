# =============================================================================
#  vector.ps1 - ships this PC's Docker container logs and Windows System /
#  Application errors+warnings to the Loki server in .\logging\.
#
#  Reuses the exact Scheduled Task + hidden-.vbs-launcher pattern from
#  hostmetrics.ps1, which is already confirmed working on this machine.
#  Vector is a long-running process rather than something that writes a file
#  every few minutes, so -Install verifies success differently: it asks
#  Loki whether it actually received anything, which is the only proof that
#  matters. A task showing "Running" proves a process started, not that a
#  single log line made the trip.
#
#  USAGE
#    .\vector.ps1 -Install      one time: download, install, register, verify
#    .\vector.ps1 -Status       is it running, and is data actually arriving
#    .\vector.ps1 -Loop         run in the foreground instead (for testing)
#    .\vector.ps1 -Uninstall    stop and remove the scheduled task
# =============================================================================

param(
    [switch]$Install,
    [switch]$Status,
    [switch]$Loop,
    [switch]$Uninstall,
    [string]$VectorVersion = "0.57.0",
    [string]$LokiUrl = "http://127.0.0.1:3100"
)

Set-Location -Path $PSScriptRoot
$ErrorActionPreference = "SilentlyContinue"

# vector.yaml points data_dir here. Vector checks this exists at startup and
# refuses to create it itself - true whether launched by -Install, -Loop, or
# the scheduled task, so it is created unconditionally right here rather than
# once inside any single code path.
New-Item -ItemType Directory -Force -Path "C:\ProgramData\vector" | Out-Null

$TaskName  = "ai-stack vector log shipper"
$Config    = Join-Path $PSScriptRoot "vector\vector.yaml"
$VectorExe = "C:\Program Files\Vector\bin\vector.exe"
$VbsPath   = Join-Path $PSScriptRoot "vector-hidden.vbs"

function Say($m)  { Write-Host $m }
function Warn($m) { Write-Host $m -ForegroundColor Yellow }
function Bad($m)  { Write-Host $m -ForegroundColor Red }
function Good($m) { Write-Host $m -ForegroundColor Green }

# Did Loki actually receive anything from this host in the last few minutes?
# This is the real answer to "is it working" - everything else (task state,
# process existing) only proves a piece of the chain, not the whole thing.
function Test-LokiHasData {
    try {
        $q = 'count_over_time({host="jason-pc"}[10m])'
        $u = "$LokiUrl/loki/api/v1/query?query=$([uri]::EscapeDataString($q))"
        $r = Invoke-RestMethod -Uri $u -TimeoutSec 10 -ErrorAction Stop
        $n = 0
        foreach ($res in $r.data.result) { $n += [int]$res.value[1] }
        return $n
    } catch { return -1 }
}

if ($Status) {
    Write-Host ""
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) {
        Bad "  NOT INSTALLED.  .\vector.ps1 -Install"
        exit 1
    }
    $info = Get-ScheduledTaskInfo -TaskName $TaskName
    $colour = if ($t.State -in @('Ready', 'Running')) { 'Green' } else { 'Red' }
    Write-Host ("  task        {0}" -f $t.State) -ForegroundColor $colour
    Write-Host ("  last run    {0}  (result {1})" -f $info.LastRunTime, $info.LastTaskResult)

    if (Test-Path $VbsPath) { Good "  launcher    present" }
    else { Bad "  launcher    MISSING - Windows will pop an error box on every run. .\vector.ps1 -Install" }

    $n = Test-LokiHasData
    if ($n -lt 0)      { Bad  "  Loki        not reachable at $LokiUrl - is .\logging\docker-compose.yml running?" }
    elseif ($n -eq 0)  { Warn "  Loki        reachable, but zero lines from this host in the last 10 minutes" }
    else               { Good ("  Loki        {0} log lines from this host in the last 10 minutes" -f $n) }
    Write-Host ""
    exit 0
}

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item $VbsPath -ErrorAction SilentlyContinue
    Good "  removed the scheduled task. Vector itself (Program Files\Vector) was left in place -"
    Say  "  rerun the MSI uninstaller from Windows Settings > Apps if you want that gone too."
    exit 0
}

if ($Loop) {
    if (-not (Test-Path $VectorExe)) { Bad "  Vector is not installed.  .\vector.ps1 -Install"; exit 1 }
    Write-Host "  Running in the foreground. Ctrl+C to stop. Watch for lines about" -ForegroundColor Cyan
    Write-Host "  'sink' errors, which mean it cannot reach Loki." -ForegroundColor Cyan
    & $VectorExe --config $Config
    exit 0
}

if (-not $Install) {
    Say "Usage: .\vector.ps1 -Install | -Status | -Loop | -Uninstall"
    exit 0
}

Write-Host ""
Write-Host "Installing Vector" -ForegroundColor Cyan
Write-Host ("-" * 60) -ForegroundColor DarkGray

if (-not (Test-Path $Config)) {
    Bad "  Missing $Config - that defines what to collect and where to send it."
    exit 1
}

# --- elevation, checked BEFORE downloading anything --------------------------
# The MSI installs to C:\Program Files, which needs an elevated session. Run
# unelevated, msiexec does not pop a UAC prompt and ask for one - it just
# fails with exit code 1603, a generic "something went wrong" that gives no
# hint this was the cause. Checking first turns that into one clear line
# instead of a failed install and a guessing game.
$me = New-Object Security.Principal.WindowsPrincipal(
          [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Bad "  This needs an Administrator PowerShell."
    Say "  Right-click PowerShell > Run as administrator, then:"
    Say "    cd `"$PSScriptRoot`""
    Say "    .\vector.ps1 -Install"
    exit 1
}
Good "  running elevated"

# --- Docker reachability, checked before anything else ----------------------
# docker_logs needs the same named-pipe access `docker` itself uses. If
# ordinary `docker` commands work for you (they do, you have been running
# them all night), this works too - nothing extra to grant.
& docker version > $null 2>&1
if ($LASTEXITCODE -ne 0) {
    Warn "  Docker is not responding right now. Container logs will be empty until it is -"
    Warn "  Windows events will still ship fine either way."
}

# --- install, if it is not already there -------------------------------------
if (Test-Path $VectorExe) {
    Good "  Vector already installed"
} else {
    $msi = Join-Path $env:TEMP "vector-$VectorVersion-x64.msi"
    $url = "https://packages.timber.io/vector/$VectorVersion/vector-x64.msi"
    Say "  downloading $url"
    try {
        Invoke-WebRequest -Uri $url -OutFile $msi -ErrorAction Stop
    } catch {
        Bad "  download failed: $_"
        Say "  check the version exists at https://vector.dev/download/ and pass -VectorVersion"
        exit 1
    }
    Say "  installing (silent)"
    # /l*v writes a full log. 1603 alone means nothing beyond "it failed" -
    # the log has the actual reason, and there is no other way to get it out
    # of a /quiet install.
    $msiLog = Join-Path $env:TEMP "vector-install.log"
    $p = Start-Process msiexec.exe -ArgumentList "/i `"$msi`" /quiet /norestart /l*v `"$msiLog`"" -Wait -PassThru
    if ($p.ExitCode -ne 0 -or -not (Test-Path $VectorExe)) {
        Bad "  install failed (msiexec exit code $($p.ExitCode))"
        # The line with "Return Value 3" marks the actual failure in an MSI
        # log; everything before it is routine. Surfacing just that instead
        # of dumping the whole (often thousands-of-lines) log.
        $reason = Select-String -Path $msiLog -Pattern "Return Value 3" -Context 3, 0 -ErrorAction SilentlyContinue |
                  Select-Object -Last 1
        if ($reason) {
            Say "  from the install log:"
            $reason.Context.PreContext + $reason.Line | ForEach-Object { Say "    $_" }
        }
        Say "  full log: $msiLog"
        exit 1
    }
    Good "  installed to $VectorExe"
}

# --- the scheduled task, same hidden-launcher trick as hostmetrics.ps1 ------
# Task Scheduler starts a visible console FIRST regardless of any flag Vector
# itself is given, so a .vbs launcher running WScript.Shell.Run(cmd, 0, False)
# is what actually suppresses the window - not a Vector command-line flag.
$taskArgs = '--config "{0}"' -f $Config
$inner = ('"{0}" {1}' -f $VectorExe, $taskArgs) -replace '"', '""'
@(
    "' Generated by vector.ps1 -Install. Safe to delete; -Install rewrites it.",
    "Set sh = CreateObject(""WScript.Shell"")",
    ("sh.Run ""{0}"", 0, False" -f $inner)
) | Set-Content -Path $VbsPath -Encoding ASCII

$wscript = "$env:SystemRoot\System32\wscript.exe"
$action = New-ScheduledTaskAction -Execute $wscript -Argument ('"{0}"' -f $VbsPath) `
              -WorkingDirectory $PSScriptRoot

# ONE trigger here, not two - Vector is a long-running process, not something
# that needs to be re-poked on a timer. AtLogOn is enough; -StartWhenAvailable
# below covers the case where you were logged off when the PC rebooted.
$trigger  = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Say "  removing the previous task first"
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

try {
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
        -Description "Ships Docker container logs and Windows System/Application errors+warnings to Loki." `
        -ErrorAction Stop | Out-Null
} catch {
    Bad "  could not register the task: $_"
    exit 1
}

Start-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

Say ""
Say "  registered and started. Waiting up to 60s for Loki to confirm it received something -"
Say "  the first Windows events can take a few seconds to arrive after Vector starts."
$ok = $false
for ($i = 0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 5
    if ((Test-LokiHasData) -gt 0) { $ok = $true; break }
}
Say ""

if ($ok) {
    Good "  CONFIRMED WORKING - Loki has log lines from this host."
    Say  "  Dashboard:  http://127.0.0.1:3001  (Explore > Loki > {host=`"jason-pc`"})"
    exit 0
}

Bad "  Loki has not received anything yet."
Say ""
Say "  Most likely one of:"
Say "    1. .\logging\docker-compose.yml is not running yet:"
Say "         cd logging; docker compose up -d; cd .."
Say "    2. Vector started but hit an error. Run it in the foreground to see it:"
Say "         .\vector.ps1 -Loop"
Say "    3. Nothing has logged anything in the last 10 minutes yet - genuinely possible"
Say "       on a quiet machine. .\vector.ps1 -Status again in a few minutes."
