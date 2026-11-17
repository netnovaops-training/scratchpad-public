<#
  list-software.ps1 -- every installed program on this PC, from all four places Windows records them
  (Jason, 2026-11-04: "an accurate reflection of the total installed software, 3rd party").

    Machine (64-bit)  HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall
    Machine (32-bit)  HKLM\SOFTWARE\WOW6432Node\...\Uninstall
    This user         HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall   (per-user installs)
    Store app         Get-AppxPackage (what shell:AppsFolder shows; never in the Uninstall keys)

  Hidden by default (use -All to include): Windows components (SystemComponent=1), updates/patches
  (ParentKeyName), Microsoft-signed Store frameworks and inbox apps. Microsoft PROGRAMS you installed
  (Office, VS Code, PowerToys...) are kept.

  .\list-software.ps1            third-party + your own Microsoft programs, table + CSV
  .\list-software.ps1 -All       everything, including Windows components
  CSV: pc-cleanup\inventory\installed-software-<date>.csv
#>
param([switch]$All)
$src = @(
  @{ Path = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*';              Where = 'Machine (64-bit)' },
  @{ Path = 'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*';  Where = 'Machine (32-bit)' },
  @{ Path = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*';              Where = 'This user' }
)
$rows = foreach ($s in $src) {
  Get-ItemProperty $s.Path -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName } |
    Where-Object { $All -or (-not $_.SystemComponent -and -not $_.ParentKeyName -and $_.ReleaseType -notmatch 'Update|Hotfix') } |
    ForEach-Object {
      $d = "$($_.InstallDate)"
      [pscustomobject]@{
        Name = $_.DisplayName.Trim(); Version = "$($_.DisplayVersion)"; Publisher = "$($_.Publisher)".Trim()
        Installed = if ($d -match '^\d{8}$') { '{0}-{1}-{2}' -f $d.Substring(0,4), $d.Substring(4,2), $d.Substring(6,2) } else { '' }
        SizeMB = if ($_.EstimatedSize) { [math]::Round($_.EstimatedSize / 1024) } else { $null }
        Where = $s.Where
      }
    }
}
$rows += Get-AppxPackage -ErrorAction SilentlyContinue | Where-Object { -not $_.IsFramework -and -not $_.IsResourcePackage } |
  Where-Object { $All -or ($_.SignatureKind -ne 'System' -and $_.Publisher -notmatch 'CN=Microsoft (Corporation|Windows)') } |
  ForEach-Object {
    [pscustomobject]@{ Name = $_.Name; Version = "$($_.Version)"; Publisher = ($_.Publisher -replace '^CN=([^,]+).*', '$1')
                       Installed = ''; SizeMB = $null; Where = 'Store app' }
  }
# ---- OTHER ACCOUNTS (Jason, 2026-11-04: "check all users ... roaming ... public profiles").
# Each account's per-user installs live in its own registry file (NTUSER.DAT). A signed-in account's is
# already open under HKEY_USERS; a signed-out one is opened read-only for a moment and closed again.
# Needs admin; without it, other accounts are listed as "not readable".
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrators')
$me = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$profiles = Get-CimInstance Win32_UserProfile | Where-Object { -not $_.Special -and $_.LocalPath -like 'C:\Users\*' -and $_.SID -ne $me }
$notes = @()
foreach ($p in $profiles) {
  $acct = Split-Path $p.LocalPath -Leaf; $hive = "Registry::HKEY_USERS\$($p.SID)"; $temp = $false
  if (-not (Test-Path $hive)) {
    if (-not $isAdmin) { $notes += "account $acct : not readable without admin"; continue }
    $dat = Join-Path $p.LocalPath 'NTUSER.DAT'
    if (-not (Test-Path $dat)) { $notes += "account $acct : no NTUSER.DAT"; continue }
    & reg.exe load "HKU\SW_INV_$($p.SID)" $dat 2>&1 | Out-Null
    $hive = "Registry::HKEY_USERS\SW_INV_$($p.SID)"; $temp = $true
  }
  $found = Get-ItemProperty "$hive\Software\Microsoft\Windows\CurrentVersion\Uninstall\*" -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -and ($All -or (-not $_.SystemComponent -and -not $_.ParentKeyName)) }
  foreach ($f in $found) { $rows += [pscustomobject]@{ Name = $f.DisplayName.Trim(); Version = "$($f.DisplayVersion)"; Publisher = "$($f.Publisher)".Trim(); Installed = ''; SizeMB = $null; Where = "Account $acct" } }
  $notes += "account $acct : $(@($found).Count) per-user program(s)"
  if ($temp) { [gc]::Collect(); [gc]::WaitForPendingFinalizers(); & reg.exe unload "HKU\SW_INV_$($p.SID)" 2>&1 | Out-Null }
}

