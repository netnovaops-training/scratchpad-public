@echo off
REM Run this BEFORE setup.cmd.
REM
REM Ten seconds. Downloads nothing, changes nothing. Checks the four things
REM that are invisible until you're twenty minutes into a 14GB download:
REM disk space, WSL memory, port conflicts, and leftover Ollama models.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0preflight.ps1" %*
