@echo off
REM Open the Windows firewall for ports 80 and 443 - the web server, and
REM nothing else. Asks for administrator; that's required to change rules.
REM
REM   firewall.cmd            add or repair the rules, then check everything
REM   firewall.cmd -CheckOnly report only, change nothing, no admin needed
REM   firewall.cmd -Remove    take the rules back out
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0firewall.ps1" %*
