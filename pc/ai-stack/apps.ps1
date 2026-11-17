<#
.SYNOPSIS
    apps - winget updater for SUPERNOVA, with live counter, run history and a scheduled task.

.DESCRIPTION
    Lives in ai-stack next to the stack scripts. Not to be confused with update.cmd,
    which rebuilds the Docker stack - this one only updates Windows apps via winget.

    apps                  Update everything now, one app at a time, with a live
                          "[3/12] OK: 2  Fail: 0  Skip: 1  Left: 9" counter (admin shell)
    apps --logs [N]       Runs / success / fail, last run, next run, last N runs (default 10)
    apps --tail           Full log of the most recent run
    apps --watch          Live counter for a run happening elsewhere (the midnight task)
    apps --install        (admin) C:\Tools\apps.cmd shim -> this file, and point the
                          "Winget Auto-Update" task here (every 2 days, 00:00, as you, highest, S4U)
    apps --uninstall      (admin) Remove the scheduled task (keeps files and logs)
    apps --help

    Files:
      ai-stack\apps.ps1 + apps.cmd       this script (apps.cmd works from inside ai-stack)
      C:\Tools\apps.cmd                  shim on machine PATH -> "apps" works from any SSH landing dir
      C:\Logs\winget\history.csv         one row per run (used by --logs)
      C:\Logs\winget\run-*.log           full winget output per run (last 30 kept)
      Event Viewer > Application, source "WingetAutoUpdate": 1000 = success, 1001 = failure
#>

# ---------------------------------------------------------------- config
$ShimDir     = 'C:\Tools'          # already on machine PATH
$LogRoot     = 'C:\Logs\winget'     # same place as the first version -> history carries over
$History     = Join-Path $LogRoot 'history.csv'
$TaskName    = 'Winget Auto-Update'
$EventSource = 'WingetAutoUpdate'
$KeepLogs    = 30
$SkipPattern = 'install technology|No applicable upgrade|No newer package|pinned|explicit targeting'
$NoUpdates   = -1978335189   # 0x8A15002B APPINSTALLER_CLI_ERROR_UPDATE_NOT_APPLICABLE -> treat as success
$script:ExitCode = 0

