# Backend — FastAPI + SQLAlchemy (async)
#
# Development-oriented image: source is bind-mounted by docker-compose and
# uvicorn runs with --reload, so edits apply without a rebuild. Rebuild only
# when requirements.txt changes.
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

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
