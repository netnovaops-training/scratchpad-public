@echo off
REM What voices you have, which surface uses which, and what they sound like.
REM
REM   voices.cmd                    show the setup
REM   voices.cmd -Preview jason     render a sample and play it
REM   voices.cmd -Preview dee -Say "Thank you for calling Tea Miss Dee."
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0voices.ps1" %*
