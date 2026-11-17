@echo off
REM Nightly copy of the PC's work to the NAS (written 2026-10-31).
REM COPY ONLY: /E adds new and changed files, /XO never overwrites a newer file on the NAS,
REM and there is no /MIR or /PURGE, so nothing on the NAS is ever deleted by this script.
REM
REM Left OUT on purpose (secrets and rebuildable bulk): .env files, people.json, key files,
REM .git objects are KEPT (history is the point), node_modules / target / __pycache__ are not.
REM
REM NAS login: saved once by Jason in Windows Credential Manager (cmdkey), never in this file.
REM Set NAS_SHARE to the share chosen for PC backups before the first run.
REM Log: backup_to_nas.log next to this file. Robocopy exit codes 0-7 are success.

if "%NAS_SHARE%"=="" set NAS_SHARE=\\192.168.1.68\homelab\pc-backup
set LOG=%~dp0backup_to_nas.log
set XD=node_modules target __pycache__ .venv
set XF=.env .env.* *.pem *.key people.json *.pfx id_* *.bak-*

echo ==== %DATE% %TIME% ==== >> "%LOG%"
call :copy "C:\Jasons New Site\www" "%NAS_SHARE%\www"
call :copy "D:\Robin-garden-template" "%NAS_SHARE%\Robin-garden-template"
call :copy "D:\homelab-docs" "%NAS_SHARE%\homelab-docs"
call :copy "D:\paperless-feed" "%NAS_SHARE%\paperless-feed"
REM 2026-11-12: Homelab Docs moved into ai-stack\All-Documents\Projects\, which the "www" line
REM above already copies (All-Documents is git-ignored, not robocopy-excluded). A separate
REM Homelab Docs copy would now be a duplicate, so that line was removed.
exit /b 0

:copy
robocopy %1 %2 /E /XO /R:1 /W:2 /NP /NFL /NDL /XD %XD% /XF %XF% >> "%LOG%" 2>&1
echo   %~1 -> exit %ERRORLEVEL% >> "%LOG%"
exit /b 0
