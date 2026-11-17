# The tables editor. Python, because st.data_editor is a spreadsheet grid for
# one line of code and hand-writing that in Rust would be a fortnight.
#
# NOT on the public path. Caddy asks the Rust admin whether you are signed in
# before proxying anything here, so this image never faces the internet.
FROM python:3.15-slim-trixie@sha256:533b0b2f9b5d46fd097091cf826f27902f1d6c3bca714b6dc74551356aa97a51

# curl for the healthcheck. slim ships neither curl nor wget.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Requirements first, so a change to app.py does not re-download Streamlit.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY targets.py store.py app.py ./

# Runs as nobody. It writes exactly one folder, mounted in; nothing here needs
# root, and a container that faces even an authenticated human should not have
# it for free.
USER 65534:65534

EXPOSE 8501

# --server.baseUrlPath is what makes it work under /admin/tables. Without it
# Streamlit asks for its own JavaScript at /static/..., which lands on the
# landing page instead and the app renders as a blank screen.
#
# enableCORS=false and enableXsrfProtection=false: both checks compare against
# an origin this app cannot see correctly from behind a reverse proxy, and both
# are redundant here anyway - Caddy will not proxy a single byte to this
# container without a valid admin session, which is a stronger check than
# either.
CMD ["sh", "-c", "streamlit run app.py \
  --server.port=8501 \
  --server.address=0.0.0.0 \
  --server.baseUrlPath=${TABLES_BASE_PATH:-/admin/tables} \
  --server.headless=true \
  --server.enableCORS=false \
  --server.enableXsrfProtection=false \
  --server.maxUploadSize=${TABLES_MAX_UPLOAD_MB:-8} \
  --browser.gatherUsageStats=false \
  --theme.base=dark \
  --theme.backgroundColor=#050a12 \
  --theme.secondaryBackgroundColor=#0d1826 \
  --theme.primaryColor=#5aa2e8 \
  --theme.textColor=#e8eef7"]
