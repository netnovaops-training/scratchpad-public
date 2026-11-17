# run-admin-steps.ps1 -- the three admin steps in one elevated window (2026-11-04). Output of each
# goes to pc-cleanup\admin-steps.log so Claude can read it afterwards.
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$log = Join-Path $here 'admin-steps.log'
Start-Transcript -Path $log -Force | Out-Null
"=== 1. verify-uninstalls"; & (Join-Path $here 'verify-uninstalls.ps1')
"=== 2. list-software (all accounts)"; & (Join-Path $here 'list-software.ps1') | Select-Object -Last 60
"=== 3. restore points (keep today's, delete older than 3 days)"; & (Join-Path $here 'restore-points.ps1') -Apply
"=== done"
Stop-Transcript | Out-Null
Read-Host 'Done. Press Enter to close'
