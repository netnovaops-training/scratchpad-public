<#
.SYNOPSIS
  One-way copy of files/folders from the PC onto SHAREDRIVE (the USB on the Pi 5), which the NUC's Abyss Find
  shows as the source "share-drive".

.DESCRIPTION
  2026-11-24, Jason: "I pick, a script copies". The PC only ever PUSHES: the key ~/.ssh/sharedrive_publish logs in
  as the Pi user "sharepub", whose authorized_keys line forces `rrsync -wo -munge /srv/sharedrive`
  (from the PC's Tailscale address only). So this key can write into /srv/sharedrive and nothing else, and cannot
  read anything back. The Pi host key is pinned in ~/.ssh/sharedrive_known_hosts.
  Uses MSYS2's rsync + ssh (C:\msys64\usr\bin, installed 2026-11-24 with pacman -S rsync openssh).
  Every run appends to publish.log next to this script.

.EXAMPLE
  .\publish.ps1 -Path "D:\x\guide.pdf","D:\x\demo.mp4" -To Novashare     # files into the folder Novashare
  .\publish.ps1 -Path "D:\Photos\Trip" -To Holidays                       # a folder (copied as Holidays\Trip)
  .\publish.ps1 -Pick -To Novashare                                         # choose files in a window
  .\publish.ps1 -PickFolder                                                 # choose one folder in a window
  .\publish.ps1 -Unpublish "Novashare/test.txt"                             # remove one published item
  .\publish.ps1 -List                                                       # what is on the drive (via the NUC)
#>
[CmdletBinding()]
param(
  [string[]]$Path,
  [string]$To = "",
  [switch]$Pick,
  [switch]$PickFolder,
  [string[]]$Unpublish,
  [switch]$List
)
$ErrorActionPreference = "Stop"

$PiHost   = "100.64.0.11"
$PiPort   = 1143
$PiUser   = "sharepub"
$Msys     = "C:\msys64\usr\bin"
$Rsync    = Join-Path $Msys "rsync.exe"
$Ssh      = Join-Path $Msys "ssh.exe"
$Cygpath  = Join-Path $Msys "cygpath.exe"
$Key      = Join-Path $HOME ".ssh\sharedrive_publish"
$Known    = Join-Path $HOME ".ssh\sharedrive_known_hosts"
$LogFile  = Join-Path $PSScriptRoot "publish.log"

function Write-Log([string]$msg) {
  $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
  Add-Content -Path $LogFile -Value $line
  Write-Host $line
}

function ConvertTo-Msys([string]$p) { (& $Cygpath -u $p).Trim() }

# A drive-relative path: forward slashes, no "..", ".", empty or hidden parts, no rsync wildcards, not absolute.
function Test-RelPath([string]$p, [switch]$AllowEmpty) {
  $p = $p.Replace("\", "/").Trim("/")
  if ($p -eq "") { if ($AllowEmpty) { return "" } throw "empty path" }
  if ($p -match '[\*\?\[\]:]' -or $p.Contains([char]0)) { throw "not allowed in a drive path: $p" }
  foreach ($part in $p.Split("/")) {
    if ($part -eq "" -or $part -eq "." -or $part -eq ".." -or $part.StartsWith(".") -or $part -eq "lost+found") {
      throw "not allowed in a drive path: $p"
    }
  }
  return $p
}

foreach ($f in @($Rsync, $Ssh, $Cygpath, $Key, $Known)) {
  if (-not (Test-Path $f)) { throw "missing: $f (see D:\scratchpad\nuc\sharedrive\README.md)" }
}
$sshCmd = "'{0}' -i '{1}' -p {2} -o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile='{3}'" -f `
  (ConvertTo-Msys $Ssh), (ConvertTo-Msys $Key), $PiPort, (ConvertTo-Msys $Known)
$common = @("-rt", "--chmod=D755,F644", "-p", "--no-links", "--out-format=%o %n (%l bytes)", "-e", $sshCmd)

if ($List) {
  # The publish key cannot read (write-only). The listing comes from the NUC, the drive's only reader.
  $rel = Test-RelPath $To -AllowEmpty
  $page = (& ssh server@100.64.0.10 "curl -s -m10 http://${PiHost}:8790/$rel/") -join "`n"
  if ($LASTEXITCODE -ne 0) { throw "listing failed (NUC or Pi unreachable)" }
  [regex]::Matches($page, 'href="\./([^"?]+)"') | ForEach-Object { [uri]::UnescapeDataString($_.Groups[1].Value) }
  exit 0
}

if ($Unpublish) {
  $empty = Join-Path $env:TEMP ("sharedrive-empty-" + [guid]::NewGuid())
  New-Item -ItemType Directory -Path $empty | Out-Null
  try {
    foreach ($u in $Unpublish) {
      $rel = Test-RelPath $u
      $parent = ($rel -replace '/?[^/]+$', '')
      $leaf = ($rel -split '/')[-1]
      # Sync an EMPTY folder onto the parent with a filter that matches ONLY this one item: --delete then removes
      # just it (excluded names are never deleted). The forced rrsync keeps it inside /srv/sharedrive.
      $dest = "${PiUser}@${PiHost}:" + $(if ($parent) { "$parent/" } else { "./" })
      $out = & $Rsync @common --delete "--include=/$leaf" "--include=/$leaf/***" "--exclude=*" `
        ((ConvertTo-Msys $empty) + "/") $dest 2>&1
      if ($LASTEXITCODE -ne 0) { Write-Log "UNPUBLISH FAILED $rel : $out"; exit $LASTEXITCODE }
      $out | ForEach-Object { Write-Log "removed  $_" }
      Write-Log "unpublished $rel"
    }
  } finally { Remove-Item -LiteralPath $empty -Recurse -Force }
  exit 0
}

if (($Pick -or $PickFolder) -and -not [Environment]::UserInteractive) {
  # 2026-11-24: Jason ran -Pick in a remote (SSH) PowerShell, where Windows forms can't open ("not running in
  # UserInteractive mode"). Fallback: pick in the console instead.
  $dir = (Read-Host "Folder to pick from, or one file (e.g. D:\Documents)").Trim().Trim('"')
  # A file typed here is published as-is (Jason typed a file path 11-24 and got "no such folder").
  if (Test-Path -LiteralPath $dir -PathType Leaf) { $Path = @((Resolve-Path -LiteralPath $dir).ProviderPath); $Pick = $false; $PickFolder = $false }
  elseif (-not (Test-Path -LiteralPath $dir -PathType Container)) { Write-Host "no such folder or file"; exit 1 }
}
if (($Pick -or $PickFolder) -and -not [Environment]::UserInteractive) {
  if ($PickFolder) { $Path = @((Resolve-Path -LiteralPath $dir).ProviderPath) }
  else {
    $list = @(Get-ChildItem -LiteralPath $dir -File | Sort-Object Name)
    if (-not $list) { Write-Host "no files there"; exit 0 }
    for ($i = 0; $i -lt $list.Count; $i++) { "{0,3}  {1}  ({2:N0} KB)" -f ($i + 1), $list[$i].Name, ($list[$i].Length / 1KB) }
    $ans = Read-Host "Numbers to publish (e.g. 1,3,5) or 'all'"
    if ($ans -eq 'all') { $Path = @($list.FullName) }
    else { $Path = @($ans -split '[,\s]+' | Where-Object { $_ -match '^\d+$' } | ForEach-Object { $list[[int]$_ - 1].FullName } | Where-Object { $_ }) }
    if (-not $Path) { Write-Host "nothing picked"; exit 0 }
  }
  $Pick = $false; $PickFolder = $false
}
if ($Pick -or $PickFolder) {
  Add-Type -AssemblyName System.Windows.Forms
  if ($PickFolder) {
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    $dlg.Description = "Folder to publish to SHAREDRIVE"
    if ($dlg.ShowDialog() -ne "OK") { Write-Host "nothing picked"; exit 0 }
    $Path = @($dlg.SelectedPath)
  } else {
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Multiselect = $true; $dlg.Title = "Files to publish to SHAREDRIVE"
    if ($dlg.ShowDialog() -ne "OK") { Write-Host "nothing picked"; exit 0 }
    $Path = $dlg.FileNames
  }
}
if (-not $Path) { Get-Help $MyInvocation.MyCommand.Path -Examples; exit 1 }

$toRel = Test-RelPath $To -AllowEmpty
# @( ) is load-bearing: with ONE path a bare foreach returns a plain string, and `@sources` then splats it
# character by character, so rsync got "/" (the whole MSYS2 root) as a source. Seen 2026-11-24 on the first
# one-file test: 772 MB of C:\msys64 landed in Novashare before it was stopped and removed. Guard below too.
$sources = @(foreach ($p in $Path) {
  $full = (Resolve-Path -LiteralPath $p).ProviderPath
  $item = Get-Item -LiteralPath $full
  if ($item.Name.StartsWith(".")) { throw "hidden names are not published: $full" }
  if ($item.PSIsContainer -and $item.Parent -eq $null) { throw "a whole drive is not published: $full" }
  ConvertTo-Msys $full.TrimEnd("\")
})
foreach ($s in $sources) {
  if ($s -notmatch '^/[a-z]/.+') { throw "refusing source '$s' (expected /<drive>/<path>)" }
}
if ($sources.Count -ne @($Path).Count) { throw "source count mismatch" }
$dest = "${PiUser}@${PiHost}:" + $(if ($toRel) { "$toRel/" } else { "./" })
Write-Log ("publish -> /{0}  from: {1}" -f $toRel, ($Path -join "; "))
$out = & $Rsync @common --mkpath @sources $dest 2>&1
$code = $LASTEXITCODE
$out | ForEach-Object { Write-Log "  $_" }
if ($code -ne 0) { Write-Log "PUBLISH FAILED (rsync exit $code)"; exit $code }
Write-Log "publish ok"
