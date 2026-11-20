#!/usr/bin/env bash
set -e

# A bind-mounted host directory arrives owned by the host user. Fix it once so
# the unprivileged app user can write, then drop privileges.
if [ "$(id -u)" = "0" ]; then
  chown -R momhelp:momhelp /data 2>/dev/null || true
  exec setpriv --reuid=momhelp --regid=momhelp --init-groups "$0" "$@"
fi

# Fail fast and loudly rather than starting into a half-configured state.
: "${MOM_SECRET_KEY:?MOM_SECRET_KEY is not set. Generate one with: openssl rand -hex 32}"

if [ -z "${MOM_DESKTOP_IP:-}" ]; then
  echo "[warn] MOM_DESKTOP_IP is not set — the app cannot reach Ollama or Whisper." >&2
else
  echo "[info] backend: http://${MOM_DESKTOP_IP}:${MOM_OLLAMA_PORT:-11434} (ollama)"
  echo "[info] backend: http://${MOM_DESKTOP_IP}:${MOM_WHISPER_PORT:-9000} (whisper)"
fi

exec "$@"
