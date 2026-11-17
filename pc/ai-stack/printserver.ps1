<#
    The print server: the half that has to run on Windows.

    WHAT IT DOES
      1. PRINTS whatever the Telegram bot has put in the approved queue, using
         your real Brother driver, then files it away under printed\.
      2. WATCHES the Windows print log so you find out when ANYONE in the
         house prints -- what, how many pages, who, and how long it took.
      3. REPORTS the printer's state (ready, jam, out of paper, offline) and
         the queue depth into transcripts\printer-status.json, which n8n reads
         and turns into Telegram alerts.

    WHY THIS IS A WINDOWS SCRIPT AND NOT A CONTAINER
    The Brother driver lives in Windows. A Linux container has no access to
    it, cannot see the Windows print spooler, and cannot read the Windows
    event log. Everything here is one of those three things. The container
    side does the parts that do not need Windows -- accepting the file,
    asking you to approve it, sending the alerts.

    SECURITY, AND WHY THE ALLOWLIST IS NOT OPTIONAL
    This script hands files to Windows' "print" verb. That verb is a shell
    action: given a .pdf it prints, but given a .ps1, .bat, .lnk or .exe it
    can RUN the thing. The queue folder is therefore a place where a file
    that arrived from the internet meets the Windows shell.

    So this script prints ONLY the extensions in $AllowedExtensions, checks
    that the resolved path is genuinely inside the approved folder (so a
    symlink or a ..\ cannot point somewhere else), and refuses everything
    else loudly. The Telegram bot enforces the same list before a file ever
    reaches the queue -- deliberately twice, because the whole point of a
    second check is that it holds when the first one is wrong.

    USAGE
      printserver.cmd -Install     run it forever as a Scheduled Task  <-- do this
      printserver.cmd -Status      is it running? what has it printed?
      printserver.cmd -Once        one pass, verbose - good for testing
      printserver.cmd -EnableLog   turn on Windows' print log (ADMIN, once)
      printserver.cmd -Uninstall   remove the scheduled task
#>

param(
    [string]$Root      = $PSScriptRoot,
    [string]$Printer   = "",        # blank = your Windows default printer
    # 60, not 30. Task Scheduler REFUSES any repetition interval under one
    # minute: it rejects the whole task with "The task XML contains a value
    # which is incorrectly formatted or out of range. Interval:PT30S".
    #
    # That error was invisible for hours because the script suppressed errors
    # during install, so the task simply never existed and -Install cheerfully
    # said "installed". -Loop has no such limit and can go faster.
    [int]$Every        = 60,        # seconds between passes (min 60 for -Install)
    [int]$HistoryHours = 24,        # how far back to read the print log
    [switch]$Install,
    [switch]$Uninstall,
    [switch]$Status,
    [switch]$Once,
    [switch]$Loop,
    [switch]$EnableLog,
    [switch]$Test,
    [switch]$Quiet
)

$ErrorActionPreference = "SilentlyContinue"
Set-Location -Path $PSScriptRoot

$TaskName   = "ai-stack print server"
$PrintLog   = "Microsoft-Windows-PrintService/Operational"

$QueueDir   = Join-Path $Root "transcripts\print-queue"
$ApprovedDir= Join-Path $QueueDir "approved"
$PrintedDir = Join-Path $QueueDir "printed"
$FailedDir  = Join-Path $QueueDir "failed"
$StatusFile = Join-Path $Root "transcripts\printer-status.json"

# MUST match ALLOWED_EXTENSIONS in shared/printing.py. Anything that can
# execute is absent on purpose, including Office macro formats.
$AllowedExtensions = @(
    ".pdf",
    ".txt", ".log", ".md", ".csv",
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tif", ".tiff",
    ".doc", ".docx", ".rtf", ".odt",
    ".xls", ".xlsx", ".ods",
    ".ppt", ".pptx"
)
# These print cleanly with no application involved at all, which is both
# faster and safer than launching a handler.
$TextExtensions = @(".txt", ".log", ".md", ".csv")

function Say($m)  { if (-not $Quiet) { Write-Host $m } }
function Warn($m) { if (-not $Quiet) { Write-Host $m -ForegroundColor Yellow } }
function Bad($m)  { Write-Host $m -ForegroundColor Red }
function Good($m) { if (-not $Quiet) { Write-Host $m -ForegroundColor Green } }

