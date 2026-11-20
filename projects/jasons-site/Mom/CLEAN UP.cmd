@echo off
REM ============================================================================
REM  Run this ONLY after you have used the site and it works.
REM  Shows what it would move first; nothing is deleted without confirmation.
REM ============================================================================
title Tech Help - tidy up
cd /d "%~dp0"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0DEPLOY.ps1" -Cleanup
echo.
echo   ============================================================
echo    Nothing has been changed yet. That was a preview.
echo   ============================================================
echo.
set /p GO="   Type YES to go ahead, or press Enter to stop: "
if /i "%GO%"=="YES" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0DEPLOY.ps1" -Cleanup -Yes
) else (
  echo.
  echo   Stopped. Nothing was moved.
)
echo.
pause
