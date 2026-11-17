# fix-run.ps1 -- what typing `fix` does. Starts the "Docker Fix" scheduled task (install-fix.ps1
# makes it), which runs docker-fix.ps1 with full rights inside Jason's desktop session, then
# prints that log's new lines until the run says "fix: DONE" (or 9 minutes pass).
# Needs Jason signed in on the PC; right after a reboot, sign in first.
$log = 'C:\Jasons New Site\www\ai-stack\logs\docker-fix.log'
$seen = if (Test-Path $log) { @(Get-Content $log).Count } else { 0 }
schtasks /run /tn "Docker Fix" *> $null
if ($LASTEXITCODE -ne 0) { 'Could not start the "Docker Fix" task. Run install-fix.ps1 as administrator first.'; exit 1 }
'Started. Following the log (Ctrl+C stops watching, not the fix)...'
$until = (Get-Date).AddMinutes(9)
while ((Get-Date) -lt $until) {
  if (Test-Path $log) {
    $all = @(Get-Content $log)
    foreach ($line in ($all | Select-Object -Skip $seen)) { $line; if ($line -match 'fix: DONE') { exit 0 } }
    $seen = $all.Count
  }
  Start-Sleep 3
}
"Still running after 9 minutes. Log: $log"
