@echo off
REM Export every transcript to markdown.
REM Runs inside the hermes container, so you do not need Python on Windows.
cd /d "%~dp0"

docker compose up -d hermes >nul 2>&1

echo Sessions found:
echo.
docker compose exec -T hermes python /scripts/export_transcripts.py --list
echo.
echo Exporting to markdown...
docker compose exec -T hermes python /scripts/export_transcripts.py --format md
echo.
echo Open this folder:  %~dp0transcripts\exports
echo.
pause
