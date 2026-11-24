#Requires -RunAsAdministrator
<#
pc_share_nucreader.ps1 (2026-11-24, Abyss step 3 "Find"): share four PC folders READ-ONLY with the NUC.

Jason runs this ONCE in an elevated PowerShell (right-click > Run as administrator):
    powershell -NoProfile -ExecutionPolicy Bypass -File D:\scratchpad\tools\pc_share_nucreader.ps1
It is safe to run again (it reuses the Proton Pass item and fixes anything that drifted).

What it does:
  1. Password: a new random 32-character password (letters + digits), or the existing one if the Proton Pass item
     already exists. It is saved to Proton Pass FIRST (vault "NUC services", item "PC nucreader SMB (2026-11-24)")
     with `pass-cli item create login --from-template -` on stdin, then read back and compared (never printed).
     The NUC reads it at run time through secrets.refs + ~/bin/dc (PC_NUCREADER_PASS).
  2. Local user `nucreader`: no password change, never expires, not in Administrators; denied local and Remote
     Desktop sign-in (user rights), so it can only read the shares over the network.
  3. Four SMB shares, READ access for nucreader only (no Everyone), SMB encryption required, access-based listing:
        NucRead-Documents, NucRead-Downloads, NucRead-Videos, NucRead-Desktop
     plus a read-only NTFS grant (RX, inherited) for nucreader on each folder. Nothing else on the PC changes.
  4. Firewall: one rule allows SMB (TCP 445) ONLY from the NUC's Tailscale address 100.64.0.10 on the Tailscale
     adapter. The built-in "File and Printer Sharing (SMB-In)" rules were OFF on 2026-11-24 and stay off; the script
     warns if any other rule opens 445.

Notes:
  * Documents and Desktop are the OneDrive folders (Windows' real Documents/Desktop on this PC, checked
    2026-11-24: 85,946 and 466 files). Files that are "online-only" in OneDrive show in Find (name, size, date) but
    may fail to download from the NUC until OneDrive has them on this PC ("Always keep on this device").
    C:\Users\USER\Documents (340,813 files, mostly code) is NOT shared; pass -Documents to choose another folder.
  * Undo (all of it):
        Get-SmbShare NucRead-* | Remove-SmbShare -Force
        Remove-NetFirewallRule -DisplayName 'Abyss Find: SMB from the NUC (Tailscale only)'
        foreach ($f in <the four folders>) { icacls $f /remove nucreader }
        Remove-LocalUser nucreader      (then delete the Pass item by hand if wanted)
#>
param(
    [string]$Documents = 'C:\Users\USER\OneDrive - Empire State University\Documents',
    [string]$Downloads = 'C:\Users\USER\Downloads',
    [string]$Videos    = 'C:\Users\USER\Videos',
    [string]$Desktop   = 'C:\Users\USER\OneDrive - Empire State University\Desktop',
    [string]$NucIp     = '100.64.0.10'
)
$ErrorActionPreference = 'Stop'
$User      = 'nucreader'
$Vault     = 'NUC services'
$ItemTitle = 'PC nucreader SMB (2026-11-24)'
$RuleName  = 'Abyss Find: SMB from the NUC (Tailscale only)'
$Shares    = [ordered]@{ 'NucRead-Documents' = $Documents; 'NucRead-Downloads' = $Downloads;
                         'NucRead-Videos' = $Videos; 'NucRead-Desktop' = $Desktop }

function Say($t) { Write-Host "  $t" }
function Step($t) { Write-Host "`n== $t" -ForegroundColor Cyan }

# ---- 0. checks before changing anything
Step 'Checks'
foreach ($k in $Shares.Keys) {
    if (-not (Test-Path -LiteralPath $Shares[$k] -PathType Container)) { throw "Folder not found: $($Shares[$k])" }
}
if (-not (Get-Command pass-cli -ErrorAction SilentlyContinue)) { throw 'pass-cli not found (Proton Pass CLI).' }
if (-not $env:PROTON_PASS_KEY_PROVIDER) { $env:PROTON_PASS_KEY_PROVIDER = 'fs' }   # same as the user setting
if ((Get-Service LanmanServer).Status -ne 'Running') { throw 'The Server service (LanmanServer) is not running.' }
if ((Get-SmbServerConfiguration).EnableSMB1Protocol) { Say 'WARNING: SMB1 is on. Consider turning it off (not changed here).' }
Say 'Folders, pass-cli and the Server service are ready.'

# ---- 1. password: from Pass if the item exists, else new and saved to Pass first
Step 'Password (Proton Pass)'
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)       # no BOM on stdin to pass-cli
$pw = $null
try {
    $pw = (& pass-cli item view --vault-name $Vault --item-title $ItemTitle --field password 2>$null | Out-String).Trim()
} catch { $pw = $null }
if ($pw -and $pw.Length -ge 24) {
    Say "Reusing the existing Pass item '$ItemTitle' (length $($pw.Length))."
} else {
    $chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'.ToCharArray()
    $bytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $pw = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
    $tpl = [ordered]@{ title = $ItemTitle; username = $User; email = $null; password = $pw; totp_uri = $null;
                       urls = @("smb://100.64.0.13") } | ConvertTo-Json -Compress
    $out = $tpl | & pass-cli item create login --from-template - --vault-name $Vault 2>&1
    $code = $LASTEXITCODE
    $tpl = $null; $out = $null
    if ($code -ne 0) { throw "pass-cli could not save the item (exit $code). Nothing else was changed." }
    $back = (& pass-cli item view --vault-name $Vault --item-title $ItemTitle --field password 2>$null | Out-String).Trim()
    if ($back -ne $pw) { $back = $null; throw 'The saved password does not read back the same. Nothing else was changed.' }
    $back = $null
    Say "Saved a new 32-character password to '$Vault' / '$ItemTitle' (read back and matched; not shown)."
}
$secure = ConvertTo-SecureString $pw -AsPlainText -Force
$pw = $null

