<#
  remove-php-from-c-root.ps1 -- finish removing PHP from C:\ (2026-11-11).

  A PHP 8.4 zip had been unpacked straight into C:\ (php.exe, php8.dll, ICU/OpenSSL DLLs, ...).
  The folders (ext, extras, lib, dev) and php.ini were moved to D: without trouble, but the 29 loose
  files in C:\ need admin to delete. Every one was already copied to
  D:\pc-cleanup-backups\php-from-C-2026-11-11\C-root, and this script only deletes a file
  if the D: copy is byte-for-byte the same, so nothing is lost.
#>
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
  [Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
  Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', "`"$PSCommandPath`""
  exit
}
$backup = 'D:\pc-cleanup-backups\php-from-C-2026-11-11\C-root'
$files = 'README.md', 'deplister.exe', 'glib-2.dll', 'gmodule-2.dll', 'icudt75.dll', 'icuin75.dll', 'icuio75.dll',
  'icuuc75.dll', 'libcrypto-3-x64.dll', 'libenchant2.dll', 'libpq.dll', 'libsasl.dll', 'libsodium.dll', 'libsqlite3.dll',
  'libssh2.dll', 'libssl-3-x64.dll', 'license.txt', 'news.txt', 'nghttp2.dll', 'phar.phar.bat', 'pharcommand.phar',
  'php-cgi.exe', 'php.exe', 'php8.dll', 'php8embed.lib', 'php8phpdbg.dll', 'phpdbg.exe', 'readme-redist-bins.txt', 'snapshot.txt'
$log = 'C:\Logs\php-removal.log'
$ok = 0; $skip = 0
foreach ($f in $files) {
  $c = Join-Path 'C:\' $f; $d = Join-Path $backup $f
  if (-not (Test-Path $c)) { continue }
  if ((Test-Path $d) -and ((Get-FileHash $c).Hash -eq (Get-FileHash $d).Hash)) {
    Remove-Item -LiteralPath $c -Force; $ok++
  } else { Add-Content $log "kept $c (no matching copy on D:)"; $skip++ }
}
$msg = "{0}  removed {1} PHP files from C:\, kept {2}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $ok, $skip
Add-Content $log $msg; Write-Host $msg
Read-Host 'Press Enter to close'
