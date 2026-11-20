@echo off
REM ============================================================================
REM  Double-click this to start everything the desktop has to be running.
REM
REM  A .cmd rather than the .ps1 directly, because Windows opens .ps1 files in
REM  Notepad when you double-click them -- it will not run them. This is the
REM  standard way to make a PowerShell script clickable, and it also sets the
REM  execution policy for just this one run so nothing has to be changed
REM  system-wide.
REM ============================================================================
title Tech Help - starting services
cd /d "%~dp0"

echo.
echo   Starting Ollama, Whisper and the ticket listener...
echo   Leave this window open. It restarts anything that stops.
echo.

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Start-TechHelp.ps1"

echo.
echo   The supervisor stopped.
echo.
pause
