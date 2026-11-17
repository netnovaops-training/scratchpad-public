<#
.SYNOPSIS
  SendTo-TeraCopy.ps1 -- hands the files you picked in Explorer to TeraCopy (copy or move) for one
  fixed destination. Called by the "Send to" shortcuts that Install-SendTo.ps1 makes. 2026-11-14, Claude.

.DESCRIPTION
  Explorer's Send to appends the selected paths AFTER the shortcut's own arguments, but TeraCopy wants
  `TeraCopy.exe Copy <source> <target>` (source first). So this wrapper writes the picked paths to a
  list file, one per line, and calls TeraCopy's documented list form:
      TeraCopy.exe Copy *"<list file>" "<target>" /OverwriteOlder
  (support.codesector.com, "TeraCopy command line": the * prefix marks a list file, one path per line;
  /OverwriteOlder = replace only when the source is newer). TeraCopy verifies after copying because
  Options.ini already has TestAlways=1 with the xxHash3-64 hash.
  List file encoding: UTF-16 with BOM. TeraCopy's docs do not say which encoding the list needs; the
  BOM form is the one Windows tools read most reliably. NOT YET TESTED with a non-English file name:
  try one (e.g. a Korean name) the first time.
#>
param(
  [Parameter(Mandatory)][ValidateSet('Copy', 'Move')][string]$Op,
  [Parameter(Mandatory)][string]$Dest,
  [Parameter(ValueFromRemainingArguments)][string[]]$Paths
)
$tc = 'C:\Program Files\TeraCopy\TeraCopy.exe'
if (-not $Paths) { return }
if (-not (Test-Path -LiteralPath $Dest)) {
  Add-Type -AssemblyName PresentationFramework
  [System.Windows.MessageBox]::Show("Can't reach $Dest.`nIs the NAS on and are you signed in to it?", 'Send to (TeraCopy)') | Out-Null
  return
}
$list = Join-Path $env:TEMP ("teracopy-sendto-{0}.txt" -f (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
[IO.File]::WriteAllLines($list, [string[]]$Paths, [Text.Encoding]::Unicode)
Start-Process -FilePath $tc -ArgumentList @($Op, "*`"$list`"", "`"$Dest`"", '/OverwriteOlder')