# ---- 2. the local user
Step "Local user $User"
$u = Get-LocalUser -Name $User -ErrorAction SilentlyContinue
if ($u) {
    Set-LocalUser -Name $User -Password $secure -PasswordNeverExpires $true -UserMayChangePassword $false `
        -AccountNeverExpires -Description 'Read-only SMB for the NUC (Abyss Find)'
    Enable-LocalUser -Name $User
    Say 'Existed: password set from Pass, settings checked.'
} else {
    New-LocalUser -Name $User -Password $secure -PasswordNeverExpires -UserMayNotChangePassword -AccountNeverExpires `
        -FullName 'NUC reader' -Description 'Read-only SMB for the NUC (Abyss Find)' | Out-Null
    Say 'Created.'
}
$secure = $null
foreach ($gsid in 'S-1-5-32-544', 'S-1-5-32-555') {          # Administrators, Remote Desktop Users
    if (Get-LocalGroupMember -SID $gsid -ErrorAction SilentlyContinue | Where-Object { $_.Name -like "*\$User" }) {
        Remove-LocalGroupMember -SID $gsid -Member $User; Say "Removed from group $gsid."
    }
}
# Deny local and Remote Desktop sign-in (network access to the shares still works).
$userSid = (Get-LocalUser -Name $User).SID.Value
$tmp = Join-Path $env:TEMP ('nucreader-rights-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tmp | Out-Null
try {
    secedit /export /cfg "$tmp\before.inf" /areas USER_RIGHTS /quiet | Out-Null
    $inf = Get-Content "$tmp\before.inf"
    $changed = $false
    foreach ($right in 'SeDenyInteractiveLogonRight', 'SeDenyRemoteInteractiveLogonRight') {
        $line = $inf | Where-Object { $_ -match "^$right\s*=" }
        if (-not $line) { $inf = $inf -replace '^\[Privilege Rights\]$', "[Privilege Rights]`r`n$right = *$userSid"; $changed = $true }
        elseif ($line -notmatch [regex]::Escape($userSid)) {
            $inf = $inf -replace "^($right\s*=.*)$", "`$1,*$userSid"; $changed = $true
        }
    }
    if ($changed) {
        $inf | Set-Content "$tmp\after.inf" -Encoding Unicode
        secedit /configure /db "$tmp\rights.sdb" /cfg "$tmp\after.inf" /areas USER_RIGHTS /quiet | Out-Null
        Say 'Denied local and Remote Desktop sign-in.'
    } else { Say 'Local and Remote Desktop sign-in already denied.' }
} finally { Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue }

# ---- 3. shares (read-only for nucreader only) + NTFS read grant
Step 'Shares (read-only)'
$acct = "$env:COMPUTERNAME\$User"
foreach ($name in $Shares.Keys) {
    $path = $Shares[$name]
    & icacls $path /grant "${User}:(OI)(CI)RX" /T /C /Q | Out-Null     # read + list, inherited by everything below
    $s = Get-SmbShare -Name $name -ErrorAction SilentlyContinue
    if ($s -and $s.Path -ne $path) { Remove-SmbShare -Name $name -Force; $s = $null }
    if (-not $s) {
        New-SmbShare -Name $name -Path $path -ReadAccess $acct -FolderEnumerationMode AccessBased -EncryptData $true `
            -CachingMode None -Description 'Read-only for the NUC (Abyss Find)' | Out-Null
    } else {
        Set-SmbShare -Name $name -EncryptData $true -FolderEnumerationMode AccessBased -CachingMode None -Force
    }
    # Exactly one share permission: nucreader, Read. Anything else (Everyone, Full, Change) is removed.
    foreach ($a in Get-SmbShareAccess -Name $name) {
        if ($a.AccountName -ne $acct -or $a.AccessRight -ne 'Read' -or $a.AccessControlType -ne 'Allow') {
            Revoke-SmbShareAccess -Name $name -AccountName $a.AccountName -Force | Out-Null
        }
    }
    if (-not (Get-SmbShareAccess -Name $name | Where-Object { $_.AccountName -eq $acct })) {
        Grant-SmbShareAccess -Name $name -AccountName $acct -AccessRight Read -Force | Out-Null
    }
    Say ("{0,-18} {1}  (share: {2} Read only)" -f $name, $path, $acct)
}

# ---- 4. firewall: SMB only from the NUC over Tailscale
Step 'Firewall'
Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -DisplayName $RuleName -Direction Inbound -Action Allow -Protocol TCP -LocalPort 445 `
    -RemoteAddress $NucIp -InterfaceAlias 'Tailscale' -Profile Any `
    -Description 'Abyss Find (2026-11-24): the NUC reads the NucRead-* shares. Made by pc_share_nucreader.ps1.' | Out-Null
Say "Allowed TCP 445 from $NucIp on the Tailscale adapter only."
$others = Get-NetFirewallRule -Direction Inbound -Enabled True -Action Allow | Where-Object { $_.DisplayName -ne $RuleName } |
    Where-Object { ($_ | Get-NetFirewallPortFilter).LocalPort -contains '445' }
if ($others) { Say ('WARNING: other enabled rules also open 445: ' + (($others | ForEach-Object DisplayName) -join '; ')) }
else { Say 'No other rule opens 445.' }

Step 'Done'
Say 'On the NUC, add this line to ~/nuc-homelab/secrets.refs (a reference, not the password):'
Say "  PC_NUCREADER_PASS=pass://$Vault/$ItemTitle/password"
Say 'Then follow the Abyss Find go-live steps (README-FIND.md).'
