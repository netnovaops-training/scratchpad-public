# people-mirror.ps1: keeps the PC's copy of the sign-in account list the same as the NUC's.
#
# The NUC's list (~/nuc-homelab/auth-data/people.json) is the only one people are edited in.
# The PC's apps that read the list (auth, landing, kitchen, hermes) read a copy at
# C:\Jasons New Site\www\ai-stack\site-data\auth\people.json; this script refreshes that copy.
#
# How: a key made only for this job (%USERPROFILE%\.ssh\people_mirror) is pinned on the NUC
# to one command (cat the list), so it can read that file and do nothing else.
# Safety: the new copy replaces the old one only if it is valid JSON with at least one
# account that has an email; otherwise the old copy stays and the reason is logged.
# It never prints or logs the list's contents.
# Run by the Scheduled Task "People mirror (NUC to PC)" every 2 minutes.

$ErrorActionPreference = 'Stop'
$Dest = 'C:\Jasons New Site\www\ai-stack\site-data\auth\people.json'  # was www\Admin\auth until 2026-11-12
$Key  = Join-Path $env:USERPROFILE '.ssh\people_mirror'
$Ssh  = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
$Log  = 'C:\Jasons New Site\www\ai-stack\logs\people-mirror.log'

function Write-Log([string]$msg) {
    $line = '{0}  {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $msg
    $dir = Split-Path $Log
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null }
    Add-Content -Path $Log -Value $line -Encoding UTF8
    # Keep the log short: the last 500 lines.
    $all = Get-Content $Log
    if ($all.Count -gt 600) { $all | Select-Object -Last 500 | Set-Content $Log -Encoding UTF8 }
}

$tmp = "$Dest.mirror-tmp"
try {
    $text = & $Ssh -T -i $Key -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 server@192.168.1.86 2>$null
    if ($LASTEXITCODE -ne 0) { throw "ssh exit code $LASTEXITCODE" }
    $text = ($text -join "`n")
    $list = $text | ConvertFrom-Json
    $n = @($list).Count
    if ($n -lt 1) { throw 'the NUC list came back empty' }
    foreach ($p in @($list)) { if (-not $p.email) { throw 'an entry has no email' } }

    $old = if (Test-Path $Dest) { [IO.File]::ReadAllText($Dest) } else { '' }
    if ($old -eq $text) { exit 0 }   # nothing changed: no write, no log line

    # Write next to the file, then swap it in, so readers never see half a file.
    [IO.File]::WriteAllText($tmp, $text, (New-Object Text.UTF8Encoding($false)))
    Move-Item -Path $tmp -Destination $Dest -Force
    Write-Log "updated the PC copy from the NUC ($n accounts)"
} catch {
    if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    Write-Log "kept the old PC copy: $($_.Exception.Message)"
    exit 1
}
