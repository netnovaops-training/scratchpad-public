@echo off
REM Which model answers which job - proved, not assumed.
REM
REM Reads litellm\config.yaml, checks each model is actually pulled, asks every
REM route a real question and times it, then shows what's resident in VRAM.
REM
REM   routes.cmd          the full check (asks each model a question)
REM   routes.cmd -Quick   config vs pulled only, no questions asked
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0routes.ps1" %*
