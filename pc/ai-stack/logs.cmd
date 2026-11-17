@echo off
REM Tail the logs of one container.
REM
REM   logs.cmd caddy         follow live (Ctrl+C to stop)
REM   logs.cmd caddy 60      last 60 lines, then exit
REM
REM Always passes BOTH compose files. The web containers (caddy, landing, casey,
REM mom, teabook) live in docker-compose.web.yml behind a profile, so a plain
REM `docker compose logs caddy` answers "no such service" even while the
REM container is sitting there running. That is confusing enough to be worth
REM a wrapper.
cd /d "%~dp0"
set COMPOSE=-f docker-compose.yml -f docker-compose.web.yml

if "%~1"=="" (
  echo Usage: logs.cmd ^<container^> [lines]
  echo   e.g. logs.cmd caddy        follow live
  echo        logs.cmd caddy 60     last 60 lines, then exit
  echo        logs.cmd metrics 40
  echo.
  docker compose %COMPOSE% ps
  pause
  exit /b
)

if "%~2"=="" (
  docker compose %COMPOSE% logs -f %1
) else (
  docker compose %COMPOSE% logs --tail %2 %1
  pause
)