# the same program often registers in two places (e.g. 64- and 32-bit): keep one row per name+version
$rows = $rows | Sort-Object Name, Version -Unique | Sort-Object Name

# ---- NOT REGISTERED ANYWHERE: Start-menu shortcuts and program folders that match no row above.
# Catches portable apps, apps whose uninstaller is gone, and leftovers. Folders/shortcuts only;
# a folder counts when it contains at least one .exe (searched 3 levels deep).
$known = ($rows.Name | ForEach-Object { ($_ -replace '[^a-z0-9]', '').ToLower() }) | Where-Object { $_.Length -ge 4 }
function Unknown($label) {
  $k = ($label -replace '[^a-z0-9]', '').ToLower()
  if ($k.Length -lt 3) { return $false }
  -not ($known | Where-Object { $_.Contains($k) -or $k.Contains($_) })
}
$users = Get-ChildItem C:\Users -Directory -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -notmatch '^(Default|Default User|All Users|TEMP|UMFD|DefaultAppPool)' }
$lnkDirs = @('C:\ProgramData\Microsoft\Windows\Start Menu\Programs') + ($users | ForEach-Object { Join-Path $_.FullName 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs' })
$extra = @()
foreach ($d in $lnkDirs) {
  if (-not (Test-Path $d -ErrorAction SilentlyContinue)) { continue }
  Get-ChildItem $d -Recurse -Filter *.lnk -ErrorAction SilentlyContinue 2>$null | Where-Object { $_.BaseName -notmatch '(?i)uninstall|readme|help|website|manual|license' } |
    Where-Object { Unknown $_.BaseName } |
    ForEach-Object { $extra += [pscustomobject]@{ Kind = 'Start-menu shortcut'; Name = $_.BaseName; Path = $_.FullName } }
}
$folderRoots = @('C:\Users\Public', 'C:\ProgramData', $env:ProgramFiles, ${env:ProgramFiles(x86)}) +
  ($users | ForEach-Object { (Join-Path $_.FullName 'AppData\Local\Programs'), (Join-Path $_.FullName 'AppData\Local'), (Join-Path $_.FullName 'AppData\Roaming') })
foreach ($r in $folderRoots | Where-Object { Test-Path $_ -ErrorAction SilentlyContinue }) {
  Get-ChildItem $r -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch '^(Microsoft|Windows|Packages|Temp|Programs|Common Files|Microsoft\..*|WindowsApps|Package Cache|regid\..*|Comms|ConnectedDevicesPlatform|D3DSCache|CrashDumps|Desktop|Documents|Downloads|Music|Pictures|Videos|Public.*|Intel|NVIDIA.*|AMD|ssh|USOShared|USOPrivate|SoftwareDistribution)$' } |
    Where-Object { Unknown $_.Name } |
    Where-Object { Get-ChildItem $_.FullName -Recurse -Depth 3 -Filter *.exe -ErrorAction SilentlyContinue | Select-Object -First 1 } |
    ForEach-Object { $extra += [pscustomobject]@{ Kind = 'Program folder, not registered'; Name = $_.Name; Path = $_.FullName } }
}
$extra = $extra | Sort-Object Kind, Name -Unique
$out = Join-Path $PSScriptRoot "inventory\installed-software-$(Get-Date -Format yyyy-MM-dd).csv"
New-Item -ItemType Directory -Force (Split-Path $out) | Out-Null
$rows | Export-Csv $out -NoTypeInformation -Encoding utf8
$rows | Format-Table Name, Version, Publisher, Installed, SizeMB, Where -AutoSize | Out-String -Width 220
"{0} programs ({1}). Saved: {2}" -f $rows.Count, (($rows | Group-Object Where | ForEach-Object { "$($_.Count) $($_.Name)" }) -join ', '), $out
''
'Other accounts:'; $notes | ForEach-Object { "  $_" }
$out2 = $out -replace '\.csv$', '-unregistered.csv'
$extra | Export-Csv $out2 -NoTypeInformation -Encoding utf8
''
"Not in any software list ($(@($extra).Count)): portable apps, apps whose uninstaller is gone, or leftovers"
$extra | Format-Table Kind, Name, Path -AutoSize | Out-String -Width 220
"Saved: $out2"
