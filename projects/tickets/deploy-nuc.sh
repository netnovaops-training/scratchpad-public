#!/usr/bin/env bash
# Deploy (or update) the Tickets server on the NUC. Run ON the NUC from this folder:
#     bash deploy-nuc.sh          build the image and (re)start the container
#     bash deploy-nuc.sh undo     stop and remove the container (data volume is kept)
#
# Listens only on the NUC's loopback, 127.0.0.1:8050 - Caddy (host network) reaches it there,
# behind the household SSO; nothing new is opened on the LAN or the internet.
# Data: Docker volume tickets_data. Settings and tokens: ~/nuc-homelab/tickets.env (chmod 600).
set -euo pipefail
NAME=tickets
IMAGE=tickets/server:latest
ENV_FILE="$HOME/nuc-homelab/tickets.env"

if [ "${1:-}" = "undo" ]; then
    docker rm -f "$NAME" 2>/dev/null || true
    echo "Stopped and removed $NAME (volume tickets_data kept)."
    exit 0
fi

if [ ! -f "$ENV_FILE" ]; then
    install -m 600 tickets.env.example "$ENV_FILE"
    echo "Made $ENV_FILE from the example. Fill in the tokens later (nano $ENV_FILE), then run this again."
fi

docker build -t "$IMAGE" .
docker rm -f "$NAME" 2>/dev/null || true
docker run -d --name "$NAME" --restart unless-stopped \
    -p 127.0.0.1:8050:8050 \
    -v tickets_data:/data \
    --env-file "$ENV_FILE" \
    --label ai-stack.role="Tickets: ticket editor, saves to GitHub + Google Drive (behind SSO)" \
    "$IMAGE"

for _ in $(seq 1 20); do
    if curl -fsS http://127.0.0.1:8050/api/health >/dev/null 2>&1; then
        echo "tickets is up on 127.0.0.1:8050"
        exit 0
    fi
    sleep 1
done
echo "tickets did not answer on 127.0.0.1:8050 - check: docker logs $NAME" >&2
exit 1
