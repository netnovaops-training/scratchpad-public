@echo off
REM ============================================================================
REM  Double-click to make Windows run the services automatically.
REM  Asks for Administrator, because a boot-time task needs it.
REM ============================================================================
title Tech Help - install startup task
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo   Asking for Administrator...
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Install-Startup.ps1"
echo.
pause
