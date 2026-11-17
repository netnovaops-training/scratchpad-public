@echo off
REM Update Windows apps with winget. NOT the Docker stack - that is update.cmd.
REM
REM   apps.cmd              update now, live counter (admin window)
REM   apps.cmd --logs       runs / success / fail / last ran / next run
REM   apps.cmd --watch      follow the midnight run live
REM   apps.cmd --install    (admin) put "apps" on PATH and point the scheduled task here
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0apps.ps1" %*
