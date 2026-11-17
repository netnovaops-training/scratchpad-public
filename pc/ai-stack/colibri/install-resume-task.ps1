<#
.SYNOPSIS
    Register a Scheduled Task that keeps the DeepSeek V4.1 download alive.

.DESCRIPTION
    WHY A SCHEDULED TASK AND NOT A BACKGROUND LOOP. Three attempts at
    `Start-Process powershell -WindowStyle Hidden` running a watch loop all
    died within minutes -- no log written, no process left. Whatever kills
    them (session teardown, the host's process management), the answer is
    not a fourth attempt at the same thing.

    A Scheduled Task is the OS's own answer to "keep doing this": it
    survives the session ending, survives a reboot -- which matters, since
    tonight already had one power cut -- and is visible in Task Scheduler
    where Jason can see and stop it.

    It runs every 15 minutes and does nothing when the download is healthy.

    NEEDS ELEVATION. I wrote "no elevation needed" here and was wrong:
    Register-ScheduledTask returns "Access is denied" (HRESULT 0x80070005)
    for this user on this machine, even registering a user-scope task. Run
    this from an elevated prompt, or fold it into elevate-setup.ps1.

.EXAMPLE
    .\install-resume-task.ps1            # register it
    .\install-resume-task.ps1 -Remove    # take it away when done
#>
[CmdletBinding()]
param(
    [string]$TaskName = 'colibri dsv41 download resume',
    [switch]$Remove
)

$script = Join-Path $PSScriptRoot 'resume-once.ps1'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "  removed: $TaskName"
    return
}

if (-not (Test-Path -LiteralPath $script)) {
    throw "missing $script -- it is what the task runs"
}

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`""

# Every 15 minutes, indefinitely. Also at logon, so a reboot resumes it
# without anyone remembering to.
$triggers = @(
    (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
        -RepetitionInterval (New-TimeSpan -Minutes 15) `
        -RepetitionDuration (New-TimeSpan -Days 30))
    (New-ScheduledTaskTrigger -AtLogOn)
)

# StartWhenAvailable so a missed run (machine asleep) fires on wake rather
# than being skipped. No battery conditions: this is a desktop.
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
    -Settings $settings -Description `
    'Restarts the DeepSeek V4.1 Flash download if it has stopped. Safe to delete once the model is complete.' `
    -Force | Out-Null

Write-Host "  registered: $TaskName"
Write-Host "  runs every 15 minutes and at logon; does nothing while the download is healthy"
Write-Host "  remove with: .\install-resume-task.ps1 -Remove"
