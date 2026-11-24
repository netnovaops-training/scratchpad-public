<#
pc_defender_harden.ps1 - Windows Defender hardening for Jason's PC (approved 2026-11-24).

RUN (Jason, elevated PowerShell - right-click "Run as administrator"):
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\security\pc_defender_harden.ps1
    # trial run first, logs blocks but blocks nothing:
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\security\pc_defender_harden.ps1 -Audit
UNDO (restores every value this script changed, from the backup it wrote):
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\security\pc_defender_harden.ps1 -Undo

What it sets:
  * Controlled Folder Access (CFA) ON (or AuditMode with -Audit), protecting D:\ and the
    Documents / Desktop / Pictures folders (local, OneDrive and OneDrive - Empire State University).
  * Allowed apps (exe paths found on this PC 2026-11-24; wildcards only in folder parts, per
    learn.microsoft.com/defender-endpoint/controlled-folder-access-overview): KopiaUI + kopia,
    Docker Desktop, git (+ Git Bash tools Claude Code uses), python, node, Claude Code + Claude Desktop,
    PowerShell 5/7, robocopy, Synology Drive, ffmpeg/ffprobe.
    Anything else that gets blocked shows as a Windows Security notification ("Unauthorized changes
    blocked"): Windows Security > Virus & threat protection > Ransomware protection >
    "Allow an app through Controlled folder access" > Recently blocked apps.
  * PUA protection on, cloud protection (MAPS advanced) with block level High, network protection on.
  * Weekly FULL scan Sunday 03:00 (ScanScheduleDay 1 = Sunday, ScanParameters 2 = full).
  * Prints the resulting settings. Defender preferences hold no secrets.

Notes:
  * Defender (real-time) already scans every file Novashare (was Atomic Drop) saves when it lands on
    this PC; CFA adds a write check on top. If a download from Novashare into D:\ is blocked, allow
    the browser from the notification.
  * CFA on all of D:\ is broad: the first days may show blocks from tools not listed here. Run -Audit
    first if you want a quiet trial (Event Viewer > Microsoft-Windows-Windows Defender/Operational,
    event 1124 = audited block), then re-run without -Audit.
  * Tamper Protection can refuse some changes; the summary shows the real values afterwards.
#>
param([switch]$Audit, [switch]$Undo)
$ErrorActionPreference = 'Stop'

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Run this from an elevated PowerShell (Run as administrator).' -ForegroundColor Red; exit 1
}

$BackupDir = 'D:\scratchpad\tools\security\backup'
$Keys = 'EnableControlledFolderAccess','ControlledFolderAccessProtectedFolders','ControlledFolderAccessAllowedApplications',
        'PUAProtection','MAPSReporting','SubmitSamplesConsent','CloudBlockLevel','CloudExtendedTimeout',
        'EnableNetworkProtection','ScanParameters','ScanScheduleDay','ScanScheduleTime'

function Show-Summary {
    $p = Get-MpPreference
    Write-Host "`n=== Defender settings now ===" -ForegroundColor Cyan
    $cfaMode = @{0='Disabled';1='Enabled';2='AuditMode';3='BlockDiskModificationOnly';4='AuditDiskModificationOnly'}
    $day = @{0='Everyday';1='Sunday';2='Monday';3='Tuesday';4='Wednesday';5='Thursday';6='Friday';7='Saturday';8='Never'}
    "Controlled Folder Access : {0}" -f $cfaMode[[int]$p.EnableControlledFolderAccess]
    "  protected folders      : {0}" -f (($p.ControlledFolderAccessProtectedFolders | Sort-Object) -join '; ')
    "  allowed apps (count)   : {0}" -f @($p.ControlledFolderAccessAllowedApplications).Count
    "PUA protection           : {0} (1=on)" -f $p.PUAProtection
    "Cloud (MAPS) reporting   : {0} (2=advanced)" -f $p.MAPSReporting
    "Cloud block level        : {0} (2=High)" -f $p.CloudBlockLevel
    "Network protection       : {0} (1=on)" -f $p.EnableNetworkProtection
    "Scheduled scan           : {0} {1} at {2}" -f $(if($p.ScanParameters -eq 2){'FULL'}else{'quick'}), $day[[int]$p.ScanScheduleDay], $p.ScanScheduleTime
}

