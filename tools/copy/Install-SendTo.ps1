<#
.SYNOPSIS
  Install-SendTo.ps1 -- adds "TC Copy to ..." and "TC Move to ..." entries to Explorer's right-click
  Send to menu, each handing the picked files to TeraCopy (verified copy) for one destination.
  2026-11-14, Claude. Re-run any time; -Remove takes them all out again.
  Shortcuts live in %APPDATA%\Microsoft\Windows\SendTo (per user, no admin needed).
  On Windows 11 Send to is under "Show more options" (or Shift+right-click).
#>
param([switch]$Remove)
$sendTo = [Environment]::GetFolderPath('SendTo')
$wrapper = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) 'SendTo-TeraCopy.ps1'
$targets = [ordered]@{
  'NAS Pictures'       = '\\192.168.1.68\Pictures'
  'NAS photo'          = '\\192.168.1.68\photo'
  'NAS Computer backup'= '\\192.168.1.68\Computer backup'
  'NAS Music backup'   = '\\192.168.1.68\home\New Music Backup'
  'NAS Website backup' = '\\192.168.1.68\Website backup'
  'D Inbox'            = 'D:\Inbox'
}
Get-ChildItem $sendTo -Filter 'TC * to *.lnk' -ErrorAction SilentlyContinue | Remove-Item
if ($Remove) { Write-Output 'Removed the TeraCopy Send to entries.'; return }
New-Item -ItemType Directory -Force 'D:\Inbox' | Out-Null
$sh = New-Object -ComObject WScript.Shell
foreach ($op in 'Copy', 'Move') {
  foreach ($k in $targets.Keys) {
    $lnk = $sh.CreateShortcut((Join-Path $sendTo "TC $op to $k.lnk"))
    $lnk.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $lnk.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$wrapper`" -Op $op -Dest `"$($targets[$k])`""
    $lnk.IconLocation = 'C:\Program Files\TeraCopy\TeraCopy.exe,0'
    $lnk.WindowStyle = 7   # minimized
    $lnk.Save()
  }
}
Write-Output "Added $(2 * $targets.Count) Send to entries in $sendTo"
