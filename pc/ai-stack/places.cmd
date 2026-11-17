@echo off
REM Add, remove and list places without touching the curated file.
REM
REM   places.cmd list
REM   places.cmd list --category parks
REM   places.cmd takeout "C:\Users\USER\Downloads\Saved Places.csv" --category parks
REM   places.cmd url "https://maps.app.goo.gl/xxxx" --category restaurants
REM   places.cmd search "grocery store in North Las Vegas NV" --category grocery
REM   places.cmd remove "Some Place"
REM
REM Everything is written to config\places.local.yaml. config\places.yaml is
REM never rewritten - that one you edit in Notepad.
cd /d "%~dp0"
docker compose exec -T -w /scripts hermes python places_import.py %*
if errorlevel 1 (
  echo.
  echo   If that failed with "no such container", start the stack first:
  echo       start.cmd
  echo.
)
pause
