FROM python:3.15-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    MPLCONFIGDIR=/tmp/mpl

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY metrics/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# shared/history.py defines what each metric is called and how it's drawn.
# Shared with Hermes and the Telegram bot on purpose -- three copies of that
# catalogue would drift, and then the chart and the text would disagree.
COPY shared /app/shared

COPY metrics/app.py /app/app.py
COPY metrics/engine.html /app/engine.html

EXPOSE 8000

# One worker, on purpose. The collector loop lives inside the app process, so
# a second worker would mean two processes writing the same SQLite file every
# two minutes for no benefit -- there is nothing here to scale.
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
