@echo off
REM Nightly household backup to the encrypted external drive on the NUC.
REM
REM A .cmd wrapper because Task Scheduler cannot run a bash script directly
REM on Windows, and because putting the Git Bash path in the task's own
REM arguments field hides it somewhere nobody will look for it when it
REM breaks. The whole command lives here, in the repo, next to the script.
REM
REM The script refuses to write anything if the vault is locked, so a
REM scheduled run on a night the drive is not unlocked reports the refusal
REM rather than quietly filling the NUC's unencrypted disk.
REM 2026-10-31: Jason chose an unencrypted drive; the mount check still runs.
set ALLOW_UNENCRYPTED=1
"C:\Program Files\Git\bin\bash.exe" -lc "cd '/c/Jasons New Site/www/ai-stack' && ./files-backup.sh" >> "%~dp0backup.log" 2>&1