foreach ($d in @($QueueDir, $ApprovedDir, $PrintedDir, $FailedDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}


# ===========================================================================
#  Modes that exit immediately
# ===========================================================================

if ($EnableLog) {
    # Windows ships this log DISABLED. Without it there is no record of who
    # printed what, so every "someone printed" alert would be impossible.
    # Enabling it needs administrator rights exactly once.
    Say ""
    Say "  Enabling the Windows print log so completed jobs are recorded..."
    & wevtutil.exe sl "$PrintLog" /e:true /ms:8388608 2>&1 | Out-Null
    $log = Get-WinEvent -ListLog $PrintLog -ErrorAction SilentlyContinue
    if ($log -and $log.IsEnabled) {
        Good "  print log is ON  (max size $([math]::Round($log.MaximumSizeInBytes/1MB)) MB)"
        Say  "  From now on every print job is recorded and can be alerted on."
    } else {
        Bad  "  could not enable it - run this as Administrator:"
        Bad  "     wevtutil sl $PrintLog /e:true"
    }
    Say ""
    return
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Remove-Item (Join-Path $PSScriptRoot "printserver-hidden.vbs") -ErrorAction SilentlyContinue
    Warn "  removed scheduled task '$TaskName'"
    return
}

if ($Install) {
    # Errors are visible in HERE even though the rest of the script suppresses
    # them. $ErrorActionPreference = "SilentlyContinue" at the top is right for
    # the sampling path -- a scheduled task should never die because one WMI
    # call hiccuped -- but during install it hid the very failure we needed to
    # see. "Registered but it did not run" with nothing after it was that.
    $ErrorActionPreference = "Continue"

    # Runs as YOU, not SYSTEM: SYSTEM has a different set of installed
    # printers and usually cannot see your default one at all, so a task
    # running as SYSTEM would look healthy and print nothing.
    $ps = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
    if (-not $ps) { $ps = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" }

    # NOT $args. That is a PowerShell AUTOMATIC variable holding a script's
    # unbound arguments, and assigning a string over an array PowerShell owns
    # is asking for trouble -- exactly the kind that shows up only inside a
    # scheduled task, where you cannot see it.
    $taskArgs = '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}\printserver.ps1" -Quiet' -f $PSScriptRoot
    if ($Printer) { $taskArgs += ' -Printer "{0}"' -f $Printer }

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
    $vbs = Join-Path $PSScriptRoot "printserver-hidden.vbs"
    $wscript = "$env:SystemRoot\System32\wscript.exe"
    $inner = ('"{0}" {1}' -f $ps, $taskArgs) -replace '"', '""'
    @(
        "' Generated by printserver.ps1 -Install. Safe to delete; -Install rewrites it.",
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

    # TWO triggers, not one with a .Repetition bolted on.
    #
    # The old version built an AtLogOn trigger and then assigned .Repetition
    # to it from a throwaway -Once trigger. That snippet is everywhere and it
    # is unreliable: Task Scheduler frequently drops the repetition on a logon
    # trigger, and [TimeSpan]::MaxValue is rejected outright on some builds.
    # The task registers cleanly, reports Ready, and runs once at logon --
    # which looks identical to working.
    # Task Scheduler's minimum repetition interval is ONE MINUTE. Anything
    # smaller makes Register-ScheduledTask reject the entire task rather than
    # rounding up, so clamp here and say so instead of failing.
    $taskEvery = $Every
    if ($taskEvery -lt 60) {
        Warn "  Task Scheduler's minimum interval is 60s - using 60 instead of $Every."
        Warn "  (printserver.cmd -Loop can poll faster; Task Scheduler cannot.)"
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
        -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

    # Remove any previous version FIRST rather than relying on -Force.
    # MultipleInstances IgnoreNew means a task stuck in a Running state
    # silently swallows every new start request -- which presents as a task
    # that is enabled, Ready, and does nothing at all.
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Say "  removing the previous task first (a stuck one blocks new runs)"
        try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch { }
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 1
    }

    try {
        Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
            -Settings $settings -Description `
            "Prints files approved from Telegram, and records every print job for alerting." `
            -ErrorAction Stop | Out-Null
    } catch {
        Bad "  could not register the task: $_"
        Say "  run it in a window instead:  printserver.cmd -Loop"
        return
    }

    # --- did it actually RUN? ---------------------------------------------
    # "Registered" and "working" are different things, and reporting the first
    # as if it were the second is how you end up staring at a three-hour-old
    # file wondering why nothing happened.
    $before = if (Test-Path $StatusFile) { (Get-Item $StatusFile).LastWriteTime } else { [datetime]::MinValue }
    try { Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
    catch { Bad "  Start-ScheduledTask failed: $_" }

    Say ""
    Say "  registered - waiting up to 45s to confirm it really runs..."
    $ok = $false
    for ($i = 0; $i -lt 45; $i++) {
        Start-Sleep -Seconds 1
        if ((Test-Path $StatusFile) -and (Get-Item $StatusFile).LastWriteTime -gt $before) { $ok = $true; break }
    }
    Say ""

    if ($ok) {
        Good "  CONFIRMED WORKING - it wrote its output file"
        Say  "  '$TaskName' - every $taskEvery seconds, and again at every logon"
        Say  "  check it with:  printserver.cmd -Status"
        Say  ""
        return
    }

    # --- it did not. Find out WHY, rather than shrugging. -------------------
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
            2147942402 { Warn "  The system cannot find the file specified - check the path below." }
        }
    } else {
        Warn "  Windows will not report any info for this task at all."
        Warn "  That usually means Task Scheduler itself is restricted here."
    }

    # The most useful test there is: run EXACTLY what the task runs, in the
    # open, and show what happens. If this works, the problem is Task
    # Scheduler. If it fails, the reason is on screen instead of hidden.
    Say ""
    Say "  Running the task's own command directly to see what it does:"
    Say ("     {0} {1}" -f $ps, $taskArgs) 
    Say ""
    $before2 = if (Test-Path $StatusFile) { (Get-Item $StatusFile).LastWriteTime } else { [datetime]::MinValue }
    try {
        $out = & $ps -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\printserver.ps1" -Once 2>&1
        $out | Select-Object -First 25 | ForEach-Object { Say ("     " + $_) }
    } catch {
        Bad ("     it threw: " + $_)
    }
    $after2 = if (Test-Path $StatusFile) { (Get-Item $StatusFile).LastWriteTime } else { [datetime]::MinValue }

    Say ""
    if ($after2 -gt $before2) {
        Good "  The script itself WORKS - only the scheduled task does not."
        Say  ""
        Say  "  Two ways forward:"
        Say  "    1. printserver.cmd -Loop      leave a window open; always works"
        Say  "    2. Task Scheduler -> 'ai-stack print server' -> Run, and read"
        Say  "       the History tab. Something on this machine is blocking it."
    } else {
        Bad  "  The script fails when run directly too - so this is NOT a"
        Bad  "  Task Scheduler problem. The output above is the real reason."
    }
    Say ""
    return
}

# ===========================================================================
#  Printing
# ===========================================================================

function Test-InsideFolder([string]$Path, [string]$Folder) {
    # Resolve BOTH sides before comparing. Comparing the strings you were
    # given would be satisfied by "approved\..\..\Windows\System32\x.exe";
    # comparing what those strings actually resolve to would not.
    try {
        $p = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).ProviderPath
        $f = (Resolve-Path -LiteralPath $Folder -ErrorAction Stop).ProviderPath
    } catch { return $false }
    if (-not $f.EndsWith("\")) { $f = $f + "\" }
    return $p.StartsWith($f, [System.StringComparison]::OrdinalIgnoreCase)
}

function Invoke-PrintFile([System.IO.FileInfo]$File) {
    $ext = $File.Extension.ToLower()

    # --- refuse anything not explicitly allowed ---------------------------
    if ($AllowedExtensions -notcontains $ext) {
        Bad "  REFUSED (not a printable type): $($File.Name)"
        Move-Item -LiteralPath $File.FullName -Destination (Join-Path $FailedDir $File.Name) -Force
        return @{ ok = $false; reason = "extension $ext is not allowed" }
    }
    # --- and anything that is not really where it claims to be ------------
    if (-not (Test-InsideFolder $File.FullName $ApprovedDir)) {
        Bad "  REFUSED (resolves outside the queue): $($File.Name)"
        return @{ ok = $false; reason = "path escapes the approved folder" }
    }

    $started = Get-Date
    try {
        if ($TextExtensions -contains $ext) {
            # No application involved: the spooler gets the text directly.
            if ($Printer) { Get-Content -LiteralPath $File.FullName | Out-Printer -Name $Printer }
            else          { Get-Content -LiteralPath $File.FullName | Out-Printer }
        }
        elseif ($Printer) {
            # PrintTo lets us name the printer. Not every file handler
            # registers a PrintTo verb, so fall back to plain Print.
            try {
                Start-Process -FilePath $File.FullName -Verb PrintTo -ArgumentList "`"$Printer`"" `
                    -PassThru -ErrorAction Stop | Out-Null
            } catch {
                Start-Process -FilePath $File.FullName -Verb Print -PassThru -ErrorAction Stop | Out-Null
            }
        }
        else {
            Start-Process -FilePath $File.FullName -Verb Print -PassThru -ErrorAction Stop | Out-Null
        }
    } catch {
        Bad "  FAILED to print $($File.Name): $_"
        Move-Item -LiteralPath $File.FullName -Destination (Join-Path $FailedDir $File.Name) -Force
        return @{ ok = $false; reason = "$_" }
    }

    # The shell verb returns as soon as the handler has been asked to print,
    # not when the paper appears. Give the spooler a moment to actually
    # receive the job before we move the file out from under it.
    Start-Sleep -Seconds 4

    $dest = Join-Path $PrintedDir ("{0:yyyyMMdd-HHmmss}-{1}" -f $started, $File.Name)
    Move-Item -LiteralPath $File.FullName -Destination $dest -Force
    Good "  printed: $($File.Name)"
    return @{ ok = $true; seconds = [math]::Round(((Get-Date) - $started).TotalSeconds) }
}


# ===========================================================================
#  Reading what happened
# ===========================================================================

function Get-CompletedJobs([int]$Hours) {
    # Event 307 is "a document finished printing". Its message reads:
    #   "Document {1}, {2} owned by {3} on {4} was printed on {5} through
    #    port {6}. Size in bytes: {7}. Pages printed: {8}."
    # so the Properties array is, 0-indexed:
    #   0 job id   1 document   2 user   3 machine
    #   4 printer  5 port       6 bytes  7 pages
    $since = (Get-Date).AddHours(-$Hours)
    $events = @()
    try {
        $events = Get-WinEvent -FilterHashtable @{
            LogName = $PrintLog; Id = 307; StartTime = $since
        } -ErrorAction Stop
    } catch {
        return @()   # log off, or simply nothing printed in the window
    }

    $out = @()
    foreach ($e in $events) {
        $p = $e.Properties
        if ($p.Count -lt 8) { continue }
        $pages = 0; [int]::TryParse("$($p[7].Value)", [ref]$pages) | Out-Null
        $bytes = 0; [int64]::TryParse("$($p[6].Value)", [ref]$bytes) | Out-Null
        $out += [pscustomobject]@{
            time     = $e.TimeCreated.ToString("o")
            job_id   = "$($p[0].Value)"
            document = "$($p[1].Value)"
            user     = "$($p[2].Value)"
            machine  = "$($p[3].Value)"
            printer  = "$($p[4].Value)"
            bytes    = $bytes
            pages    = $pages
            status   = "Printed"
        }
    }
    return $out
}

function Get-PrintErrors([int]$Hours) {
    # Anything the print service logged at Error or Warning level. Kept
    # generic rather than a list of event ids on purpose: the ids differ
    # between Windows builds and drivers, and "the print service complained"
    # is the thing worth telling you about either way.
    $since = (Get-Date).AddHours(-$Hours)
    $out = @()
    try {
        $events = Get-WinEvent -FilterHashtable @{
            LogName = $PrintLog; Level = @(1,2,3); StartTime = $since
        } -ErrorAction Stop
        foreach ($e in $events) {
            $msg = ($e.Message -replace '\s+', ' ').Trim()
            if ($msg.Length -gt 300) { $msg = $msg.Substring(0,297) + "..." }
            $out += [pscustomobject]@{
                time  = $e.TimeCreated.ToString("o")
                id    = $e.Id
                level = $e.LevelDisplayName
                text  = $msg
            }
        }
    } catch { }
    return $out
}

function Get-PrinterState() {
    $name = $Printer
    if (-not $name) {
        $def = Get-CimInstance -ClassName Win32_Printer -Filter "Default = TRUE" -ErrorAction SilentlyContinue
        if ($def) { $name = $def.Name }
    }
    if (-not $name) { return $null }

    $win = Get-CimInstance -ClassName Win32_Printer -Filter "Name='$($name -replace "'","''")'" -ErrorAction SilentlyContinue
    $jobs = @(Get-CimInstance -ClassName Win32_PrintJob -ErrorAction SilentlyContinue |
              Where-Object { $_.Name -like "$name*" })

    # Oldest job still sitting in the queue. A job there for ten minutes is
    # stuck, and that is the difference between "printing" and "quietly not
    # printing".
    $stuckMin = $null
    foreach ($j in $jobs) {
        $t = $null
        try { $t = [System.Management.ManagementDateTimeConverter]::ToDateTime($j.TimeSubmitted) } catch { }
        if ($t) {
            $m = [math]::Round(((Get-Date) - $t).TotalMinutes)
            if ($null -eq $stuckMin -or $m -gt $stuckMin) { $stuckMin = $m }
        }
    }

    # --- the printer's state ------------------------------------------------
    # Get-Printer, NOT a hand-decoded WMI enum.
    #
    # This used to translate Win32_Printer.DetectedErrorState through a table
    # written from memory -- and that table was the PrinterStatus enum, which
    # is a DIFFERENT enumeration with different numbers. The result was a
    # perfectly healthy Brother reporting "Jammed", which is exactly the kind
    # of alert that teaches you to ignore alerts.
    #
    # Get-Printer returns the status as a NAME ("Normal", "Offline",
    # "PaperJam"), so there is no table to get wrong. The raw number is kept
    # alongside it so that if this is ever wrong again, the truth is visible
    # rather than mistranslated.
    $state = $null
    $rawStatus = $null
    try {
        $p = Get-Printer -Name $name -ErrorAction Stop
        if ($null -ne $p.PrinterStatus) {
            $state = [string]$p.PrinterStatus
            $rawStatus = [int]$p.PrinterStatus
        }
    } catch { }

    if (-not $state -and $win) {
        # Fallback only. Reported as a raw number rather than guessed at,
        # because a wrong word is worse than an honest number.
        if ($null -ne $win.DetectedErrorState) {
            $rawStatus = [int]$win.DetectedErrorState
            $state = "reported code $rawStatus"
        } else {
            $state = "Unknown"
        }
    }
    if (-not $state) { $state = "Unknown" }

    # WorkOffline is Windows' "Use Printer Offline" flag, and it is REPORTED
    # SEPARATELY rather than folded into $state.
    #
    # They mean different things and have different fixes. The printer saying
    # it is offline is a printer problem. Windows deciding it is offline --
    # which Windows does on its own when a WSD port stops answering, and then
    # does not undo when the printer comes back -- is a WINDOWS problem, and
    # the printer can be sitting there switched on and reachable the whole
    # time. Collapsing both into "Offline" sends you to look at the wrong one.
    $workOffline = $false
    if ($win -and $win.WorkOffline) {
        $workOffline = $true
        $state = "Offline"          # jobs really will queue rather than print
    }

    return [pscustomobject]@{
        printer   = $name
        model     = if ($win) { $win.DriverName } else { $null }
        port      = if ($win) { $win.PortName } else { $null }
        state     = $state
        raw_status = $rawStatus
        work_offline = $workOffline
        reachable = if ($win) { -not $win.WorkOffline } else { $false }
        queued    = $jobs.Count
        queue_stuck_min = $stuckMin
        shared    = if ($win) { [bool]$win.Shared } else { $false }
    }
}


# ===========================================================================
#  One pass
# ===========================================================================

function Invoke-Pass() {
    # 1. print anything approved
    $printedNow = @()
    $pending = @(Get-ChildItem -LiteralPath $ApprovedDir -File -ErrorAction SilentlyContinue |
                 Sort-Object CreationTime)
    foreach ($f in $pending) {
        $r = Invoke-PrintFile $f
        $printedNow += [pscustomobject]@{ name = $f.Name; ok = $r.ok; reason = $r.reason }
    }

    # 2. read what the whole house has printed
    $jobs   = @(Get-CompletedJobs -Hours $HistoryHours)
    $errors = @(Get-PrintErrors  -Hours $HistoryHours)
    $state  = Get-PrinterState

    $logOn = $false
    $l = Get-WinEvent -ListLog $PrintLog -ErrorAction SilentlyContinue
    if ($l -and $l.IsEnabled) { $logOn = $true }

    $out = [pscustomobject]@{
        ts          = (Get-Date).ToString("o")
        printer     = if ($state) { $state.printer } else { $null }
        model       = if ($state) { $state.model } else { $null }
        port        = if ($state) { $state.port } else { $null }
        state       = if ($state) { $state.state } else { "no printer found" }
        work_offline = if ($state) { $state.work_offline } else { $false }
        reachable   = if ($state) { $state.reachable } else { $false }
        queued      = if ($state) { $state.queued } else { $null }
        queue_stuck_min = if ($state) { $state.queue_stuck_min } else { $null }
        # Toner is NOT here. Windows does not expose supply levels for most
        # network printers; that comes from SNMP on the Netdata side, and is
        # merged in by whatever reads this file.
        supplies    = @()
        print_log_enabled = $logOn
        recent      = @($jobs | Sort-Object time -Descending | Select-Object -First 25)
        errors      = @($errors | Sort-Object time -Descending | Select-Object -First 10)
        printed_now = $printedNow
        queue_pending = @(Get-ChildItem -LiteralPath $ApprovedDir -File -ErrorAction SilentlyContinue).Count
    }

    $dir = Split-Path $StatusFile -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    # NOT `Set-Content -Encoding UTF8`. In Windows PowerShell 5.1 that
# writes a BYTE ORDER MARK, and Python's json.loads rejects a BOM
# outright: "Unexpected UTF-8 BOM (decode using utf-8-sig)". The file
# looks perfect in Notepad and is unreadable to every consumer.
# UTF8Encoding($false) is UTF-8 with no BOM.
[System.IO.File]::WriteAllText($StatusFile,
    ($out | ConvertTo-Json -Depth 6),
    (New-Object System.Text.UTF8Encoding $false))
    return $out
}


# ===========================================================================
#  Status
# ===========================================================================

if ($Test) {
    # The fastest way to answer "is any of this actually working".
    # Writes a one-page text file into the APPROVED queue and runs a pass, so
    # it exercises the real print path rather than a special-case shortcut.
    $st = Get-PrinterState
    Write-Host ""
    if (-not $st) {
        Bad "  No printer found. Set a default printer in Windows Settings,"
        Bad "  or pass one:  printserver.cmd -Test -Printer ""Your Printer Name"""
        Write-Host ""
        Write-Host "  Printers Windows can see:" -ForegroundColor DarkGray
        Get-Printer | ForEach-Object { Write-Host ("    " + $_.Name) -ForegroundColor DarkGray }
        Write-Host ""
        return
    }
    Say ""
    Say "  printer   $($st.printer)"
    Say "  state     $($st.state)$(if ($null -ne $st.raw_status) { "  (code $($st.raw_status))" })"
    Say ""
    $f = Join-Path $ApprovedDir ("test-page-{0:yyyyMMdd-HHmmss}.txt" -f (Get-Date))
    @(
        "ai-stack print server - test page",
        "",
        ("printed: " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")),
        ("printer: " + $st.printer),
        ("from:    " + $env:COMPUTERNAME + " as " + $env:USERNAME),
        "",
        "If you are holding this, the whole path works:",
        "  Telegram -> queue folder -> Windows -> this printer.",
        "",
        "Send a PDF to your Telegram bot and tap 'Print it'."
    ) | Set-Content -Path $f -Encoding ASCII
    Good "  queued a test page"
    Invoke-Pass | Out-Null
    Say ""
    Say "  If nothing came out, check:  printserver.cmd -Status"
    Say ""
    return
}

if ($Status) {
    Write-Host ""
    $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $t) {
        Bad "  NOT INSTALLED - nothing is printing or watching."
        Write-Host "  Install it:  printserver.cmd -Install"
    } else {
        $info = Get-ScheduledTaskInfo -TaskName $TaskName
        $c = if ($t.State -eq 'Ready' -or $t.State -eq 'Running') { 'Green' } else { 'Red' }
        Write-Host ("  task        {0}" -f $t.State) -ForegroundColor $c
        Write-Host ("  last run    {0}  (result {1})" -f $info.LastRunTime, $info.LastTaskResult)
        Write-Host ("  next run    {0}" -f $info.NextRunTime)
    }

    $l = Get-WinEvent -ListLog $PrintLog -ErrorAction SilentlyContinue
    if ($l -and $l.IsEnabled) {
        Write-Host "  print log   ON" -ForegroundColor Green
    } else {
        Write-Host "  print log   OFF - completed jobs cannot be recorded" -ForegroundColor Yellow
        Write-Host "              fix (as Administrator):  printserver.cmd -EnableLog"
    }

    if (Test-Path $StatusFile) {
        $age = [math]::Round(((Get-Date) - (Get-Item $StatusFile).LastWriteTime).TotalMinutes, 1)
        $c = if ($age -le 2) { 'Green' } elseif ($age -le 15) { 'Yellow' } else { 'Red' }
        Write-Host ("  status file {0} minutes old" -f $age) -ForegroundColor $c
    } else {
        Write-Host "  status file MISSING - it has never written one." -ForegroundColor Red
    }

    $st = Get-PrinterState
    if ($st) {
        Write-Host ""
        Write-Host ("  printer     {0}" -f $st.printer)
        Write-Host ("  driver      {0}" -f $st.model)
        Write-Host ("  state       {0}" -f $st.state)
        Write-Host ("  queue       {0} job(s)" -f $st.queued)
    } else {
        Write-Host ""
        Bad "  no default printer found - set one in Windows Settings"
    }

    $pend = @(Get-ChildItem -LiteralPath $ApprovedDir -File -ErrorAction SilentlyContinue).Count
    $done = @(Get-ChildItem -LiteralPath $PrintedDir  -File -ErrorAction SilentlyContinue).Count
    $fail = @(Get-ChildItem -LiteralPath $FailedDir   -File -ErrorAction SilentlyContinue).Count
    Write-Host ""
    Write-Host ("  queue       {0} waiting, {1} printed, {2} refused/failed" -f $pend, $done, $fail)
    Write-Host ("              {0}" -f $QueueDir) -ForegroundColor DarkGray
    Write-Host ""
    return
}

if ($Loop) {
    Say ""
    Say "  Print server running. Leave this window open; Ctrl+C to stop."
    Say "  To have it survive a reboot instead:  printserver.cmd -Install"
    Say ""
    while ($true) {
        $r = Invoke-Pass
        Say ("  {0}  printer={1} queue={2} recent={3}" -f (Get-Date -Format "HH:mm:ss"), $r.state, $r.queued, $r.recent.Count)
        Start-Sleep -Seconds $Every
    }
}

# default, and -Once: a single pass
$r = Invoke-Pass
if (-not $Quiet) {
    Write-Host ""
    Write-Host "  Print server pass complete" -ForegroundColor Cyan
    Write-Host "  $StatusFile" -ForegroundColor DarkGray
    Write-Host ""
    Write-Host ("  printer     {0}  ({1})" -f $r.printer, $r.state)
    Write-Host ("  queue       {0} waiting" -f $r.queue_pending)
    Write-Host ("  printed now {0}" -f $r.printed_now.Count)
    Write-Host ("  jobs seen   {0} in the last {1}h" -f $r.recent.Count, $HistoryHours)
    if (-not $r.print_log_enabled) {
        Write-Host ""
        Warn "  The Windows print log is OFF, so 'who printed what' is blank."
        Warn "  Run once as Administrator:  printserver.cmd -EnableLog"
    }
    Write-Host ""
}
