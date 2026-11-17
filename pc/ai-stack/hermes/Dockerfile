# Pinned by digest so a rebuild never changes Python under us by surprise.
# 2026-11-11: moved from 3.15.1 (533b0b2f...) to 3.15.2, the expedited ssl
# security release. Tested first as an unpinned build (ai-stack/hermes:test):
# all 13 app modules import, tests/test_hermes_memory_wiring.py ALL PASS, and a
# live chat turn with a list_dir tool call answered. To update again: build
# unpinned, run those same checks, then paste the new digest here.
FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    TERM=xterm-256color \
    ENV=/root/.shinit

RUN apt-get update && apt-get install -y --no-install-recommends \
        bash git curl ca-certificates ripgrep jq less \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY hermes/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared /app/shared
COPY hermes/app /app/app

# `hermes` on PATH, plus `h` as a shortcut.
RUN printf '#!/bin/sh\nexec python -m app.cli "$@"\n' > /usr/local/bin/hermes \
    && chmod +x /usr/local/bin/hermes \
    && ln -sf /usr/local/bin/hermes /usr/local/bin/h

# Docker Desktop's Exec tab drops you into a bare shell with no clue what to do
# next. This prints the one thing you need. $ENV covers /bin/sh, .bashrc covers
# bash -- Docker Desktop has used both depending on version.
RUN printf '%s\n' \
    'echo ""' \
    'echo "  Hermes CLI agent"' \
    'echo "  ----------------"' \
    'echo "  type:  hermes     (or just: h)"' \
    'echo ""' \
    'echo "  workspace -> C:\\ai-stack\\workspace"' \
    'echo "  /help once you are inside"' \
    'echo ""' \
    > /root/.shinit \
    && cp /root/.shinit /root/.bashrc

WORKDIR /workspace

CMD ["sleep", "infinity"]
