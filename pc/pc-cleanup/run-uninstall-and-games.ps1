# One elevated window: the remaining uninstalls (auto-clicked), then games-off. 2026-11-04.
$env:CHAINED = '1'
& (Join-Path $PSScriptRoot 'uninstall-the-rest.ps1')
& (Join-Path $PSScriptRoot 'games-off.ps1')
Add-Content (Join-Path $PSScriptRoot 'games-off.log') "=== chain finished $(Get-Date -Format HH:mm:ss)" -Encoding utf8
Read-Host 'All done. Press Enter to close'
