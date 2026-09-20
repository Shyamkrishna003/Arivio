# Backend — FastAPI + SQLAlchemy (async)
#
# One image for both development and deployment. Locally, docker-compose
# bind-mounts the source and overrides the command with a reloading uvicorn, so
# edits apply without a rebuild; rebuild only when requirements.txt changes.
# Deployed, nothing overrides the command and start.sh below runs instead —
# migrations, then uvicorn on the platform's $PORT, no reloader.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# curl is used by the compose healthcheck.
#
# tesseract-ocr is the local fallback for label reading: OCR_PROVIDER=auto
# resolves to it whenever no AI key is configured, so without the binary a
# deployment with no key would have a scan button that always fails. The
# vision-model path does not use it.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

# Copied first so the dependency layer is cached across source edits.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Re-applied here because a checkout on a filesystem without a permission bit
# loses it, and the failure is a confusing "exec format"-adjacent error at boot.
RUN chmod +x ./start.sh

# Documentation only — the deployed port comes from $PORT at runtime.
EXPOSE 8000

CMD ["./start.sh"]
