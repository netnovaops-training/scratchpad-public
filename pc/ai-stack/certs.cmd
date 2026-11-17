@echo off
REM What certificate each site is actually serving, right now.
REM
REM Asks the server over TLS instead of reading logs, so the answer is
REM never ambiguous. Works from inside your own house.
REM
REM   certs.cmd
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0certs.ps1" %*
