@echo off
REM What runs automatically, and what still needs you.
REM   autostart.cmd            audit only, changes nothing
REM   autostart.cmd -Install   also register the weekly certificate check
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0autostart.ps1" %*
