# Local voice for the phone line. LuxTTS (~1GB VRAM) with Chatterbox as fallback.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime@sha256:77f17f843507062875ce8be2a6f76aa6aa3df7f9ef1e31d9d7432f4b0f563dee

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface PORT=8091

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libsndfile1 git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY voice/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# LuxTTS has no PyPI package -- it installs from source.
# Pinned to a commit would be safer, but the repo is young and moving; if this
# fails the server falls back to Chatterbox automatically and still answers.
RUN git clone --depth 1 https://github.com/ysharma3501/LuxTTS.git /opt/luxtts \
    && pip install --no-cache-dir -r /opt/luxtts/requirements.txt || true
ENV PYTHONPATH=/opt/luxtts:/app

COPY voice/server.py /app/server.py
EXPOSE 8091
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8091", "--workers", "1"]