if ($Undo) {
    $last = Get-ChildItem $BackupDir -Filter 'defender-before-*.json' -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -Last 1
    if (-not $last) { Write-Host "No backup in $BackupDir; nothing to undo." -ForegroundColor Yellow; exit 1 }
    Write-Host "Restoring from $($last.FullName)"
    $b = Get-Content $last.FullName -Raw | ConvertFrom-Json
    # Remove what this script added, then put the old lists back.
    $now = Get-MpPreference
    if ($now.ControlledFolderAccessProtectedFolders) { Remove-MpPreference -ControlledFolderAccessProtectedFolders $now.ControlledFolderAccessProtectedFolders }
    if ($now.ControlledFolderAccessAllowedApplications) { Remove-MpPreference -ControlledFolderAccessAllowedApplications $now.ControlledFolderAccessAllowedApplications }
    if ($b.ControlledFolderAccessProtectedFolders) { Add-MpPreference -ControlledFolderAccessProtectedFolders @($b.ControlledFolderAccessProtectedFolders) }
    if ($b.ControlledFolderAccessAllowedApplications) { Add-MpPreference -ControlledFolderAccessAllowedApplications @($b.ControlledFolderAccessAllowedApplications) }
    Set-MpPreference -EnableControlledFolderAccess ([int]$b.EnableControlledFolderAccess) `
        -PUAProtection ([int]$b.PUAProtection) -MAPSReporting ([int]$b.MAPSReporting) `
        -SubmitSamplesConsent ([int]$b.SubmitSamplesConsent) -CloudBlockLevel ([int]$b.CloudBlockLevel) `
        -CloudExtendedTimeout ([int]$b.CloudExtendedTimeout) -EnableNetworkProtection ([int]$b.EnableNetworkProtection) `
        -ScanParameters ([int]$b.ScanParameters) -ScanScheduleDay ([int]$b.ScanScheduleDay) `
        -ScanScheduleTime ([TimeSpan]$b.ScanScheduleTime)
    Show-Summary; exit 0
}

# ---- 1. backup the current values (as admin we can read the allowed-apps list) ----
New-Item -ItemType Directory -Force $BackupDir | Out-Null
$cur = Get-MpPreference
$snap = [ordered]@{}
foreach ($k in $Keys) { $v = $cur.$k; if ($v -is [TimeSpan]) { $v = $v.ToString() }; $snap[$k] = $v }
$bfile = Join-Path $BackupDir ("defender-before-{0}.json" -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
$snap | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 $bfile
Write-Host "Backup of old settings: $bfile"

# ---- 2. protected folders (only ones that exist) ----
$U = 'C:\Users\USER'
$folders = @('D:\',
  "$U\Documents", "$U\Desktop", "$U\Pictures",
  "$U\OneDrive\Documents", "$U\OneDrive\Desktop", "$U\OneDrive\Pictures",
  "$U\OneDrive - Empire State University\Documents", "$U\OneDrive - Empire State University\Desktop",
  "$U\OneDrive - Empire State University\Pictures") | Where-Object { Test-Path $_ }

# ---- 3. allowed apps (found 2026-11-24 with Get-Command / running processes) ----
$apps = @(
  # Kopia
  "$U\AppData\Local\Programs\KopiaUI\KopiaUI.exe",
  "$U\AppData\Local\Programs\KopiaUI\resources\server\kopia.exe",
  # Docker Desktop
  "$U\AppData\Local\Programs\DockerDesktop\Docker Desktop.exe",
  "$U\AppData\Local\Programs\DockerDesktop\frontend\Docker Desktop.exe",
  "$U\AppData\Local\Programs\DockerDesktop\resources\com.docker.backend.exe",
  "$U\AppData\Local\Programs\DockerDesktop\resources\com.docker.build.exe",
  "$U\AppData\Local\Programs\DockerDesktop\resources\bin\docker.exe",
  # git + the Git Bash tools Claude Code's Bash tool writes files with
  'C:\Program Files\Git\cmd\git.exe',
  'C:\Program Files\Git\mingw64\bin\git.exe',
  'C:\Program Files\Git\mingw64\libexec\git-core\git.exe',
  'C:\Program Files\Git\bin\bash.exe',
  'C:\Program Files\Git\usr\bin\bash.exe',
  'C:\Program Files\Git\usr\bin\cp.exe',
  'C:\Program Files\Git\usr\bin\mv.exe',
  'C:\Program Files\Git\usr\bin\rm.exe',
  'C:\Program Files\Git\usr\bin\mkdir.exe',
  'C:\Program Files\Git\usr\bin\sed.exe',
  'C:\Program Files\Git\usr\bin\tar.exe',
  'C:\Program Files\Git\usr\bin\touch.exe',
  'C:\Program Files\Git\usr\bin\tee.exe',
  # python (all installs: C:\Python314, per-user 3.11/3.12)
  'C:\Python*\python.exe', 'C:\Python*\pythonw.exe',
  "$U\AppData\Local\Programs\Python\*\python.exe",
  "$U\AppData\Local\Programs\Python\*\pythonw.exe",
  # node + Claude Code + Claude Desktop (version folder changes on update, hence the wildcard)
  'C:\Program Files\nodejs\node.exe',
  "$U\.local\bin\claude.exe",
  'C:\Program Files\WindowsApps\Claude_*\app\claude.exe',
  # PowerShell 5 and 7, robocopy
  'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe',
  'C:\Program Files\PowerShell\7\pwsh.exe',
  'C:\Windows\System32\Robocopy.exe',
  # Synology Drive
  "$U\AppData\Local\SynologyDrive\SynologyDrive.app\bin\cloud-drive-daemon.exe",
  "$U\AppData\Local\SynologyDrive\SynologyDrive.app\bin\cloud-drive-ui.exe",
  # ffmpeg (winget; the Links\ffmpeg.exe shim starts this real exe)
  "$U\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\*\bin\ffmpeg.exe",
  "$U\AppData\Local\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe\*\bin\ffprobe.exe",
  "$U\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe",
  "$U\AppData\Local\Microsoft\WinGet\Links\ffprobe.exe"
)
# keep wildcard entries; drop fixed paths that do not exist on this PC
$apps = $apps | Where-Object { $_ -like '*`**' -or (Test-Path -LiteralPath $_) }

# ---- 4. apply (Add-* keeps anything already in the lists) ----
Add-MpPreference -ControlledFolderAccessProtectedFolders $folders
Add-MpPreference -ControlledFolderAccessAllowedApplications $apps
Set-MpPreference -EnableControlledFolderAccess $(if ($Audit) { 'AuditMode' } else { 'Enabled' })
Set-MpPreference -PUAProtection Enabled
Set-MpPreference -MAPSReporting Advanced -SubmitSamplesConsent SendSafeSamples
Set-MpPreference -CloudBlockLevel High -CloudExtendedTimeout 50
Set-MpPreference -EnableNetworkProtection Enabled
# Windows PowerShell 5.1's Defender module wants a DateTime here (only the time part is used); a TimeSpan failed
# on Jason's PC 2026-11-24 ("Cannot convert ... TimeSpan to ... DateTime").
Set-MpPreference -ScanParameters FullScan -ScanScheduleDay Sunday -ScanScheduleTime ([DateTime]::Today.AddHours(3))

Write-Host "`nAllowed apps added:"; $apps | ForEach-Object { "  $_" }
Show-Summary
Write-Host "`nUndo: powershell -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -Undo" -ForegroundColor Yellow
