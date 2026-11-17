@echo off
REM Find out why n8n is losing its login and workflows.
REM Looks only - changes nothing.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0n8n-check.ps1"
