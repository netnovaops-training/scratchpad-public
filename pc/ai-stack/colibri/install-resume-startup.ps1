<#
.SYNOPSIS
    Make the DeepSeek download resume itself -- WITHOUT needing an admin
    prompt.

.DESCRIPTION
    install-resume-task.ps1 registers a Scheduled Task, which needs
    elevation: Register-ScheduledTask returns 0x80070005 as a normal user,
    and that elevated run never happened. So the watchdog has never been
    running, and when the download died at 12:53 today with a Xet CAS error
    nothing noticed for eight hours.

    This uses the Startup folder instead. It needs no privileges at all --
    it is a shortcut in the user's own profile -- and it starts the watchdog
    on every logon. The tradeoff versus a Scheduled Task, stated plainly:

      * a task can run without anyone logged in; this cannot
      * a task can run as SYSTEM; this runs as Jason
      * a task survives a logout; this restarts on the next logon

    For a download on a desktop that stays logged in, none of those matter.
    An auto-resume that exists beats a better one that needs a prompt
    nobody is there to click.

.EXAMPLE
    .\install-resume-startup.ps1            # install and start now
    .\install-resume-startup.ps1 -Remove    # take it out again
#>
[CmdletBinding()]
param(
    [string]$Watchdog = 'C:\Jasons New Site\www\ai-stack\colibri\resume-dsv41.ps1',
    [switch]$Remove
)

$ErrorActionPreference = 'Stop'

$startup  = [Environment]::GetFolderPath('Startup')
$lnkPath  = Join-Path $startup 'dsv41-resume.lnk'

if ($Remove) {
    if (Test-Path -LiteralPath $lnkPath) {
        Remove-Item -LiteralPath $lnkPath -Force
        "  removed $lnkPath"
    } else { "  nothing installed at $lnkPath" }
    return
}

if (-not (Test-Path -LiteralPath $Watchdog)) { throw "no watchdog at $Watchdog" }

# -WindowStyle Hidden on the shortcut rather than in the arguments: a
# PowerShell window that flashes up on every logon is the kind of thing that
# gets "fixed" by deleting the shortcut.
$sh  = New-Object -ComObject WScript.Shell
$lnk = $sh.CreateShortcut($lnkPath)
$lnk.TargetPath       = (Get-Command powershell).Source
$lnk.Arguments        = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{0}"' -f $Watchdog
$lnk.WorkingDirectory = Split-Path $Watchdog -Parent
$lnk.Description      = 'Resume the DeepSeek V4.1 Flash download if it stops'
$lnk.WindowStyle      = 7   # minimised
$lnk.Save()
"  installed $lnkPath"

# --- start it now too, so it does not wait for the next logon -------------
$already = @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -EA SilentlyContinue |
             Where-Object { $_.CommandLine -like '*resume-dsv41*' })
if ($already.Count -gt 0) {
    "  watchdog already running (pid $($already[0].ProcessId))"
} else {
    $p = Start-Process powershell `
        -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',$Watchdog `
        -WindowStyle Hidden -PassThru
    "  started the watchdog now (pid $($p.Id))"
}

""
"  it logs to C:\Models\dsv41-resume.log"
"  check on it with:  Get-Content C:\Models\dsv41-resume.log -Tail 20"
