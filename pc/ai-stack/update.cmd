@echo off
REM Apply changes to a stack that's already running.
REM
REM Safe to run any time, as many times as you like. Doesn't reinstall, doesn't
REM re-download models, doesn't touch .env values you've set.
REM
REM   update.cmd                 the AI stack (this is the one you want)
REM   update.cmd -IncludePhone   also the phone line containers
REM   update.cmd -IncludeWeb     also Caddy and the websites
REM   update.cmd -KeepWebUI      leave Open WebUI running
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0update.ps1" %*
