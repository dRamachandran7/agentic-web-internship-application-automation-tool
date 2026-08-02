# syntax=docker/dockerfile:1

# --- Fetch the Tectonic binary ----------------------------------------------
# Pulled from the release tarball rather than apt: Debian doesn't package
# Tectonic, and the static musl build runs as-is on both amd64 and arm64.
FROM debian:bookworm-slim AS tectonic

ARG TECTONIC_VERSION=0.17.0
# Set automatically by BuildKit — "amd64" or "arm64".
ARG TARGETARCH

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    case "$TARGETARCH" in \
      amd64) triple="x86_64-unknown-linux-musl" ;; \
      arm64) triple="aarch64-unknown-linux-musl" ;; \
      *) echo "unsupported architecture: $TARGETARCH" >&2; exit 1 ;; \
    esac; \
    url="https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-${triple}.tar.gz"; \
    curl -fsSL "$url" -o /tmp/tectonic.tar.gz; \
    tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin tectonic; \
    chmod +x /usr/local/bin/tectonic


# --- Service ----------------------------------------------------------------
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# Tectonic self-fetches LaTeX packages over HTTPS on first use, so it needs a
# trust store even though the binary itself is static.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY --from=tectonic /usr/local/bin/tectonic /usr/local/bin/tectonic

WORKDIR /srv/services/personalize_resume

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN useradd --create-home --uid 1000 appuser && chown -R appuser:appuser /srv

USER appuser

# Pre-warm Tectonic's package cache at build time. Without this, the very first
# personalize-resume request pays a multi-minute download and can trip the
# service's own compile timeout. The doc pulls the packages common resume
# templates load; a miss here only costs speed, so it must not fail the build.
RUN set -eux; \
    cd /tmp; \
    printf '%s\n' \
      '\documentclass[letterpaper,11pt]{article}' \
      '\usepackage{geometry}\usepackage{titlesec}\usepackage{enumitem}' \
      '\usepackage{hyperref}\usepackage{fancyhdr}\usepackage{tabularx}' \
      '\usepackage{xcolor}\usepackage{latexsym}\usepackage{marvosym}' \
      '\begin{document}Cache warm-up.\end{document}' > warmup.tex; \
    tectonic warmup.tex --outdir /tmp >/dev/null 2>&1 \
      || echo "NOTE: Tectonic cache pre-warm failed; the first compile at runtime will be slow."; \
    rm -f /tmp/warmup.*

EXPOSE 8003
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8003"]
