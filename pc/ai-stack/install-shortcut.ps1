<#
    Put a Hermes shortcut on the Desktop, with the icon.

    Also adds it to the Start menu, so typing "hermes" into Start finds it.

    Run it again any time -- it overwrites its own shortcuts and touches
    nothing else. Remove them with:  install-shortcut.cmd -Remove
#>

param(
    [switch]$Remove,
    [switch]$Plain,          # force plain console even if Windows Terminal exists
    [switch]$NoStartMenu
)

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

function Ok   { param($m,$d="") Write-Host "  [ok]   $m" -ForegroundColor Green; if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Bad  { param($m,$d="") Write-Host "  [FAIL] $m" -ForegroundColor Red;   if($d){Write-Host "         $d" -ForegroundColor DarkGray} }
function Warn { param($m,$d="") Write-Host "  [warn] $m" -ForegroundColor Yellow;if($d){Write-Host "         $d" -ForegroundColor DarkGray} }

Write-Host ""
Write-Host "  Hermes desktop shortcut" -ForegroundColor Yellow
Write-Host "  -----------------------" -ForegroundColor DarkGray

$desktop   = [Environment]::GetFolderPath("Desktop")
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"
$targets   = @((Join-Path $desktop "Hermes.lnk"))
if (-not $NoStartMenu) { $targets += (Join-Path $startMenu "Hermes.lnk") }

# --- remove ------------------------------------------------------------------
if ($Remove) {
    foreach ($t in $targets) {
        if (Test-Path $t) { Remove-Item $t -Force; Ok "removed $t" }
        else { Warn "not there: $t" }
    }
    Write-Host ""
    Read-Host "  Press Enter to close"
    exit 0
}

# --- the pieces --------------------------------------------------------------
$icon   = Join-Path $PSScriptRoot "assets\hermes.ico"
$launch = Join-Path $PSScriptRoot "hermes-launch.ps1"

if (-not (Test-Path $launch)) { Bad "hermes-launch.ps1 is missing" "re-extract the update"; Read-Host "  Enter"; exit 1 }
if (-not (Test-Path $icon))   { Warn "assets\hermes.ico is missing" "the shortcut will use the default console icon" }

# Windows Terminal renders the emoji and the rounded box properly; the legacy
# console draws several of them as hollow squares. Prefer wt.exe when it's here.
$wt = (Get-Command wt.exe -ErrorAction SilentlyContinue)
$useWt = ($wt -and -not $Plain)

if ($useWt) {
    $target = $wt.Source
    $args   = "--title Hermes -d `"$PSScriptRoot`" powershell -NoProfile -ExecutionPolicy Bypass -File `"$launch`""
    $how    = "Windows Terminal (better emoji and box drawing)"
} else {
    $target = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
    $args   = "-NoProfile -ExecutionPolicy Bypass -File `"$launch`""
    $how    = "the standard console"
    if ($Plain) { $how += " (-Plain)" }
    else { Warn "Windows Terminal not found" "emoji may render as boxes; set HERMES_ASCII=1 in .env if so" }
}

# --- write them --------------------------------------------------------------
$shell = New-Object -ComObject WScript.Shell
foreach ($path in $targets) {
    try {
        $dir = Split-Path $path -Parent
        if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }

        $lnk = $shell.CreateShortcut($path)
        $lnk.TargetPath       = $target
        $lnk.Arguments        = $args
        $lnk.WorkingDirectory = $PSScriptRoot
        $lnk.Description      = "Hermes - your local AI agent"
        if (Test-Path $icon) { $lnk.IconLocation = "$icon,0" }
        $lnk.WindowStyle      = 1        # normal window
        $lnk.Save()

        Ok "created $path"
    } catch {
        Bad "couldn't create $path" $_.Exception.Message
    }
}

Write-Host ""
Ok "launches through $how"
Write-Host ""
Write-Host "  Double-click Hermes on your Desktop. It starts Docker Desktop if" -ForegroundColor DarkGray
Write-Host "  it isn't running, starts the container if it's stopped, waits for" -ForegroundColor DarkGray
Write-Host "  the router, then opens the agent." -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Windows may not refresh the icon straight away. If it looks wrong," -ForegroundColor DarkGray
Write-Host "  press F5 on the Desktop." -ForegroundColor DarkGray
Write-Host ""
Read-Host "  Press Enter to close"
