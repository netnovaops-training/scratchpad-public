# Pee-Wee Studio -- the workbench, served. Streamlit + Pillow, nothing else.
#
# The code is NOT copied in: compose mounts peewee-tools at /tools and the
# site at /site, so edits to app.py land on the next browser refresh and the
# Studio's writes go straight to the same files the desktop copy edits. The
# image is just the runtime.
FROM python:3.15-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51
RUN pip install --no-cache-dir streamlit==1.66.1 pillow && \
    useradd --system --create-home app
USER app
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "/tools/studio/app.py", \
     "--server.headless", "true", "--server.address", "0.0.0.0", \
     "--browser.gatherUsageStats", "false"]
