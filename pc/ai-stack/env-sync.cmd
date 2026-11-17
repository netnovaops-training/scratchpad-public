@echo off
REM Add new settings to .env without touching what you've already filled in.
REM
REM Run this after any update that adds a setting. It only APPENDS what's
REM missing - your keys, tokens and preferences are never modified, and a
REM timestamped backup is written first either way.
REM
REM   env-sync.cmd            add the missing settings
REM   env-sync.cmd -DryRun    show what it would add, write nothing
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0env-sync.ps1" %*
