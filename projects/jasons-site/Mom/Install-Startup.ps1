<#
================================================================================
  Make Windows run the Tech Help services at every startup.

      Right-click PowerShell -> Run as Administrator, then:
      .\Install-Startup.ps1

      .\Install-Startup.ps1 -Remove      undo it
      .\Install-Startup.ps1 -AtLogon     start when you sign in (no admin needed)

  WHY A SCHEDULED TASK AND NOT A .EXE OR THE STARTUP FOLDER
  ---------------------------------------------------------
  A compiled .exe sounds tidier and is worse here. It would need rebuilding on
  every change, Defender treats freshly-compiled unsigned binaries as suspect,
  and when something breaks you get a silent exit instead of a log line.

  The Startup folder has a different problem: it runs only after you sign in,
  so a machine that reboots at 3am for updates sits there doing nothing until
  someone logs in -- and your mother is 2,500 miles away asking questions in
  the meantime.

  A scheduled task set to run at BOOT, as SYSTEM, with "run whether the user is
  logged on or not", starts the moment Windows is up. It restarts on failure,
  it logs to Event Viewer, and it survives updates. That is what this installs.

  THE TRADE-OFF, STATED PLAINLY
  -----------------------------
  Running at boot as SYSTEM means no desktop session and no user environment,
  which some GPU drivers dislike for CUDA work. If Ollama misbehaves that way,
  re-run with -AtLogon: it runs as you, after sign-in, with a full session --
  and then the machine must be signed in for it to serve.

  Start with -AtLogon if you are unsure. Move to boot once it is proven.
================================================================================
#>
[CmdletBinding()]
param(
  [string]$Script  = (Join-Path $PSScriptRoot 'Start-TechHelp.ps1'),
  [string]$TaskName = 'TechHelp Services',
  [switch]$AtLogon,
  [switch]$Remove
)

$ErrorActionPreference = 'Continue'
function Ok($t)  { Write-Host "  [ok]   $t" -ForegroundColor Green }
function Note($t){ Write-Host "         $t" -ForegroundColor Gray }
function Warn($t){ Write-Host "  [warn] $t" -ForegroundColor Yellow }
function Bad($t) { Write-Host "  [FAIL] $t" -ForegroundColor Red }

$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Host "`n  Tech Help startup task" -ForegroundColor White

if ($Remove) {
  if (-not $isAdmin) { Bad "Removing needs Administrator."; exit 1 }
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
  Ok "Removed '$TaskName'. Nothing will start automatically now."
  exit 0
}

if (-not (Test-Path $Script)) {
  Bad "Cannot find $Script"
  Note "Pass the path:  .\Install-Startup.ps1 -Script 'C:\path\to\Start-TechHelp.ps1'"
  exit 1
}
Ok "Will run: $Script"

if (-not $isAdmin) {
  if (-not $AtLogon) {
    Warn "Not Administrator, so a boot-time task cannot be created."
    Note "Falling back to -AtLogon, which does not need it."
    $AtLogon = $true
  }
}

# -WindowStyle Hidden plus -Quiet so nothing flashes up at boot. Everything
# still goes to the log the script writes.
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$Script`" -Quiet"

if ($AtLogon) {
  $trigger   = New-ScheduledTaskTrigger -AtLogOn
  $principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" `
                                          -LogonType Interactive -RunLevel Highest
  $when = "when you sign in"
} else {
  $trigger   = New-ScheduledTaskTrigger -AtStartup
  $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' `
                                          -LogonType ServiceAccount -RunLevel Highest
  $when = "at boot, before anyone signs in"
}

# RestartCount/Interval cover a service that dies at boot because the network
# is not up yet -- common, and otherwise means nothing runs until you notice.
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
  -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
  -ExecutionTimeLimit ([TimeSpan]::Zero)     # it supervises forever, so no limit

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
try {
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description 'Starts Ollama, Whisper and the ticket listener for Tech Help.' | Out-Null
  Ok "Installed. It will start $when."
} catch {
  Bad "Could not create the task: $($_.Exception.Message)"
  exit 1
}

Write-Host ""
Note "Start it now without rebooting:"
Note "  Start-ScheduledTask -TaskName '$TaskName'"
Note ""
Note "See what is running:"
Note "  .\Start-TechHelp.ps1 -Status"
Note ""
Note "Read the log:"
Note "  Get-Content `"$env:LOCALAPPDATA\TechHelp\startup.log`" -Tail 40"
Note ""
# A Desktop shortcut, so there is something obvious to click when it is not
# running -- the scheduled task covers boot, this covers "it stopped and I want
# it back now" without hunting through folders.
try {
  $lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) 'Start Tech Help.lnk'
  $sh  = New-Object -ComObject WScript.Shell
  $s   = $sh.CreateShortcut($lnk)
  $s.TargetPath       = (Join-Path $PSScriptRoot 'START TECH HELP.cmd')
  $s.WorkingDirectory = $PSScriptRoot
  $s.Description      = 'Start Ollama, Whisper and the ticket listener'
  $s.IconLocation     = "$env:SystemRoot\System32\shell32.dll,15"
  $s.Save()
  Ok "Shortcut on your Desktop: Start Tech Help"
} catch { Warn "Could not create the Desktop shortcut: $($_.Exception.Message)" }

Note "Remove it:"
Note "  .\Install-Startup.ps1 -Remove"
Write-Host ""
