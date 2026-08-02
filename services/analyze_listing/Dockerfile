# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# The WORKDIR mirrors the repo layout: app/config.py derives the shared SQLite
# path from `parents[3]`, so this has to sit at <root>/services/<name>/ for the
# default to resolve to /srv/data/app.db — the same file search_listings writes.
WORKDIR /srv/services/analyze_listing

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 appuser \
 && mkdir -p /srv/data \
 && chown -R appuser:appuser /srv

USER appuser
EXPOSE 8002
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8002"]
