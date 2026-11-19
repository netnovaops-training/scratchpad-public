# /pi on SuperNova (2026-11-08; one-file storage 2026-11-09).
# gmpy2 = GMP/MPFR big-number maths, mpmath = the independent second method,
# cryptography = the Ed25519 signature, opentimestamps-client = Bitcoin timestamps.
FROM python:3.15-slim
RUN pip install --no-cache-dir gmpy2==2.3.2 mpmath==1.3.0 cryptography==50.0.2 opentimestamps-client==0.8.0
WORKDIR /app
COPY app.py snpi.py page.html verify_pi.py ./
ENV PI_DATA=/data PI_PORT=8020 PYTHONUNBUFFERED=1 GIT_PYTHON_REFRESH=quiet
EXPOSE 8020
CMD ["python", "app.py"]
