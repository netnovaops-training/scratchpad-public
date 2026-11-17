@echo off
REM The metrics only Windows can see: C: free space, CPU temperature, Windows
REM uptime, real NIC throughput, Wi-Fi signal, internet latency. Writes
REM transcripts\host-metrics.json, which the stack reads.
REM
REM   hostmetrics.cmd              one sample, then show it
REM   hostmetrics.cmd -Loop        keep sampling while this window is open
REM   hostmetrics.cmd -Install     run it forever as a Scheduled Task  <-- do this
REM   hostmetrics.cmd -Status      is it actually running? when did it last write?
REM   hostmetrics.cmd -Uninstall   remove that task
REM
REM Network throughput is a DELTA between two samples, so a single run always
REM reports null for it. -Install is what makes those numbers appear.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0hostmetrics.ps1" %*
pause
