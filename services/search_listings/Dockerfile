# syntax=docker/dockerfile:1

# Two targets share one base:
#   `service` — the headless scraping API (what `docker compose up` runs).
#   `auth`    — a one-shot container for the interactive Handshake login,
#               which needs a *visible* browser (see the auth stage below).

FROM python:3.12-slim-bookworm AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# The WORKDIR deliberately mirrors the repo layout. app/config.py derives the
# shared SQLite path from `parents[3]`, so the service has to sit at
# <root>/services/<name>/ for that default to resolve to /srv/data/app.db —
# the directory compose mounts. Flatten this and the import blows up.
WORKDIR /srv/services/search_listings

COPY requirements.txt ./
# Chromium is installed via the pip-resolved Playwright rather than from a
# prebuilt Playwright image, so the browser build always matches the library
# version — requirements.txt pins only `playwright>=1.44`.
RUN pip install --no-cache-dir -r requirements.txt \
 && playwright install --with-deps chromium

COPY . .

# Chromium refuses to enable its sandbox when running as root, which would
# break every scrape. Running as a normal user is Playwright's recommended
# fix and avoids having to pass --no-sandbox from application code.
RUN useradd --create-home --uid 1000 appuser \
 && mkdir -p /srv/data .auth \
 && chown -R appuser:appuser /ms-playwright /srv


# --- Scraping API -----------------------------------------------------------
FROM base AS service

USER appuser
EXPOSE 8001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"]


# --- One-shot interactive auth capture --------------------------------------
# Handshake logins go through university SSO + MFA, so there is no headless
# credential flow to automate — a human has to drive a real browser once.
# Rather than requiring a host-side Python/Playwright install just for that,
# this stage runs the headed browser against a virtual display and streams it
# to the host over noVNC, so the login happens in an ordinary browser tab.
FROM base AS auth

USER root
RUN apt-get update \
 && apt-get install -y --no-install-recommends xvfb x11vnc novnc websockify \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix

USER appuser
EXPOSE 7900
CMD ["bash", "docker/auth-entrypoint.sh"]
