# One small service: a list, some files, and a page.
FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
# pillow-heif is not optional in practice: the photographs come off an
# iPhone, an iPhone gives you HEIC, and Pillow alone cannot open one. Without
# it every upload from the phone this was built for fails with a message
# about a broken image, which is a lie.
# 2026-11-20: installed from a hash-locked file (see requirements.txt); pip
# refuses any download whose hash is not listed there.
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt
COPY app.py /app/app.py
COPY static /app/static
WORKDIR /app
# 2026-11-20: runs as an unprivileged user (uid 10001) instead of root. It
# writes only to /data (DJ_ROOT), created here and handed to that user so a NEW
# named volume starts out writable. An existing root-owned volume or folder
# needs a one-off `chown -R 10001:10001` on it.
RUN useradd --uid 10001 --user-group --no-create-home app \
 && mkdir -p /data && chown app:app /data
USER 10001:10001
CMD ["python", "-u", "app.py"]
