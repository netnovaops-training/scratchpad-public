@echo off
REM Collect everything needed to diagnose the web stack into diagnostics.txt
REM No secrets are included.
REM
REM   diag.cmd
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0diag.ps1" %*