# ---------------------------------------------------------------- helpers
function Test-Admin {
    $id = [Security.Principal.WindowsIdentity]::GetCurrent()
    ([Security.Principal.WindowsPrincipal]$id).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-WingetPath {
    # The App Execution Alias (%LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe) is not always
    # resolvable in a non-interactive task session, so fall back to the real binary.
    $cmd = Get-Command winget.exe -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $bin = Get-ChildItem "$env:ProgramFiles\WindowsApps\Microsoft.DesktopAppInstaller_*_8wekyb3d8bbwe\winget.exe" -ErrorAction SilentlyContinue |
           Sort-Object { [version](($_.Directory.Name -split '_')[1]) } -Descending |
           Select-Object -First 1
    if ($bin) { return $bin.FullName }
    throw 'winget.exe not found (App Installer missing, or not visible to this account).'
}

function Write-Evt([string]$Message, [string]$Type = 'Information', [int]$Id = 1000) {
    try {
        if ([Diagnostics.EventLog]::SourceExists($EventSource)) {
            [Diagnostics.EventLog]::WriteEntry($EventSource, $Message, $Type, $Id)
        }
    } catch { }   # never let event logging break the run
}


# ---------------------------------------------------------------- live display
$StateFile = Join-Path $LogRoot 'running.json'
$script:Live = $true

function Get-Width { try { [Math]::Max(40, [Console]::WindowWidth - 1) } catch { 119 } }

function Update-Live([string]$Text) {
    if (-not $script:Live) { return }
    $w = Get-Width
    if ($Text.Length -gt $w) { $Text = $Text.Substring(0, $w) }
    Write-Host ("`r" + $Text.PadRight($w)) -NoNewline -ForegroundColor Cyan
}

function Clear-Live {
    if ($script:Live) { Write-Host ("`r" + (' ' * (Get-Width)) + "`r") -NoNewline }
}

function Set-RunState($Total, $Ok, $Fail, $Current, $Skip = 0) {
    try {
        [pscustomobject]@{ Started = $script:RunStart.ToString('yyyy-MM-dd HH:mm:ss'); Total = $Total; Ok = $Ok
                           Fail = $Fail; Skip = $Skip; Left = ($Total - $Ok - $Fail - $Skip); Current = $Current } |
            ConvertTo-Json -Compress | Set-Content -Path $StateFile -Encoding UTF8
    } catch { }
}

function Get-RunState {
    if (-not (Test-Path $StateFile)) { return $null }
    $f = Get-Item $StateFile
    if ($f.LastWriteTime -lt (Get-Date).AddHours(-3)) { return $null }   # stale (run was killed)
    try { Get-Content $StateFile -Raw | ConvertFrom-Json } catch { $null }
}

function Format-State($s) {
    "Running now: [{0}/{1}]  OK: {2}  Fail: {3}  Skip: {4}  Left: {5}   {6}" -f ($s.Ok + $s.Fail + $s.Skip), $s.Total, $s.Ok, $s.Fail, $s.Skip, $s.Left, $s.Current
}

# ---------------------------------------------------------------- run
function Get-Upgradable([string]$Winget, [string]$Log) {
    # Parse the "winget upgrade" table: column positions come from the header line.
    # Stops before the "require explicit targeting" (pinned) section.
    $raw = @(& $Winget upgrade --accept-source-agreements --disable-interactivity 2>&1 |
             ForEach-Object { ("$_" -split "`r")[-1] })
    $raw | Where-Object { $_.Trim() -and $_ -notmatch '[\u2588\u2592]' } | Add-Content -Path $Log -Encoding UTF8

    $idCol = -1
    foreach ($l in $raw) {
        if ($idCol -lt 0) {
            if ($l -match '^\s*Name\s+Id\s+Version') { $idCol = $l.IndexOf('Id') }
            continue
        }
        if ($l -match '^-{5,}') { continue }
        if (-not $l.Trim() -or $l -match 'upgrades? available|explicit targeting|cannot be determined') { break }
        if ($l.Length -le $idCol) { continue }
        $id = ($l.Substring($idCol).Trim() -split '\s+')[0]
        if ($id) { [pscustomobject]@{ Id = $id; Name = $l.Substring(0, $idCol).Trim() } }
    }
}

function Invoke-Update([string]$Trigger) {
    $script:Live = ($Trigger -ne 'Scheduled')
    if (-not (Test-Admin)) {
        Write-Warning 'Not elevated: machine-wide apps may fail. Use an admin shell (admin SSH sessions are elevated by default).'
    }

    $mutex = New-Object Threading.Mutex($false, 'Global\WingetAutoUpdate')
    if (-not $mutex.WaitOne(0)) {
        Write-Warning 'An update run is already in progress. Use "apps --watch" to follow it.'
        $script:ExitCode = 2; return
    }

    try {
        New-Item -ItemType Directory -Force -Path $LogRoot | Out-Null
        $script:RunStart = Get-Date
        $log   = Join-Path $LogRoot ('run-{0:yyyyMMdd-HHmmss}.log' -f $script:RunStart)
        $total = 0; $okN = 0; $failN = 0; $skipN = 0; $note = ''; $failedIds = @()

        "===== $($script:RunStart.ToString('yyyy-MM-dd HH:mm:ss'))  trigger=$Trigger  user=$env:USERDOMAIN\$env:USERNAME =====" |
            Out-File -FilePath $log -Encoding utf8

        try {
            $winget = Get-WingetPath
            [Console]::OutputEncoding = [Text.Encoding]::UTF8
            $ErrorActionPreference = 'Continue'   # native stderr must not throw in 5.1

            Update-Live 'Checking for updates...'
            Set-RunState 0 0 0 'checking for updates'
            $pkgs  = @(Get-Upgradable $winget $log)
            $total = $pkgs.Count
            Clear-Live
            if ($script:Live) { Write-Host "$total update(s) available." }

            $i = 0
            foreach ($p in $pkgs) {
                $i++
                Update-Live ("[{0}/{1}]  OK: {2}  Fail: {3}  Skip: {4}  Left: {5}   Updating {6}..." -f $i, $total, $okN, $failN, $skipN, ($total - $okN - $failN - $skipN), $p.Id)
                Set-RunState $total $okN $failN $p.Id $skipN

                "---- [$i/$total] $($p.Id) ----" | Add-Content -Path $log -Encoding UTF8
                $pkgOut = @(& $winget upgrade --id $p.Id --exact --silent --accept-package-agreements --accept-source-agreements --disable-interactivity 2>&1 |
                    ForEach-Object { ("$_" -split "`r")[-1] } |
                    Where-Object { $_.Trim() -and $_ -notmatch '^\s*[-\\|/]\s*$' -and $_ -notmatch '[\u2588\u2592]' })
                $code = $LASTEXITCODE
                $pkgOut | Add-Content -Path $log -Encoding UTF8

                Clear-Live
                if ($code -eq 0 -or $code -eq $NoUpdates) {
                    $okN++
                    if ($script:Live) { Write-Host '  [ok]   ' -ForegroundColor Green -NoNewline; Write-Host $p.Id }
                }
                elseif (($pkgOut -join ' ') -match $SkipPattern) {
                    # Listed by winget but not upgradable in place (e.g. Audacity: different install technology).
                    $skipN++
                    "     skipped: exit $code" | Add-Content -Path $log -Encoding UTF8
                    if ($script:Live) { Write-Host '  [skip] ' -ForegroundColor Yellow -NoNewline; Write-Host "$($p.Id)  (winget can't upgrade it in place)" }
                }
                else {
                    $failN++; $failedIds += $p.Id
                    "     exit $code ({0})" -f ('0x{0:X8}' -f $code) | Add-Content -Path $log -Encoding UTF8
                    if ($script:Live) { Write-Host '  [FAIL] ' -ForegroundColor Red -NoNewline; Write-Host ("{0}  (exit 0x{1:X8})" -f $p.Id, $code) }
                }
            }
            Set-RunState $total $okN $failN 'finishing' $skipN
        }
        catch {
            $note = $_.Exception.Message
            Clear-Live
            "ERROR: $note" | Add-Content -Path $log -Encoding UTF8
            Write-Host "ERROR: $note" -ForegroundColor Red
        }

        $ok       = ($failN -eq 0) -and (-not $note)
        $status   = if ($ok) { 'SUCCESS' } else { 'FAIL' }
        $duration = [int]((Get-Date) - $script:RunStart).TotalSeconds
        if ($failedIds) { $note = (@($note) + "failed: $($failedIds -join ', ')" | Where-Object { $_ }) -join ' | ' }

        "===== Done: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))  status=$status  available=$total  upgraded=$okN  failed=$failN  skipped=$skipN =====" |
            Add-Content -Path $log -Encoding UTF8

        # First-version rows lack Available/Skipped: convert them in place so the run count carries over.
        if ((Test-Path $History) -and ((Get-Content $History -TotalCount 1) -notmatch 'Skipped')) {
            $old = @(Import-Csv $History)
            Copy-Item $History "$History.bak" -Force
            $old | ForEach-Object {
                [pscustomobject]@{ Time = $_.Time; Status = $_.Status; Available = $(if ($_.Available) { $_.Available } else { $_.Upgraded })
                                   Upgraded = $_.Upgraded; Failed = $_.Failed; Skipped = 0; Seconds = $_.Seconds
                                   Trigger = $_.Trigger; Note = $_.Note; Log = $_.Log }
            } | Export-Csv -Path $History -NoTypeInformation -Encoding UTF8
        }
        [pscustomobject]@{
            Time      = $script:RunStart.ToString('yyyy-MM-dd HH:mm:ss')
            Status    = $status
            Available = $total
            Upgraded  = $okN
            Failed    = $failN
            Skipped   = $skipN
            Seconds   = $duration
            Trigger   = $Trigger
            Note      = $note
            Log       = $log
        } | Export-Csv -Path $History -Append -NoTypeInformation -Encoding UTF8

        $msg = "winget upgrade $status ($Trigger). Available=$total Upgraded=$okN Failed=$failN Skipped=$skipN Duration=${duration}s Log=$log $note"
        if ($ok) { Write-Evt $msg 'Information' 1000 } else { Write-Evt $msg 'Error' 1001 }

        Get-ChildItem $LogRoot -Filter 'run-*.log' | Sort-Object LastWriteTime -Descending |
            Select-Object -Skip $KeepLogs | Remove-Item -Force -ErrorAction SilentlyContinue

        $color = if ($ok) { 'Green' } else { 'Red' }
        Write-Host ("`n{0}   Available: {1}  OK: {2}  Fail: {3}  Skip: {4}  Left: 0   ({5}s)" -f $status, $total, $okN, $failN, $skipN, $duration) -ForegroundColor $color
        $script:ExitCode = if ($ok) { 0 } else { 1 }
    }
    finally {
        Remove-Item $StateFile -Force -ErrorAction SilentlyContinue
        $mutex.ReleaseMutex(); $mutex.Dispose()
    }
}

function Watch-Run {
    # Follow a run happening in another session (e.g. the scheduled task) with a live counter line.
    $seen = $false
    while ($true) {
        $s = Get-RunState
        if (-not $s) { break }
        $seen = $true
        Update-Live (Format-State $s)
        Start-Sleep -Seconds 2
    }
    Clear-Live
    if (-not $seen) { Write-Host 'No update is running right now.' }
    Show-Logs 1
}

# ---------------------------------------------------------------- status
function Show-Logs([int]$Count = 10) {
    if (-not (Test-Path $History)) { Write-Host 'No runs recorded yet.'; return }
    $h = @(Import-Csv $History)
    if ($h.Count -eq 0) { Write-Host 'No runs recorded yet.'; return }

    $ok     = @($h | Where-Object Status -eq 'SUCCESS').Count
    $fail   = $h.Count - $ok
    $last   = $h[-1]
    $lastOk = $h | Where-Object Status -eq 'SUCCESS' | Select-Object -Last 1
    $next   = try { (Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction Stop).NextRunTime } catch { 'task not registered / not visible' }
    $color  = if ($last.Status -eq 'SUCCESS') { 'Green' } else { 'Red' }

    Write-Host ''
    $live = Get-RunState
    if ($live) { Write-Host (Format-State $live) -ForegroundColor Cyan }
    Write-Host ("Runs: {0}   Success: {1}   Fail: {2}" -f $h.Count, $ok, $fail)
    Write-Host ("Last ran : {0}  " -f $last.Time) -NoNewline
    Write-Host $last.Status -ForegroundColor $color -NoNewline
    Write-Host ("  (available {0}, OK {1}, fail {2}, {3}s, {4})" -f $last.Available, $last.Upgraded, $last.Failed, $last.Seconds, $last.Trigger)
    if ($last.Note) { Write-Host "           $($last.Note)" -ForegroundColor Yellow }
    if ($lastOk -and $lastOk -ne $last) { Write-Host "Last OK  : $($lastOk.Time)" }
    Write-Host "Next run : $next"

    $h | Select-Object -Last $Count |
        Format-Table Time, Status, Available, Upgraded, Failed, Skipped, Seconds, Trigger -AutoSize | Out-Host
}

function Show-Tail {
    $f = Get-ChildItem $LogRoot -Filter 'run-*.log' -ErrorAction SilentlyContinue | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($f) { Get-Content $f.FullName } else { Write-Host 'No run logs yet.' }
}

# ---------------------------------------------------------------- install / uninstall
function Install-Updater {
    if (-not (Test-Admin)) { throw 'Run "apps --install" from an admin PowerShell.' }
    $me = $PSCommandPath

    # Shim on PATH. Absolute path to this file, so it keeps working from any directory.
    New-Item -ItemType Directory -Force -Path $ShimDir, $LogRoot | Out-Null
    "@powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$me`" %*" |
        Set-Content -Path (Join-Path $ShimDir 'apps.cmd') -Encoding ASCII
    Write-Host "  [ok]   $ShimDir\apps.cmd -> $me" -ForegroundColor Green

    # Retire the first version's 'update' shim (it would shadow nothing in ai-stack, but it's confusing).
    $old = Get-ChildItem $ShimDir -Filter 'update.*' -ErrorAction SilentlyContinue
    if ($old) {
        $dest = Join-Path $ShimDir '_old'
        New-Item -ItemType Directory -Force -Path $dest | Out-Null
        $old | Move-Item -Destination $dest -Force
        Write-Host "  [ok]   moved old $ShimDir\update.* to $dest" -ForegroundColor Green
    }

    try {
        if (-not [Diagnostics.EventLog]::SourceExists($EventSource)) {
            [Diagnostics.EventLog]::CreateEventSource($EventSource, 'Application')
        }
    } catch { Write-Warning "Event Log source not registered: $($_.Exception.Message)" }

    $user      = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $action    = New-ScheduledTaskAction -Execute 'powershell.exe' `
                   -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$me`" --scheduled"
    $trigger   = New-ScheduledTaskTrigger -Daily -DaysInterval 2 -At ([datetime]::Today.AddDays(1))
    $existing  = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) { $trigger = $existing.Triggers }   # keep the current every-other-night cadence
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Highest
    $settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
                   -ExecutionTimeLimit (New-TimeSpan -Hours 2) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Description "winget updates every 2 days at 00:00 via $me. Logs: $LogRoot" -Force | Out-Null

    Write-Host "  [ok]   task '$TaskName' -> $me" -ForegroundColor Green
    Write-Host "         next run: $((Get-ScheduledTaskInfo -TaskName $TaskName).NextRunTime)"
    Write-Host "         open a NEW window, then: apps --logs"
}

function Uninstall-Updater {
    if (-not (Test-Admin)) { throw 'Run --uninstall from an elevated PowerShell.' }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed task '$TaskName'. $ShimDir\apps.cmd and logs in $LogRoot were left in place."
}

# ---------------------------------------------------------------- dispatch
# $args (not a param block) so "--logs", "-logs" and "logs" all work from PowerShell, cmd and Task Scheduler.
$verb = if ($args.Count -gt 0) { "$($args[0])".TrimStart('-', '/').ToLower() } else { 'run' }
$n    = 10
if ($args.Count -gt 1) { [int]::TryParse("$($args[1])", [ref]$n) | Out-Null }

switch ($verb) {
    'run'       { Invoke-Update 'Manual' }
    'scheduled' { Invoke-Update 'Scheduled' }
    { $_ -in 'logs', 'log', 'status' } { Show-Logs $n }
    'tail'      { Show-Tail }
    'watch'     { Watch-Run }
    'install'   { Install-Updater }
    'uninstall' { Uninstall-Updater }
    { $_ -in 'help', 'h', '?' } { Get-Help $PSCommandPath -Detailed }
    default     { Write-Host "Unknown option '$($args[0])'. Use --help."; $script:ExitCode = 64 }
}
exit $script:ExitCode
