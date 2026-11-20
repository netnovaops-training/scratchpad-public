# start-dev.ps1 - start a work session: fresh pull, then VS Code, reachable from the phone (2026-11-19).
#
#   powershell -ExecutionPolicy Bypass -File D:\scratchpad\tools\start-dev.ps1            open VS Code here
#   powershell -ExecutionPolicy Bypass -File D:\scratchpad\tools\start-dev.ps1 -Folder D:\scratchpad
#   powershell -ExecutionPolicy Bypass -File D:\scratchpad\tools\start-dev.ps1 -SetupTunnel   one time, as admin
#
# Why: Jason 2026-11-19: on lunch or a break, resume a session from the phone without setting up again.
# Windows has no mosh server, so the phone side is VS Code Remote Tunnels: the PC dials OUT to Microsoft's
# tunnel service (no open port, no port forward), and the phone opens https://vscode.dev/tunnel/<name>
# in a browser. The tunnel runs as a Windows service, so it survives the phone losing signal; the editor,
# terminals and a Claude Code session in them keep running on the PC and are there again on reconnect.
# Claude Code sessions can also be driven from the Claude phone app with /remote-control.
#
# Steps: 1) fast-forward main in every clone (tools/sync_main.sh, never touches feature branches);
# 2) open VS Code on -Folder. -SetupTunnel installs the tunnel service once: it asks you to sign in with
# GitHub in the browser (device code), then runs at every boot.
param(
    [string]$Folder = "D:\scratchpad",
    [string]$TunnelName = "supernova",
    [switch]$SetupTunnel
)
$ErrorActionPreference = "Stop"
$bash = "C:\Program Files\Git\bin\bash.exe"
$code = "$env:LOCALAPPDATA\Programs\Microsoft VS Code\bin\code.cmd"

if ($SetupTunnel) {
    # One time. The service runs as this user; sign-in is a device code shown here.
    & $code tunnel user login --provider github
    & $code tunnel service install --accept-server-license-terms --name $TunnelName
    & $code tunnel status
    Write-Host "Phone: open https://vscode.dev/tunnel/$TunnelName and sign in with the same GitHub account."
    return
}

Write-Host "== fresh pull (main only, fast-forward)"
& $bash -lc "bash /d/scratchpad/tools/sync_main.sh"

Write-Host "== VS Code: $Folder"
& $code $Folder

$st = & $code tunnel status 2>$null | ConvertFrom-Json
if (-not $st.service_installed) {
    Write-Host "Tunnel not set up yet: run this script once with -SetupTunnel to reach VS Code from the phone."
} else {
    Write-Host "Phone: https://vscode.dev/tunnel/$TunnelName"
}
