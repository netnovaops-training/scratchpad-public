@echo off
REM Why can't a container see the 4070?
REM
REM Walks the whole chain - driver, WSL kernel, /dev/dxg, Docker's GPU
REM plumbing - and says which link is broken. Changes nothing.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0gpu-check.ps1" %*
