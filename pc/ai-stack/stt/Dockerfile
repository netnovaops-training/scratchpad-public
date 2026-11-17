# Streaming speech-to-text for the phone line. faster-whisper on the GPU.
FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime@sha256:77f17f843507062875ce8be2a6f76aa6aa3df7f9ef1e31d9d7432f4b0f563dee

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/root/.cache/huggingface PORT=8092

RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY stt/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY stt/server.py /app/server.py
EXPOSE 8092
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8092", "--workers", "1"]
