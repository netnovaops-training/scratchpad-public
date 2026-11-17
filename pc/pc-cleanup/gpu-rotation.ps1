<#
  gpu-rotation.ps1 -- the GPU takes turns (Jason, 2026-11-05: "the backfill and book review/summary can go in 4-hour
  rotations so they both can get access").
    4 hours: the plant pipeline's full run (7,019 plants; resumable plant by plant; its fetcher obeys robots.txt and
             each site's Crawl-delay, so only sites that allow bots are read)
    4 hours: the GPU is left free for everything else: Hermes, the library's AI summaries, the notes AI
  Then again, until the plant run finishes on its own; then it writes DONE and stops for good.
  Stopping the pipeline mid-window is safe: every plant's result is its own file (README: "stop any time ... it
  carries on where it stopped"). Started at sign-in by the scheduled task "GPU rotation" (setup-gpu-rotation.ps1).
  Log: C:\Logs\gpu-rotation.log    Pause it any time: create C:\Logs\gpu-rotation.pause (delete to resume).
#>
$log = 'C:\Logs\gpu-rotation.log'; $done = 'C:\Logs\gpu-rotation.DONE'; $pause = 'C:\Logs\gpu-rotation.pause'
New-Item -ItemType Directory -Force C:\Logs | Out-Null
function Say($m) { Add-Content $log ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) -Encoding utf8 }
$pipe = 'D:\Garden for all\_local-data\pipeline'
$in = 'D:\Garden for all\_local-data\in'
$hours = 4
Say 'rotation started'
while (-not (Test-Path $done)) {
  if (Test-Path $pause) { Say 'paused (gpu-rotation.pause exists)'; Start-Sleep 900; continue }
  # the plant pipeline needs the real Ollama with its models; if it isn't answering, give the slot back and retry later
  try { $tags = Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 10; $ok = ($tags.models.name -match 'phi4').Count -gt 0 }
  catch { $ok = $false }
  if (-not $ok) { Say 'Ollama with phi4 not reachable on 127.0.0.1:11434; skipping this plant window'; Start-Sleep ($hours * 3600); continue }

  Say "PLANT window: starting the full run for $hours h"
  $p = Start-Process -FilePath "$pipe\run-pipeline.cmd" -WorkingDirectory $pipe -WindowStyle Hidden -PassThru `
       -ArgumentList @('run', '--root', 'run-full', '--plants', "`"$in\future.json`"", '--current', "`"$in\current.json`"") `
       -RedirectStandardOutput 'C:\Logs\plant-pipeline.out.log' -RedirectStandardError 'C:\Logs\plant-pipeline.err.log'
  $finished = $p.WaitForExit($hours * 3600 * 1000)
  if ($finished) {
    Say "plant run exited on its own (code $($p.ExitCode))"
    # 2026-11-05: Start-Process with redirected output can report an EMPTY exit code, so the old check never saw
    # success and re-ran a finished pipeline every 4 h. The pipeline's own summary line is the real signal.
    $summary = Get-Content 'C:\Logs\plant-pipeline.out.log' -ErrorAction SilentlyContinue | Select-String 'PASS run finished'
    if ($p.ExitCode -eq 0 -or $summary) { Set-Content $done (Get-Date -Format s); Say 'plant run COMPLETE: rotation ends'; break }
  } else {
    # time's up: stop the whole process tree (python under cmd); resumable by design
    & taskkill.exe /PID $p.Id /T /F | Out-Null
    Say 'PLANT window over: pipeline stopped (resumes next window)'
  }
  Say "LIBRARY/AI window: GPU free for $hours h"
  Start-Sleep ($hours * 3600)
}
Say 'rotation finished'
