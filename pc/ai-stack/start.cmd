@echo off
REM Start the AI stack - models, router, Hermes, Telegram.
REM
REM NOT the websites. Those go up with websites.cmd, which stages the
REM certificate steps so a misconfiguration can't burn your five-per-week
REM Let's Encrypt attempts. start-all.cmd does both.
cd /d "%~dp0"
REM Secrets come from Proton Pass (pass:// lines in .env) since 2026-11-12 - see ai-compose.cmd.
set PROTON_PASS_KEY_PROVIDER=fs
pass-cli run --env-file .env -- docker compose up -d
echo.
echo   Hermes    : hermes.cmd  (or the Desktop shortcut)
echo   On a phone: message your Telegram bot
echo   Websites  : websites.cmd
echo   Check all : verify.cmd
echo.
pause
