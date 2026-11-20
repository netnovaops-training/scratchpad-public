# /pi on SuperNova (2026-11-08; one-file storage 2026-11-09).
# gmpy2 = GMP/MPFR big-number maths, mpmath = the independent second method,
# cryptography = the Ed25519 signature, opentimestamps-client = Bitcoin timestamps.
# 2026-11-20: base pinned by digest (tag kept in front for Dependabot), and the
# four libraries installed from a hash-locked file, with everything they pull in.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --require-hashes -r /tmp/requirements.txt \
 && rm /tmp/requirements.txt
WORKDIR /app
COPY app.py snpi.py page.html verify_pi.py ./
ENV PI_DATA=/data PI_PORT=8020 PYTHONUNBUFFERED=1 GIT_PYTHON_REFRESH=quiet
EXPOSE 8020
# 2026-11-20: runs as an unprivileged user (uid 10001) instead of root. It
# writes only to /data (PI_DATA: pi.snpi, keys, job.json). Created and chowned
# here so a NEW named volume starts writable; an existing root-owned volume or
# folder needs a one-off `chown -R 10001:10001` on it. Port 8020 is not
# privileged. HOME is /nonexistent, so GIT_PYTHON_REFRESH=quiet above matters.
RUN useradd --uid 10001 --user-group --no-create-home app \
 && mkdir -p /data && chown app:app /data
USER 10001:10001
CMD ["python", "app.py"]
