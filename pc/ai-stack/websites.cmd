@echo off
REM Bring the websites up, staged so a misconfiguration can't burn your
REM five-per-week Let's Encrypt attempts.
REM
REM Certificates are READ, never re-issued, unless you ask with -Certs. A
REM rebuild is not a reason to spend one of five weekly Let's Encrypt attempts
REM on a certificate you already have.
REM
REM   websites.cmd            settings -> DNS -> build -> apps. Certs untouched.
REM   websites.cmd -Certs     rehearse on staging, then get real certificates
REM   websites.cmd -Real      skip the rehearsal, go straight to real certs
REM   websites.cmd -Stop      stop the websites, leave the AI stack running
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0websites.ps1" %*
