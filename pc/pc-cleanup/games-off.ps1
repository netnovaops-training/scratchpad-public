<#
  games-off.ps1 -- Jason, 2026-11-04: "I don't play any games any more, so my PC is used for LLMs now".
  One elevated run, no clicking. Log: pc-cleanup\games-off.log. Nothing is deleted outright:
  folders are MOVED to a _To-delete-2026-11-04 folder on the same drive (instant, no copying);
  Jason empties those himself.
   1. Steam: shut it down, run its uninstaller silently (removes the client + Bongo Cat, Nightwater Demo,
      Steamworks redist from C:)
   2. The other Steam libraries (D:, E:, G:\SteamLibrary; G: holds Wallpaper Engine) -> to-delete
   3. EA app: only dead Start-menu shortcuts left (the app itself is gone) -> to-delete
   4. Shader caches (NVIDIA DXCache/GLCache, Windows D3DSCache, AMD caches): emptied into to-delete;
      they only speed up games, and anything that still needs one rebuilds it
   5. Xbox Game Bar / background game recording off (it hooks every full-screen app and holds RAM)
  Kept: Microsoft GameInput (controllers/input for non-game apps), the NVIDIA driver and app, CUDA.
#>
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $here 'games-off.log'
. (Join-Path $PSScriptRoot 'autoclick.ps1'); Disable-QuickEdit
function Say($m) { $l = "{0}  {1}" -f (Get-Date -Format 'HH:mm:ss'), $m; Add-Content $log $l -Encoding utf8; Write-Host $l }
function Bin($path) {   # same-drive to-delete folder
  $root = [IO.Path]::GetPathRoot($path); $b = Join-Path $root '_To-delete-2026-11-04'
  New-Item -ItemType Directory -Force $b | Out-Null; $b
}
function Move-ToBin($path) {
  if (-not (Test-Path -LiteralPath $path)) { return }
  $dest = Join-Path (Bin $path) (($path -replace '^[A-Z]:\\', '') -replace '\\', '__')
  try { Move-Item -LiteralPath $path -Destination $dest -ErrorAction Stop; Say "  moved $path -> $dest" }
  catch { Say "  could not move $path : $($_.Exception.Message)" }
}
Say '=== games-off start'

Say '1. Steam'
$steamDir = 'C:\Program Files (x86)\Steam'
if (Get-Process steam -ErrorAction SilentlyContinue) {
  Start-Process (Join-Path $steamDir 'steam.exe') '-shutdown'
  Get-Process steam -ErrorAction SilentlyContinue | Wait-Process -Timeout 60 -ErrorAction SilentlyContinue
  Get-Process steam*, steamwebhelper -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
if (Test-Path "$steamDir\uninstall.exe") {
  $p = Start-Process "$steamDir\uninstall.exe" '/S' -Wait -PassThru; Start-Sleep 10
  Say "  Steam uninstaller exit $($p.ExitCode)"
}
Move-ToBin $steamDir   # whatever the uninstaller leaves (it keeps steamapps on purpose)

Say '2. Other Steam libraries'
foreach ($l in 'D:\SteamLibrary', 'E:\SteamLibrary', 'G:\SteamLibrary') { Move-ToBin $l }

Say '3. EA leftovers'
Move-ToBin 'C:\ProgramData\Microsoft\Windows\Start Menu\Programs\EA'
Move-ToBin 'C:\ProgramData\Electronic Arts'
Move-ToBin 'C:\Users\USER\AppData\Local\Electronic Arts'

Say '4. Shader caches'
foreach ($c in 'C:\Users\USER\AppData\Local\NVIDIA\DXCache', 'C:\Users\USER\AppData\Local\NVIDIA\GLCache',
               'C:\Users\USER\AppData\Local\D3DSCache', 'C:\Users\USER\AppData\Local\AMD\DxCache',
               'C:\Users\USER\AppData\Local\AMD\DxcCache', 'C:\Users\USER\AppData\Local\AMD\GLCache',
               'C:\ProgramData\NVIDIA Corporation\NV_Cache') {
  if (Test-Path $c) {
    $mb = [math]::Round(((Get-ChildItem $c -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1MB)
    $dest = Join-Path (Bin $c) ('shader-cache__' + (($c -replace '^[A-Z]:\\', '') -replace '\\', '__'))
    New-Item -ItemType Directory -Force $dest | Out-Null
    $moved = 0; Get-ChildItem $c -Force -ErrorAction SilentlyContinue | ForEach-Object { try { Move-Item $_.FullName $dest -ErrorAction Stop; $moved++ } catch {} }
    Say "  $c : $mb MB, moved $moved item(s) (files in use stay; they're tiny)"
  }
}

Say '5. Xbox Game Bar / game recording off'
New-Item 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR' -Force | Out-Null
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\GameDVR' AllowGameDVR 0 -Type DWord
$u = 'Registry::HKEY_USERS\S-1-5-21-3991538369-352823140-2599934387-1001'
New-Item "$u\System\GameConfigStore" -Force | Out-Null; Set-ItemProperty "$u\System\GameConfigStore" GameDVR_Enabled 0 -Type DWord
New-Item "$u\Software\Microsoft\Windows\CurrentVersion\GameDVR" -Force | Out-Null; Set-ItemProperty "$u\Software\Microsoft\Windows\CurrentVersion\GameDVR" AppCaptureEnabled 0 -Type DWord
Get-AppxPackage -AllUsers Microsoft.XboxGamingOverlay -ErrorAction SilentlyContinue | Remove-AppxPackage -AllUsers -ErrorAction SilentlyContinue
Say "  game recording off; Game Bar removed: $(-not (Get-AppxPackage -AllUsers Microsoft.XboxGamingOverlay -ErrorAction SilentlyContinue))"

$sum = foreach ($d in 'C:', 'D:', 'E:', 'G:') { $b = "$d\_To-delete-2026-11-04"; if (Test-Path $b) { "{0} {1:N1} GB" -f $b, (((Get-ChildItem $b -Recurse -File -Force -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum) / 1GB) } }
Say "=== games-off finished. To-delete folders: $($sum -join '; ')"
if (-not $env:CHAINED) { Read-Host 'Done. Press Enter to close' }
