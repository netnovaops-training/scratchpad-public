@echo off
REM ============================================================================
REM  Double-click to see what is running, then close.
REM  Does not start or stop anything.
REM ============================================================================
title Tech Help - status
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-TechHelp.ps1" -Status
echo.
pause
