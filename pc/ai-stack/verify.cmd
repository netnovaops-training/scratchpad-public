@echo off
REM Check that everything actually works.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0verify.ps1"
echo.
pause
