<#
    The numbers only Windows can see.

    WHY THIS EXISTS
    Containers can tell you a great deal -- GPU load, VRAM, per-container CPU
    and memory, what's down. They cannot tell you four things that matter:

        * how much space is left on C:      Docker sees its own disk image,
                                            not your drive
        * CPU temperature                   WSL2 does not pass host sensors
                                            through to containers
        * how long WINDOWS has been up      a container only knows how long
                                            the WSL VM has been up
        * whether the internet is healthy   measurable from a container, but
                                            the real NIC is out here

    So this runs on Windows, writes one small JSON file, and the stack reads
    it. transcripts\ is chosen because several containers already mount it --
    no new share, no new port, no service listening anywhere.

    Run it as often as you like; it is read-only apart from that one file.
#>

param(
    [string]$OutFile = "$PSScriptRoot\transcripts\host-metrics.json",
    [switch]$Quiet,
    # Keep writing every $Every seconds until you close the window. This is
    # what makes the network numbers real: throughput is a DELTA between two
    # samples, so a single run can only ever report null for it.
    [switch]$Loop,
    [int]$Every = 120,
    # Register a Scheduled Task so this keeps running after a reboot without
    # a window open. No admin needed - it runs as you, at logon.
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status
)

$ErrorActionPreference = "SilentlyContinue"
Set-Location -Path $PSScriptRoot

$TaskName = "ai-stack host metrics"

# Same four helpers printserver.ps1 has. -Quiet has to silence the routine
# output while ALWAYS letting failures through: a scheduled task runs hidden,
# so anything it swallows is lost for good.
function Say($m)  { if (-not $Quiet) { Write-Host $m } }
function Warn($m) { if (-not $Quiet) { Write-Host $m -ForegroundColor Yellow } }
function Bad($m)  { Write-Host $m -ForegroundColor Red }
function Good($m) { if (-not $Quiet) { Write-Host $m -ForegroundColor Green } }

if ($Status) {
    # Answers the only question that matters here: is this still running, and
    # if not, since when. Both halves are needed -- the task can exist, be
    # enabled, and still not have run for a week.
    Write-Host ""
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) {
        Write-Host "  NOT INSTALLED - nothing is collecting the Windows metrics." -ForegroundColor Red
        Write-Host "  Install it:  hostmetrics.cmd -Install"
    } else {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        $colour = if ($t.State -eq 'Ready' -or $t.State -eq 'Running') { 'Green' } else { 'Red' }
        Write-Host ("  task        {0}" -f $t.State) -ForegroundColor $colour
        Write-Host ("  last run    {0}  (result {1})" -f $info.LastRunTime, $info.LastTaskResult)
        Write-Host ("  next run    {0}" -f $info.NextRunTime)
        if ($info.LastTaskResult -ne 0) {
            Write-Host "  last run did NOT succeed - result 0 means success." -ForegroundColor Yellow
        }
    }

    # The launcher file the task points at. If this is missing, Windows
    # throws a MODAL DIALOG ("Can not find script file...") on every single
    # run -- which is far worse than the flashing console it was added to
    # prevent. Checking it here turns a recurring popup into one line.
    $vbsPath = Join-Path $PSScriptRoot "hostmetrics-hidden.vbs"
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        if (Test-Path $vbsPath) {
            Write-Host "  launcher    present" -ForegroundColor Green
        } else {
            Write-Host "  launcher    MISSING - $vbsPath" -ForegroundColor Red
            Write-Host "              Windows will pop up an error box on every run." -ForegroundColor Yellow
            Write-Host "              Fix:  hostmetrics.cmd -Install    (rewrites it)" -ForegroundColor Yellow
            Write-Host "              Or remove it: hostmetrics.cmd -Uninstall" -ForegroundColor Yellow
        }
    }

    # The file is the real proof. A healthy task with an unwritable output
    # folder looks perfect above and delivers nothing.
    if (Test-Path $OutFile) {
        $age = [math]::Round(((Get-Date) - (Get-Item $OutFile).LastWriteTime).TotalMinutes, 1)
        $c = if ($age -le 5) { 'Green' } elseif ($age -le 15) { 'Yellow' } else { 'Red' }
        Write-Host ("  data file   {0} minutes old" -f $age) -ForegroundColor $c
        Write-Host ("              {0}" -f $OutFile) -ForegroundColor DarkGray
        if ($age -gt 15) { Write-Host "  The stack treats anything over 15 minutes as not current." -ForegroundColor Yellow }
    } else {
        Write-Host "  data file   MISSING - it has never written one." -ForegroundColor Red
    }
    Write-Host ""
    return
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $PSScriptRoot "hostmetrics-hidden.vbs") -ErrorAction SilentlyContinue
    Write-Host "  removed scheduled task '$TaskName'" -ForegroundColor Yellow
    return
}

