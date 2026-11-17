@echo off
REM Start EVERYTHING - the AI stack, the phone line, and the websites.
cd /d "%~dp0"
REM Secrets come from Proton Pass (pass:// lines in .env) since 2026-11-12 - see ai-compose.cmd.
set PROTON_PASS_KEY_PROVIDER=fs
pass-cli run --env-file .env -- docker compose -f docker-compose.yml -f docker-compose.web.yml --profile everything --profile phone up -d
echo.
echo Websites : https://example.org
echo Phone    : your Twilio number
echo Web UI   : http://localhost:3000
echo CLI      : hermes.cmd
echo.
pause
