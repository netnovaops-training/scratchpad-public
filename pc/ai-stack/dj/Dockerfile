# One small service: a list, some files, and a page.
FROM python:3.15-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51
# pillow-heif is not optional in practice: the photographs come off an
# iPhone, an iPhone gives you HEIC, and Pillow alone cannot open one. Without
# it every upload from the phone this was built for fails with a message
# about a broken image, which is a lie.
RUN pip install --no-cache-dir Pillow==13.0.0 pillow-heif==1.8.1
COPY app.py /app/app.py
COPY static /app/static
WORKDIR /app
CMD ["python", "-u", "app.py"]
