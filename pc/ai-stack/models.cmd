@echo off
REM Download or refresh the models listed in .env (PULL_MODELS).
cd /d "%~dp0"
docker compose --profile init up ollama-init
echo.
pause
