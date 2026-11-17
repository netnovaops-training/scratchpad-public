@echo off
REM The print server. Prints what you approve from Telegram, and tells you
REM when anyone in the house prints.
REM
REM   printserver.cmd -Install     run forever as a Scheduled Task  <-- do this
REM   printserver.cmd -EnableLog   turn on Windows' print log (ADMIN, once)
REM   printserver.cmd -Test        print a test page RIGHT NOW  <-- try this first
REM   printserver.cmd -Status      is it running? what has it printed?
REM   printserver.cmd -Once        one pass, verbose - good for testing
REM   printserver.cmd -Loop        keep running while this window is open
REM   printserver.cmd -Uninstall   remove the scheduled task
REM
REM Prints to your Windows DEFAULT printer unless you name one:
REM   printserver.cmd -Install -Printer "Brother HL-L2350DW series"
REM
REM -EnableLog needs Administrator, once ever. Without it Windows keeps no
REM record of completed print jobs, so the "someone printed" alerts stay empty.
cd /d "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0printserver.ps1" %*
pause
