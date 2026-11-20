@echo off
REM ============================================================================
REM  Double-click to check whether everything is working. Changes nothing.
REM ============================================================================
title Tech Help - verify
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0DEPLOY.ps1" -Verify
echo.
pause
