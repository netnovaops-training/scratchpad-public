<#
.SYNOPSIS
  Quiet the background services SuperNova doesn't need, safely and reversibly.

.DESCRIPTION
  Written 2026-11-04 from a read-only inventory of this PC (Jason asked for a script he runs).

  WHAT IT DOES: sets each listed service to MANUAL and stops it. Manual is not Disabled:
  Windows or the app can still start the service when it is actually needed (open Brother
  scanning, plug in the Nikon, launch Native Access...), so nothing breaks permanently.

  WHAT IT NEVER TOUCHES: Docker / WSL / Hyper-V (vmcompute), Tailscale, No-IP, sshd (the NUC
  logs in here; 7 logins this week), windows_exporter (the wall's PC numbers), ctrld (DNS),
  NVIDIA, Realtek audio, Defender, the print spooler, Windows Search, Synology, Proton Drive.

  SAFE BY DEFAULT: with no switch it only PRINTS the plan. Nothing changes until -Apply.
  -Apply first saves every listed service's current start type to a backup file next to
  this script; -Restore puts them all back exactly.

.EXAMPLE
  .\quiet-services.ps1                      # show the plan, change nothing
  .\quiet-services.ps1 -Apply               # do it (elevated PowerShell)
  .\quiet-services.ps1 -Restore .\services-backup-20261104-1830.json
#>
param([switch]$Apply, [string]$Restore)

# name -> why it is safe to quiet
$Quiet = [ordered]@{
  'AUEPLauncher'                    = 'AMD "user experience" data uploader (telemetry)'
  'DSAService'                      = 'Intel Driver & Support Assistant -- this is an AMD PC'
  'DSAUpdateService'                = 'Intel DSA updater -- AMD PC'
  'ESRV_SVC_QUEENCREEK'             = 'Intel energy/usage reporting (telemetry)'
  'SystemUsageReportSvc_QUEENCREEK' = 'Intel system usage report (telemetry)'
  'IntelCollectorService'           = 'Intel collector (telemetry)'
  'IntelTelemetryAgent'             = 'Intel telemetry agent'
  'Player Location Check'           = 'GeoComply location check (betting/casino apps only)'
  'GigabyteUpdateService'           = 'GIGABYTE updater; check for BIOS/driver updates by hand'
  'LGHUBUpdaterService'             = 'Logitech G HUB updater'
  'SamsungMagicianSVC'              = 'Samsung Magician; only needed while the app is open'
  'Nkdnssd Service'                 = 'Nikon network camera discovery'
  'NkPtpEnumWT3'                    = 'Nikon wireless transmitter'
  'NTKDaemonService'                = 'Native Instruments downloader (Native Access starts it)'
  'TeamViewer'                      = 'TeamViewer remote access -- you have Tailscale; better: uninstall'
  'PDFProFiltSrvPP'                 = 'Kofax PaperPort PDF filter'
  'GameInputRedistService'          = 'Game controller input service (games start it)'
  'PresentMonSharedService'         = 'Frame-rate overlay service (NVIDIA FrameView)'
  'AdobeARMservice'                 = 'Adobe Acrobat updater (Acrobat checks when opened)'
  'ProtonVPN Service'               = 'Proton VPN background service (the app starts it)'
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)

if ($Restore) {
  if (-not $isAdmin) { throw 'Run -Restore from an elevated PowerShell (Run as administrator).' }
  $saved = Get-Content $Restore -Raw | ConvertFrom-Json
  foreach ($s in $saved) {
    try { Set-Service -Name $s.Name -StartupType $s.StartType -ErrorAction Stop; "restored  $($s.Name) -> $($s.StartType)" }
    catch { "COULD NOT restore $($s.Name): $($_.Exception.Message)" }
  }
  return
}

$rows = foreach ($name in $Quiet.Keys) {
  $svc = Get-Service -Name $name -ErrorAction SilentlyContinue
  [pscustomobject]@{ Name = $name; Found = [bool]$svc; StartType = if ($svc) { "$($svc.StartType)" } else { '-' };
                     Status = if ($svc) { "$($svc.Status)" } else { '-' }; Why = $Quiet[$name] }
}
$rows | Format-Table -AutoSize Name, StartType, Status, Why | Out-String -Width 200

if (-not $Apply) { 'Plan only. Nothing changed. Run again with -Apply (as administrator) to do it.'; return }
if (-not $isAdmin) { throw 'Run -Apply from an elevated PowerShell (Run as administrator).' }

$backup = Join-Path $here ("services-backup-{0:yyyyMMdd-HHmm}.json" -f (Get-Date))
$rows | Where-Object Found | Select-Object Name, StartType | ConvertTo-Json | Set-Content $backup -Encoding utf8
"Backup of current settings: $backup"

foreach ($r in $rows | Where-Object Found) {
  try {
    Set-Service -Name $r.Name -StartupType Manual -ErrorAction Stop
    if ($r.Status -eq 'Running') { Stop-Service -Name $r.Name -Force -ErrorAction SilentlyContinue }
    "quieted   $($r.Name)"
  } catch { "SKIPPED   $($r.Name): $($_.Exception.Message)" }
}
"Done. To undo everything: .\quiet-services.ps1 -Restore `"$backup`""
