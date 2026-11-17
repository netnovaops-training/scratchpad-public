<#
  setup-gpu-rotation.ps1 -- run ONCE in an ADMIN PowerShell (2026-11-05). Uses the steps already written in
  ai-stack\docker-compose.yml under "TO RESTORE" (2026-09-13).
   1. stop the EMPTY Windows Ollama (it has no models; it only blocks port 11434) and move its Startup shortcut to
      Downloads\_To-delete (nothing deleted)
   2. let the real Docker Ollama answer on 127.0.0.1:11434 (THIS PC ONLY: no LAN, no internet) and restart it
      (AI pauses for a few seconds); check phi4 / phi4-mini / gemma4:12b are reachable
   3. register the scheduled task "GPU rotation" (starts gpu-rotation.ps1 at sign-in; 4 h plants / 4 h free)
  Undo: re-comment the port line and `docker compose up -d --no-deps ollama`;
        Unregister-ScheduledTask "GPU rotation" -Confirm:$false; move Ollama.lnk back to the Startup folder.
#>
$ErrorActionPreference = 'Stop'
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole('Administrators')
if (-not $isAdmin) { throw 'Run this in an ADMIN PowerShell (right-click Start > Terminal (Admin)).' }

"1/3 stopping the empty Windows Ollama"
$lnk = "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup\Ollama.lnk"
if (Test-Path $lnk) { Move-Item $lnk 'C:\Users\USER\Downloads\_To-delete\startup-Ollama.lnk' -Force; "   startup shortcut moved to _To-delete" }
# "not found" just means it's already stopped; run through cmd so PowerShell 5.1 doesn't treat that message as fatal
cmd.exe /c 'taskkill /F /IM "ollama app.exe" /T >nul 2>&1'
cmd.exe /c 'taskkill /F /IM ollama.exe /T >nul 2>&1'
"   Windows Ollama stopped (or wasn't running)"
Start-Sleep 3

"2/3 letting the real Ollama answer on this PC only"
$compose = 'C:\Jasons New Site\www\ai-stack\docker-compose.yml'
Copy-Item $compose "$compose.bak-ollama-loopback" -Force
$t = [IO.File]::ReadAllText($compose)
$old = '    ports: []' + "`n" + '      # Loopback for everything on this PC, exactly as before.'
if ($t.Contains($old)) {
  $t = $t.Replace($old, '    ports:' + "`n" + '      - "127.0.0.1:11434:11434"   # restored 2026-11-05 for the plant pipeline (setup-gpu-rotation.ps1)' + "`n" + '      # Loopback for everything on this PC, exactly as before.')
  $t = $t.Replace('      # - "127.0.0.1:11434:11434"', '      # (the loopback line is now active above)')
  [IO.File]::WriteAllText($compose, $t, (New-Object Text.UTF8Encoding($false)))
  "   compose: loopback port enabled (backup .bak-ollama-loopback)"
} else { "   compose already changed or not as expected; check the ollama ports by hand" }
Push-Location 'C:\Jasons New Site\www\ai-stack'; docker compose up -d --no-deps ollama; Pop-Location
Start-Sleep 8
$tags = Invoke-RestMethod http://127.0.0.1:11434/api/tags -TimeoutSec 15
"   models reachable: " + (($tags.models.name | Where-Object { $_ -match 'phi4|gemma4' }) -join ', ')

"3/3 registering the GPU rotation task"
$a = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "C:\Jasons New Site\www\pc-cleanup\gpu-rotation.ps1"'
$tr = New-ScheduledTaskTrigger -AtLogOn -User 'SUPERNOVA\USER'; $tr.Delay = 'PT5M'
$pr = New-ScheduledTaskPrincipal -UserId 'SUPERNOVA\USER' -LogonType Interactive
$st = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'GPU rotation' -Action $a -Trigger $tr -Principal $pr -Settings $st -Force | Out-Null
Start-ScheduledTask -TaskName 'GPU rotation'
"   'GPU rotation' registered and started. Log: C:\Logs\gpu-rotation.log"
Read-Host 'Done. Press Enter to close'
