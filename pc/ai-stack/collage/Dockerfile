# One small service: a list, some files, and a page.
FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
RUN pip install --no-cache-dir Pillow==13.0.0
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
CMD ["python", "-u", "app.py"]
