@echo off
REM Stop everything. Your data, models and certificates are kept.
REM
REM Both compose files are named on purpose. `docker compose stop` alone only
REM knows about docker-compose.yml, so it leaves Caddy and the websites running
REM and you're left wondering why the sites are still up.
cd /d "%~dp0"
docker compose -f docker-compose.yml -f docker-compose.web.yml --profile web --profile phone --profile everything stop
echo.
echo   Everything stopped. Nothing was deleted.
echo   Start again with:  start.cmd   (or websites.cmd for just the sites)
echo.
pause
