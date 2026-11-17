@echo off
REM Pull the workflows out of n8n and back into n8n\workflows.
REM
REM The .json files in that folder are what you IMPORTED. They are not what is
REM RUNNING -- n8n keeps the live copy in its own database, and the two drift
REM apart the moment you edit a node in the browser. Run this after editing
REM anything and the folder becomes the truth again.
REM
REM   n8n-export.cmd              everything, active and switched off
REM   n8n-export.cmd -ActiveOnly  only the ones actually running
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0n8n-export.ps1" %*
