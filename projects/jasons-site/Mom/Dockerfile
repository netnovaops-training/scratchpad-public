# Tech Help — multi-architecture image.
#
# Builds for linux/arm64 (Raspberry Pi 5, today) and linux/amd64 (whatever you
# move to later) from the same file. The base image is official multi-arch, so
# there is nothing architecture-specific in here at all.
#
#   Pi now:        docker compose up -d --build
#   x64 later:     identical command. Copy the volume, done.
#   Both at once:  docker buildx build --platform linux/arm64,linux/amd64 .
#
# NOTE: the LLM is deliberately NOT in this container. Ollama and Whisper live
# on the desktop with the GPU; this image is just the app talking to them over
# the LAN. That split is what lets the ticket system run on a $80 Pi while
# inference happens on a 4070.
FROM python:3.15-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is used by the healthcheck only; keep the layer small.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY knowledge/ ./knowledge/
COPY manage.py test_mom.py test_growth.py ./

# Run unprivileged. The data volume is chowned at runtime by the entrypoint so
# a bind-mounted host directory still works.
RUN useradd --system --uid 10001 --home /data momhelp \
 && mkdir -p /data && chown -R momhelp:momhelp /data /app

COPY docker-entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

ENV MOM_DATA_DIR=/data \
    MOM_BASE_DIR=/app \
    MOM_KNOWLEDGE_DIR=/app/knowledge \
    MOM_BIND_PORT=8001

VOLUME ["/data"]
EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8001/healthz || exit 1

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