if ($Install) {
    # Errors visible in HERE, even though the sampling path suppresses them.
    # SilentlyContinue is right for a scheduled task that must survive a WMI
    # hiccup, and wrong for an installer -- it hid the reason the task never
    # ran.
    $ErrorActionPreference = "Continue"

    # Runs as YOU, not SYSTEM. Deliberate: SYSTEM has no mapped drives and a
    # different network profile, so the Wi-Fi and drive numbers it collects
    # would not be the ones you see. It also means no admin prompt.
    $ps = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
    if (-not $ps) { $ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" }

    $taskArgs = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}\hostmetrics.ps1" -Quiet' -f $PSScriptRoot
    # --- stop the console window flashing every interval -------------------
    # `powershell.exe -WindowStyle Hidden` does NOT prevent this. Task
    # Scheduler starts a console host FIRST, and only then does PowerShell
    # read -WindowStyle -- so you get a black window that appears and
    # vanishes on every single run. On a 60-second timer that is a window
    # popping up all day, which is unusable.
    #
    # A tiny .vbs launcher with WScript.Shell.Run(..., 0, False) starts the
    # process with no window AT ALL, and keeps the task in your interactive
    # session (which the print path needs -- Windows' "print" verb wants a
    # shell). The command is baked in at install time so there is no runtime
    # quoting to get wrong.
    $vbs = Join-Path $PSScriptRoot "hostmetrics-hidden.vbs"
    $wscript = "$env:SystemRoot\System32\wscript.exe"
    $inner = ('"{0}" {1}' -f $ps, $taskArgs) -replace '"', '""'
    @(
        "' Generated by hostmetrics.ps1 -Install. Safe to delete; -Install rewrites it.",
        "' Runs the command with NO console window (the 0 below is the window style).",
        "Set sh = CreateObject(""WScript.Shell"")",
        ("sh.Run ""{0}"", 0, False" -f $inner)
    ) | Set-Content -Path $vbs -Encoding ASCII

    if (Test-Path $wscript) {
        $action = New-ScheduledTaskAction -Execute $wscript `
            -Argument ('"{0}"' -f $vbs) -WorkingDirectory $PSScriptRoot
    } else {
        Warn "  wscript.exe not found - falling back to a visible window."
        $action = New-ScheduledTaskAction -Execute $ps -Argument $taskArgs -WorkingDirectory $PSScriptRoot
    }

    # TWO triggers. A repetition bolted onto an AtLogOn trigger is frequently
    # dropped by Task Scheduler, leaving a task that reports Ready and runs
    # exactly once at logon.
    # Task Scheduler's minimum repetition interval is ONE MINUTE - it rejects
    # the whole task rather than rounding up. 120 is fine; this guard is for
    # anyone who passes -Every 30.
    $taskEvery = $Every
    if ($taskEvery -lt 60) {
        Warn "  Task Scheduler's minimum interval is 60s - using 60 instead of $Every."
        $taskEvery = 60
    }
    $repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(15) `
        -RepetitionInterval (New-TimeSpan -Seconds $taskEvery) `
        -RepetitionDuration (New-TimeSpan -Days 3650)
    $atLogon = New-ScheduledTaskTrigger -AtLogOn
    $trigger = @($repeat, $atLogon)

    $settings = New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -MultipleInstances IgnoreNew `
        -ExecutionTimeLimit (New-TimeSpan -Minutes 5)

    # Remove any previous version first. IgnoreNew means a task stuck in a
    # Running state silently swallows every start request.
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Say "  removing the previous task first (a stuck one blocks new runs)"
        try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }

    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Description `
            "Writes the Windows-only metrics (C: space, real NIC throughput, latency, uptime) that Docker containers cannot see." `
            -ErrorAction Stop | Out-Null
    } catch {
        Bad "  could not register the task: $_"
        Say "  run it in a window instead:  hostmetrics.cmd -Loop"
        return
    }

    $before = if (Test-Path $OutFile) { (Get-Item $OutFile).LastWriteTime } else { [datetime]::MinValue }
    try { Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
    catch { Bad "  Start-ScheduledTask failed: $_" }

    Say ""
    Say "  registered - waiting up to 45s to confirm it really runs..."
    $ok = $false
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        if ((Test-Path $OutFile) -and (Get-Item $OutFile).LastWriteTime -gt $before) { $ok = $true; break }
    }
    Say ""

    if ($ok) {
        Good "  CONFIRMED WORKING - it wrote its output file"
        Say  "  '$TaskName' - every $taskEvery seconds, and again at every logon"
        Say  "  remove it with:  hostmetrics.cmd -Uninstall"
        Say  ""
        return
    }

    Bad "  REGISTERED BUT IT DID NOT RUN."
    Say ""
    $t    = Get-ScheduledTask     -TaskName $TaskName -ErrorAction SilentlyContinue
    $info = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($t)    { Say ("  state       {0}" -f $t.State) }
    if ($info) {
        Say ("  last run    {0}" -f $info.LastRunTime)
        Say ("  result      {0}   (0 = success)" -f $info.LastTaskResult)
        switch ($info.LastTaskResult) {
            267011     { Warn "  267011 = the task has never been started." }
            267009     { Warn "  267009 = the task is currently running." }
            2147942401 { Warn "  Access denied launching powershell.exe." }
            2147942402 { Warn "  The system cannot find the file specified." }
        }
    } else {
        Warn "  Windows will not report any info for this task at all."
    }

    Say ""
    Say "  Running the task's own command directly to see what it does:"
    Say ""
    $before2 = if (Test-Path $OutFile) { (Get-Item $OutFile).LastWriteTime } else { [datetime]::MinValue }
    try {
        $out = & $ps -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\hostmetrics.ps1" -Quiet 2>&1
        $out | Select-Object -First 20 | ForEach-Object { Say ("     " + $_) }
    } catch { Bad ("     it threw: " + $_) }
    $after2 = if (Test-Path $OutFile) { (Get-Item $OutFile).LastWriteTime } else { [datetime]::MinValue }

    Say ""
    if ($after2 -gt $before2) {
        Good "  The script itself WORKS - only the scheduled task does not."
        Say  "    1. hostmetrics.cmd -Loop      leave a window open; always works"
        Say  "    2. Task Scheduler -> 'ai-stack host metrics' -> Run, read History"
    } else {
        Bad  "  It fails when run directly too - the output above is the reason."
    }
    Say ""
    return
}

