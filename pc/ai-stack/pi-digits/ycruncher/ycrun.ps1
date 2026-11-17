# ycrun.ps1 -- runs y-cruncher ELEVATED for /pi (Jason, 2026-11-08: "use both C and D and other drives ... go as many digits as possible").
#
# Why elevated: y-cruncher's swap mode (scratch data on disk instead of RAM) needs SeManageVolumePrivilege,
# which only an administrator has. install-task.ps1 (run once, as admin) registers the scheduled task
# "Pi y-cruncher" that runs THIS script with highest privileges; after that, a plain `schtasks /run`
# starts it with no prompt.
#
# What it runs: ONLY y-cruncher.exe from this folder, with the arguments on the first line of job.txt.
# Changing job.txt changes the run; nothing else can be launched through this task.
# Output: the console log goes to D:\pi-data\yc\log-<time>.txt and the state to D:\pi-data\yc\status.txt
# (running / finished <exit code>), which the /pi container reads.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$exe  = Join-Path $here 'y-cruncher v0.8.7.9547b\y-cruncher.exe'
$job  = (Get-Content (Join-Path $here 'job.txt') -TotalCount 1).Trim()
$out  = 'D:\pi-data\yc'
New-Item -ItemType Directory -Force $out | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$log = Join-Path $out "log-$stamp.txt"
"running $stamp $job" | Set-Content (Join-Path $out 'status.txt')
Set-Location (Split-Path -Parent $exe)
# skip-warnings: no ENTER prompts; pause:-2: never wait at the end; status:none keeps the log clean.
$p = Start-Process -FilePath $exe -ArgumentList "skip-warnings pause:-2 colors:0 status:none priority:-1 logfile:`"$log`" $job" `
     -WorkingDirectory (Split-Path -Parent $exe) -PassThru -Wait -WindowStyle Minimized
"finished $(Get-Date -Format 'yyyyMMdd-HHmmss') exit=$($p.ExitCode) $job" | Set-Content (Join-Path $out 'status.txt')
