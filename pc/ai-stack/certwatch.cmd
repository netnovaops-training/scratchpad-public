@echo off
REM Check the certificates all three sites are serving, and warn before one expires.
REM
REM   certwatch.cmd              check now
REM   certwatch.cmd -Quiet       only speak up if something is wrong
REM   certwatch.cmd -Install     run weekly, automatically (needs admin)
REM   certwatch.cmd -Uninstall   stop doing that
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0certwatch.ps1" %*
