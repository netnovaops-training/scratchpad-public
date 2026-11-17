@echo off
REM Open the Hermes CLI agent. Double-click this, or use the Desktop shortcut.
REM
REM Starts Docker Desktop if it isn't running, starts the container if it's
REM stopped, waits for the router, then opens the agent. Same launcher the
REM Desktop shortcut uses, so both behave identically.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0hermes-launch.ps1" %*
