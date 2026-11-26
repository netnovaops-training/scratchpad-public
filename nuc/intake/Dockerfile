FROM python:3.12-slim
WORKDIR /app
# T0925 part 2 (A14, 2026-11-19): requirements.txt is now the one list (it was copied in but unused, and
# the versions were typed twice); the app runs as uid 1000 (server on the NUC, who owns every mounted
# library) instead of root; temp files go on the /srv disk (A8) so filing is a rename.
# T0958 (2026-11-21): Tesseract reads the text in images before the sorter decides (classify.ocr).
# Debian packages, English + Korean data (Jason 11-21: Korean for Robin). OCR_LANGS picks what is used:
# eng (default, Jason's drop) or eng+kor (Robin's Garden Drop instance).
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng tesseract-ocr-kor \
    && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/
RUN pip install --no-cache-dir -r /app/requirements.txt
# portal.py is listed here too. It was missed the first time and the
# container crash-looped on ModuleNotFoundError -- the kind of error a
# wildcard would have prevented, so the wildcard is the fix.
COPY *.py /app/
ENV TMPDIR=/srv/unsorted/.tmp
USER 1000:1000
EXPOSE 8100
# Real check: /healthz writes a probe file in staging and checks free space (503 otherwise).
# compose.yml has the same test; this one covers `docker run` without compose.
HEALTHCHECK --interval=60s --timeout=10s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8100/healthz',timeout=5).status==200 else 1)"
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8100"]
