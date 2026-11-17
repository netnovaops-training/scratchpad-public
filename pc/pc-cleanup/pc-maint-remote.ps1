<#
  pc-maint-remote.ps1 -- the ONLY thing the NUC's SSH key may run on this PC (forced command in
  administrators_authorized_keys, installed by install-pc-maint.ps1; 2026-11-04). The NUC asks for one
  of three words; anything else is refused. No shell, no other commands.
    start    start the scheduled task "PC maintenance (NUC-led)"
    status   print C:\Logs\pc-maint-status.json (stage + time) and the log's last lines
    health   is Docker up, how many containers, does the AI router answer
#>
$cmd = "$env:SSH_ORIGINAL_COMMAND".Trim().ToLower()
switch ($cmd) {
  'start'  { schtasks /run /tn "PC maintenance (NUC-led)" | Out-Null; if ($LASTEXITCODE -eq 0) { 'started' } else { "could not start (exit $LASTEXITCODE)" } }
  'status' { Get-Content 'C:\Logs\pc-maint-status.json' -ErrorAction SilentlyContinue; Get-Content 'C:\Logs\pc-maint.log' -Tail 25 -ErrorAction SilentlyContinue }
  'health' {
    $d = docker info --format '{{.ServerVersion}} {{.ContainersRunning}}' 2>$null
    $llm = try { (Invoke-WebRequest 'http://127.0.0.1:4000/health/liveliness' -UseBasicParsing -TimeoutSec 10).StatusCode } catch { 'no answer' }
    $up = [math]::Round(((Get-Date) - (Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalMinutes)
    "docker: $(if ($d) { "up, version/containers $d" } else { 'NOT up' }) | ai router: $llm | uptime: $up min"
  }
  default  { "refused: '$cmd' is not one of start, status, health" ; exit 1 }
}
