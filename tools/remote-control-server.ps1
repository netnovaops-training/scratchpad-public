# remote-control-server.ps1 - keep Claude Code Remote Control running on the PC, always (2026-11-19).
#
#   Started at logon by the scheduled task "Claude Remote Control" (hidden window). By hand:
#   powershell -ExecutionPolicy Bypass -File D:\scratchpad\tools\remote-control-server.ps1
#
# Why: Jason 2026-11-19: "when I went to bed the summary and session stopped ... I want it when I wake
# up and it not responded, my session doesn't stop." Checked that night: the PC did NOT sleep or reboot
# (sleep = never on AC, no sleep/restart events), so the session ended on its own. `claude remote-control`
# is a persistent server: the phone app (or claude.ai/code) can open new sessions or continue old ones
# whenever it is running. This script runs it forever and restarts it within 15 s if it exits.
# Log: D:\scratchpad\.git\remote-control.log (outside the tracked tree).
$ErrorActionPreference = "Continue"
$claude = "$env:USERPROFILE\.local\bin\claude.exe"
$workDir = "C:\Users\USER\Projects\deps-wt\jasons-site\ai-stack"   # same folder the old task used
$log = "D:\scratchpad\.git\remote-control.log"

Set-Location $workDir
while ($true) {
    Add-Content $log "$(Get-Date -Format s) starting claude remote-control in $workDir"
    # auto permission mode = the same mode Jason uses at the desk; the phone shows any prompt that still asks.
    & $claude remote-control --name supernova --permission-mode auto *>> $log
    Add-Content $log "$(Get-Date -Format s) exited with $LASTEXITCODE; restarting in 15 s"
    Start-Sleep -Seconds 15
}