if ($Loop) {
    Write-Host ""
    Write-Host "  Sampling every $Every seconds. Leave this window open; Ctrl+C to stop." -ForegroundColor Cyan
    Write-Host "  To have it survive a reboot instead:  hostmetrics.cmd -Install" -ForegroundColor DarkGray
    Write-Host ""
    while ($true) {
        & $PSCommandPath -OutFile $OutFile -Quiet
        Write-Host ("  {0}  wrote {1}" -f (Get-Date -Format "HH:mm:ss"), (Split-Path $OutFile -Leaf)) -ForegroundColor DarkGray
        Start-Sleep -Seconds $Every
    }
}

function Round1($n) { if ($null -eq $n) { return $null } [math]::Round([double]$n, 1) }

$now = Get-Date

# --- previous sample, so network throughput can be a RATE not a total ------
$prev = $null
if (Test-Path $OutFile) {
    try { $prev = Get-Content $OutFile -Raw | ConvertFrom-Json } catch { }
}

# --- uptime + memory ------------------------------------------------------
$os = Get-CimInstance Win32_OperatingSystem
$uptimeHours = $null
if ($os -and $os.LastBootUpTime) {
    $uptimeHours = Round1 (($now - $os.LastBootUpTime).TotalHours)
}
$ramTotalGb = $null; $ramUsedGb = $null; $ramPct = $null
if ($os -and $os.TotalVisibleMemorySize) {
    $ramTotalGb = Round1 ($os.TotalVisibleMemorySize / 1MB)   # value is in KB
    $freeGb     = $os.FreePhysicalMemory / 1MB
    $ramUsedGb  = Round1 ($ramTotalGb - $freeGb)
    if ($ramTotalGb -gt 0) { $ramPct = [math]::Round(100 * $ramUsedGb / $ramTotalGb) }
}

