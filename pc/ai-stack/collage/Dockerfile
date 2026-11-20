# One small service: a list, some files, and a page.
FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
# 2026-11-20: installed from a hash-locked file (Pillow and nothing else it
# needs can change under us); pip refuses any file whose hash is not listed.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt
COPY app.py /app/app.py
# The watermark pass. Its own file because it is the only part of this
# service with any real algorithm in it, and app.py should stay readable.
COPY watermark.py /app/watermark.py
# The batch tool, for a folder that already exists -- a Google Photos
# export, say. Shipped in the image so it can be run against a mounted
# folder with `docker exec` rather than needing Python on the host.
COPY unwatermark.py /app/unwatermark.py
COPY static /app/static
WORKDIR /app
# 2026-11-20: runs as an unprivileged user (uid 10001) instead of root. The
# only place it writes is /data (COLLAGE_ROOT), so that is created here and
# handed to that user: a NEW named volume copies this ownership on first use.
# A volume or folder that already exists and is owned by root needs a one-off
# `chown -R 10001:10001` on it, or uploads fail with "permission denied".
RUN useradd --uid 10001 --user-group --no-create-home app \
 && mkdir -p /data && chown app:app /data
USER 10001:10001
CMD ["python", "-u", "app.py"]
