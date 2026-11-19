FROM python:3.15-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY telegram/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY shared /app/shared

# The SAME agent loop and tool belt Hermes runs in the terminal. Copied rather
# than reimplemented so the two can't drift: fix a tool once and both surfaces
# get it. Imported as `hermes_agent`; the relative imports inside it don't care
# what the folder is called.
COPY hermes/app /app/hermes_agent

COPY telegram/bot.py /app/bot.py

CMD ["python", "-u", "bot.py"]
