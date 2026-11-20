@echo off
REM ============================================================================
REM  Double-click this. It does the whole deployment.
REM
REM  Asks for Administrator first, because the Wake-on-LAN settings and the
REM  boot-time startup task both need it. Everything else would work without,
REM  but you would then have to run half of it again.
REM ============================================================================
title Tech Help - deploying
cd /d "%~dp0"

net session >nul 2>&1
if %errorlevel% neq 0 (
  echo.
  echo   Asking for Administrator, so Wake-on-LAN and the startup task
  echo   can be set up in the same run...
  echo.
  powershell.exe -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0DEPLOY.ps1"
echo.
pause