# --- cpu load -------------------------------------------------------------
$cpuPct = (Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average
if ($null -ne $cpuPct) { $cpuPct = [math]::Round($cpuPct) }

# --- cpu temperature ------------------------------------------------------
# Most desktop motherboards do NOT implement the ACPI thermal zone this reads,
# so null here is the normal, expected answer rather than a fault. Reporting
# null honestly beats inventing a number; a real reading needs something like
# LibreHardwareMonitor installed, which is not worth a background service for
# a machine that already reports GPU temperature.
$cpuTempC = $null
try {
    $tz = Get-CimInstance -Namespace "root/wmi" -ClassName MSAcpi_ThermalZoneTemperature -ErrorAction Stop |
          Select-Object -First 1
    if ($tz -and $tz.CurrentTemperature) {
        # tenths of a Kelvin
        $cpuTempC = Round1 (($tz.CurrentTemperature / 10) - 273.15)
        if ($cpuTempC -le 0 -or $cpuTempC -gt 150) { $cpuTempC = $null }
    }
} catch { $cpuTempC = $null }

# --- drives ---------------------------------------------------------------
$drives = @()
foreach ($d in Get-CimInstance Win32_LogicalDisk -Filter "DriveType=3") {
    if (-not $d.Size) { continue }
    $totalGb = Round1 ($d.Size / 1GB)
    $freeGb  = Round1 ($d.FreeSpace / 1GB)
    $drives += [pscustomobject]@{
        name     = $d.DeviceID.TrimEnd(':')
        total_gb = $totalGb
        free_gb  = $freeGb
        pct_used = [math]::Round(100 * ($d.Size - $d.FreeSpace) / $d.Size)
        machine  = "SuperNova"
        state    = "ok"
    }
}

# --- nuc drives, including the external vault ------------------------------
#
# /home showed SuperNova's internal disks and nothing else, so the
# external on the NUC -- which holds every backup and the photo library --
# was invisible on the one page meant to show storage.
#
# ASKED OVER SSH, not collected here and not a new service on the NUC. The
# key already exists (it is how the NUC is administered) and the remote
# end is ~/nuc-drives, which decides for itself whether the vault is
# genuinely mounted before measuring it -- a question only the machine
# holding the disk can answer. See that script for the trap.
#
# BatchMode means a missing or passphrase-locked key fails in two seconds
# instead of hanging a 60-second timer forever waiting on a prompt that
# nobody will ever see.
try {
    $sshExe = (Get-Command ssh.exe -ErrorAction SilentlyContinue).Source
    if ($sshExe) {
        $nucHost = $env:NUC_SSH
        if (-not $nucHost) { $nucHost = "server@192.168.1.86" }
        $lines = & $sshExe -o BatchMode=yes -o ConnectTimeout=5 `
                           -o StrictHostKeyChecking=accept-new `
                           $nucHost "./nuc-drives" 2>$null
        foreach ($ln in $lines) {
            $f = "$ln".Split("|")
            if ($f.Count -lt 4) { continue }
            $totalB = 0L; $availB = 0L
            [void][int64]::TryParse($f[1], [ref]$totalB)
            [void][int64]::TryParse($f[2], [ref]$availB)
            # A LOCKED VAULT IS REPORTED, NOT HIDDEN. It reads as 0 of 0,
            # which on a bar looks like plenty of room -- so the state
            # goes through to the page and the page says the word.
            $pct = 0
            if ($totalB -gt 0) {
                $pct = [math]::Round(100 * ($totalB - $availB) / $totalB)
            }
            $drives += [pscustomobject]@{
                name     = $f[0]
                total_gb = Round1 ($totalB / 1GB)
                free_gb  = Round1 ($availB / 1GB)
                pct_used = $pct
                machine  = "NUC"
                state    = $f[3]
            }
        }
    }
} catch {
    # The NUC being unreachable is not a reason to lose SuperNova's rows.
}

# --- network: link speed, throughput, and Wi-Fi signal ---------------------
$net = @()
foreach ($a in Get-NetAdapter | Where-Object { $_.Status -eq 'Up' -and -not $_.Virtual }) {
    $st = Get-NetAdapterStatistics -Name $a.Name -ErrorAction SilentlyContinue
    if (-not $st) { continue }

    # LinkSpeed is a STRING WITH A UNIT: "1.2 Gbps", "144.4 Mbps", "100 Mbps".
    # Splitting on the space and taking the number turned 1.2 Gbps into 1 Mbps
    # -- wrong by a factor of a thousand, and it looks like a dying link. Wi-Fi
    # is exactly where this bites, because modern cards negotiate in Gbps.
    $linkMbps = $null
    if ($a.LinkSpeed -match '^\s*([\d.]+)\s*(\w+)') {
        $n = [double]$Matches[1]
        switch -Regex ($Matches[2]) {
            '^Gbps' { $linkMbps = [math]::Round($n * 1000) }
            '^Mbps' { $linkMbps = [math]::Round($n) }
            '^Kbps' { $linkMbps = [math]::Round($n / 1000, 2) }
            default { $linkMbps = [math]::Round($n) }
        }
    }

    $rxMbps = $null; $txMbps = $null
    if ($prev -and $prev.net) {
        $old = $prev.net | Where-Object { $_.name -eq $a.Name } | Select-Object -First 1
        if ($old -and $old.rx_bytes -and $prev.ts) {
            $secs = ($now - [datetime]$prev.ts).TotalSeconds
            if ($secs -gt 0) {
                # *8 for bits, /1e6 for megabits. Counters reset on reboot, so a
                # negative delta means "reset", not "negative traffic".
                $dRx = $st.ReceivedBytes - $old.rx_bytes
                $dTx = $st.SentBytes - $old.tx_bytes
                if ($dRx -ge 0) { $rxMbps = Round1 ($dRx * 8 / 1e6 / $secs) }
                if ($dTx -ge 0) { $txMbps = Round1 ($dTx * 8 / 1e6 / $secs) }
            }
        }
    }

    $net += [pscustomobject]@{
        name       = $a.Name
        wireless   = ($a.PhysicalMediaType -match 'Native 802.11|Wireless')
        link_mbps  = $linkMbps
        rx_mbps    = $rxMbps
        tx_mbps    = $txMbps
        rx_bytes   = $st.ReceivedBytes
        tx_bytes   = $st.SentBytes
    }
}

# --- Wi-Fi signal ---------------------------------------------------------
# On Wi-Fi, signal strength predicts trouble far earlier than throughput does:
# the link renegotiates down and packets start retrying long before a speed
# test looks bad. Worth knowing on a machine that answers a phone line, since
# a weak signal shows up as choppy audio rather than an obvious outage.
#
# netsh output is localised, so every field is optional and a parse miss just
# leaves that value null rather than failing the whole collection.
$wifi = $null
try {
    $raw = (netsh wlan show interfaces 2>$null | Out-String)
    if ($raw -and $raw -match '(?m)^\s*Signal\s*:\s*(\d+)%') {
        $wifi = [pscustomobject]@{
            ssid       = $(if ($raw -match '(?m)^\s*SSID\s*:\s*(.+?)\s*$') { $Matches[1] } else { $null })
            signal_pct = [int]$($(if ($raw -match '(?m)^\s*Signal\s*:\s*(\d+)%') { $Matches[1] } else { 0 }))
            radio      = $(if ($raw -match '(?m)^\s*Radio type\s*:\s*(.+?)\s*$') { $Matches[1] } else { $null })
            channel    = $(if ($raw -match '(?m)^\s*Channel\s*:\s*(\d+)') { [int]$Matches[1] } else { $null })
            rx_rate    = $(if ($raw -match '(?m)^\s*Receive rate \(Mbps\)\s*:\s*([\d.]+)') { [double]$Matches[1] } else { $null })
            tx_rate    = $(if ($raw -match '(?m)^\s*Transmit rate \(Mbps\)\s*:\s*([\d.]+)') { [double]$Matches[1] } else { $null })
        }
    }
} catch { $wifi = $null }

# --- internet health: latency, not bandwidth ------------------------------
# Deliberately NOT a speed test. A real one saturates the line for several
# seconds, and doing that every few minutes on the connection your mum's
# phone line runs over would cause the very problem it claims to measure.
# Latency and packet loss are what actually predict a bad call anyway.
$latency = @{}
$online = $false
foreach ($host_ in @('1.1.1.1', '8.8.8.8')) {
    try {
        $r = Test-Connection -ComputerName $host_ -Count 2 -ErrorAction Stop
        $ms = ($r | Measure-Object -Property ResponseTime -Average).Average
        if ($null -ne $ms) { $latency[$host_] = [math]::Round($ms); $online = $true }
    } catch {
        $latency[$host_] = $null
    }
}

# --- gpu (added 2026-08-31 for the gauges page) -----------------------------
# nvidia-smi lives on the HOST, which is exactly where this script runs --
# the one place that can see real VRAM, GPU load and GPU temperature.
$gpu = $null
try {
    $q = & nvidia-smi --query-gpu=utilization.gpu,memory.used,memory.total,temperature.gpu,name,power.draw,clocks.sm,fan.speed --format=csv,noheader,nounits 2>$null
    if ($q) {
        $f = ($q | Select-Object -First 1) -split ",\s*"
        $gpu = [pscustomobject]@{
            name        = $f[4]
            util_pct    = [int]$f[0]
            vram_used_mb  = [int]$f[1]
            vram_total_mb = [int]$f[2]
            temp_c      = [int]$f[3]
            # T0056 2026-11-23: power/clock/fan added so n8n's stats can read the
            # GPU from this file instead of exec-ing into the ollama container.
            # A laptop-style "[N/A]" reading becomes $null instead of failing the cast.
            power_w     = $(if ($f[5] -match '^[\d.]+$') { [double]$f[5] } else { $null })
            clock_mhz   = $(if ($f[6] -match '^\d+$') { [int]$f[6] } else { $null })
            fan_pct     = $(if ($f[7] -match '^\d+$') { [int]$f[7] } else { $null })
        }
    }
} catch { $gpu = $null }

# --- write ----------------------------------------------------------------
$out = [pscustomobject]@{
    gpu           = $gpu
    ts            = $now.ToString("o")
    uptime_hours  = $uptimeHours
    cpu_pct       = $cpuPct
    cpu_temp_c    = $cpuTempC
    ram_total_gb  = $ramTotalGb
    ram_used_gb   = $ramUsedGb
    ram_pct       = $ramPct
    drives        = $drives
    net           = $net
    wifi          = $wifi
    latency_ms    = $latency
    online        = $online
}

$dir = Split-Path $OutFile -Parent
if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
# NOT `Set-Content -Encoding UTF8`. In Windows PowerShell 5.1 that
# writes a BYTE ORDER MARK, and Python's json.loads rejects a BOM
# outright: "Unexpected UTF-8 BOM (decode using utf-8-sig)". The file
# looks perfect in Notepad and is unreadable to every consumer.
# UTF8Encoding($false) is UTF-8 with no BOM.
[System.IO.File]::WriteAllText($OutFile,
    ($out | ConvertTo-Json -Depth 6),
    (New-Object System.Text.UTF8Encoding $false))

if (-not $Quiet) {
    Write-Host ""
    Write-Host "  Host metrics written" -ForegroundColor Cyan
    Write-Host "  $OutFile" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host ("  uptime    {0} hours" -f $uptimeHours)
    Write-Host ("  cpu       {0}%   temp: {1}" -f $cpuPct, $(if($cpuTempC){"$cpuTempC C"}else{"not reported by this motherboard"}))
    Write-Host ("  ram       {0} / {1} GB  ({2}%)" -f $ramUsedGb, $ramTotalGb, $ramPct)
    foreach ($d in $drives) {
        Write-Host ("  drive {0}:  {1} GB free of {2} GB  ({3}% used)" -f $d.name, $d.free_gb, $d.total_gb, $d.pct_used)
    }
    foreach ($n in $net) {
        $rate = if ($null -ne $n.rx_mbps) { "{0} down / {1} up Mbps" -f $n.rx_mbps, $n.tx_mbps } else { "rate needs a second run" }
        Write-Host ("  net {0}: link {1} Mbps, {2}" -f $n.name, $n.link_mbps, $rate)
    }
    if ($wifi) {
        Write-Host ("  wifi      {0}  signal {1}%  {2}  ch {3}  ({4}/{5} Mbps rx/tx)" -f `
            $wifi.ssid, $wifi.signal_pct, $wifi.radio, $wifi.channel, $wifi.rx_rate, $wifi.tx_rate)
    }
    Write-Host ("  internet  {0}" -f $(if($online){"reachable"}else{"NOT reachable"}))
    Write-Host ""
}
