@echo off
REM ai-compose.cmd: `docker compose` for this stack, with secrets filled in from Proton Pass.
REM
REM Since 2026-11-12 the secret lines in .env are pass://API keys/... references, not values.
REM pass-cli run reads .env, swaps each reference for the vault value, then runs compose with
REM them as environment variables (vendor docs: D:\secrets-tools\proton-pass-cli-docs\).
REM A plain `docker compose up` would give containers the literal pass:// text, so use this.
REM
REM   ai-compose.cmd up -d                    start/refresh what is defined
REM   ai-compose.cmd up -d --force-recreate X recreate one service
REM   ai-compose.cmd ps                       anything compose takes works
REM
REM Verified 2026-11-12: `config` through this path resolved every unrotated secret to the same
REM value as the old plaintext .env (hash comparison, compose_config_hash.py), 0 unexpected diffs.
cd /d "%~dp0"
set PROTON_PASS_KEY_PROVIDER=fs
pass-cli run --env-file .env -- docker compose -f docker-compose.yml -f docker-compose.web.yml %*
