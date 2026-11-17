# Generic image for the three FastAPI sites (Casey, Mom, Tea Miss Dee booking).
#
# One Dockerfile, three containers. Each build passes APP_DIR so the same
# recipe produces a different image per app -- there's no meaningful
# difference between them beyond which folder they copy.
#
# TWO THINGS HERE ARE DELIBERATE AND LOOK ODD:
#
# 1. The fallback requirements are written INLINE rather than copied from
#    web/requirements-fallback.txt. The build context for these images is the
#    www folder, and www/.dockerignore excludes `ai-stack/` -- so that file is
#    not in the context at all and COPY cannot see it. Fixing the path alone
#    wouldn't help; the file simply isn't there to copy. Inlining removes the
#    dependency instead of working around it.
#
# 2. The app is copied BEFORE dependencies are installed, which costs the
#    usual "cache the deps layer" trick. The reason is that Booking has no
#    requirements.txt at all, and `COPY app/requirements*.txt` FAILS the build
#    outright when a wildcard matches nothing. Choosing which file to install
#    has to happen inside a RUN, where a shell can test for it -- and that
#    means the file has to be in the image first.
#
#    The cost is that changing app code reinstalls ~7 small packages. That is
#    perhaps thirty seconds. A build that cannot succeed for one of the three
#    apps costs considerably more.
FROM python:3.15-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# The shared set, for an app that doesn't pin its own. Matched to what Casey and
# Mom already use so all three run the same stack.
RUN printf '%s\n' \
    'fastapi==0.115.6' \
    'uvicorn[standard]==0.34.0' \
    'jinja2==3.1.5' \
    'python-multipart==0.0.20' \
    'itsdangerous==2.2.0' \
    'httpx==0.28.1' \
    'tzdata==2025.2' \
    > /tmp/fallback.txt

# Which site this image is for, relative to the build context (the www folder).
ARG APP_DIR
COPY ${APP_DIR}/ /app/

RUN if [ -s /app/requirements.txt ]; then \
        echo "==> installing ${APP_DIR}/requirements.txt" && \
        pip install --no-cache-dir -r /app/requirements.txt; \
    else \
        echo "==> ${APP_DIR} has no requirements.txt, using the shared set" && \
        pip install --no-cache-dir -r /tmp/fallback.txt; \
    fi

# SQLite lives here, on a named volume -- so rebuilding the image never
# touches the data.
RUN mkdir -p /data
VOLUME ["/data"]

ARG APP_PORT=8000
ENV APP_PORT=${APP_PORT}
EXPOSE ${APP_PORT}

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${APP_PORT} --proxy-headers --forwarded-allow-ips='*'"]
